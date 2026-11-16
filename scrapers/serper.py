"""Additive FR/CA discovery and the unchanged Google plan on a separate provider.

No cookies, date cutoffs, automatic purchases or existing-provider quota changes.
Every attempted POST keeps a lifetime credit reservation, even after a timeout.
"""
import calendar
from datetime import datetime, timezone
import json
import logging
import re
import time
from urllib.parse import urlsplit

import httpx

from config import Config
from database import canonical_url
from http_limits import retry_after_seconds
from keywords import MODEL_CODES
from offer_verifier import public_url, index_card_negative, remember_indexed_closed
from provider_state import store, read, write, claim_window
from scrapers.base import BaseScraper
from scrapers.google import GoogleScraper, GLOBAL_QUERIES
from source_policy import is_disabled_source

log = logging.getLogger(__name__)
API = 'https://google.serper.dev/search'


def regional_queries():
    signals = '(Zeta OR Zetta OR Strados OR "Jazz Fusion" OR "Jean-Luc Ponty" OR ' + ' OR '.join(MODEL_CODES) + ')'
    canada = '(site:kijiji.ca OR site:ebay.ca OR site:montreal.craigslist.org OR site:toronto.craigslist.org OR site:vancouver.craigslist.org)'
    return [
        (f'site:leboncoin.fr {signals}', {'gl':'fr', 'hl':'fr'}),
        (f'(site:zikinf.com OR site:audiofanzine.com) {signals}', {'gl':'fr', 'hl':'fr'}),
        (f'{canada} {signals}', {'gl':'ca', 'hl':'fr'}),
        (f'{canada} {signals}', {'gl':'ca', 'hl':'en'}),
    ]


def google_unavailable():
    """Use the last actually attempted source observation, never a skipped zero."""
    if not Config.GOOGLE_API_KEY or not Config.GOOGLE_CSE_ID:
        return True
    with store() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scraper_stats'").fetchone():
            return False
        row = conn.execute("SELECT error FROM scraper_stats WHERE scraper='Google Search' AND raw_count>=0 ORDER BY id DESC LIMIT 1").fetchone()
    return bool(row and 'API_PROJECT_NOT_ELIGIBLE' in (row[0] or ''))


def reserve_credit(*, auxiliary=False, now=None, planned_per_cycle=52, report=None):
    """Protect remaining scheduled slots this month and a separate auxiliary lane.

    The hard lifetime cap is an operator allowance, not an account balance. Unlike
    a monthly allowance, a free trial cannot silently renew on the first of a month.
    """
    now = time.time() if now is None else now
    stamp = datetime.fromtimestamp(now, timezone.utc)
    day = stamp.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    daily = planned_per_cycle * Config.RUNS_PER_DAY
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        total = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper'").fetchone()[0]
        core = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper:core' AND created>=?",(day,)).fetchone()[0]
        aux = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper:aux' AND created>=?",(day,)).fetchone()[0]
        protected = (calendar.monthrange(stamp.year,stamp.month)[1]-stamp.day)*daily + max(0,daily-core)
        reason = ('lifetime' if total+1>Config.SERPER_TOTAL_CREDITS else
                  'auxiliary_daily' if auxiliary and aux>=8 else
                  'scheduled_capacity' if auxiliary and total+1+protected>Config.SERPER_TOTAL_CREDITS else
                  'core_daily' if not auxiliary and core>=daily else '')
        if reason:
            if report is not None:
                report.update(reason=reason,lifetime_reserved=total,lifetime_allowance=Config.SERPER_TOTAL_CREDITS,
                              auxiliary_used_today=aux,core_used_today=core,scheduled_protected=protected)
            conn.rollback()
            return False
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                         [('serper',now,1),('serper:aux' if auxiliary else 'serper:core',now,1)])
        conn.commit()
    return True


def payload(query, extra):
    result = {'q':query, 'num':10}
    for field in ('gl','hl'):
        if extra.get(field):
            result[field] = extra[field]
    if extra.get('dateRestrict') == 'w2':
        result['tbs'] = 'qdr:w2'
    return result


def parse_response(data, query):
    """A response must echo the actual query and explicitly contain organic rows."""
    if (not isinstance(data,dict) or data.get('error') or data.get('message')
            or not isinstance(data.get('searchParameters'),dict)
            or data['searchParameters'].get('q') != query
            or not isinstance(data.get('organic'),list)):
        raise ValueError('invalid or mismatched search response')
    rows = []
    for raw in data['organic']:
        if not isinstance(raw,dict):
            continue
        url, title = raw.get('link'), raw.get('title')
        if not isinstance(url,str) or not public_url(url) or is_disabled_source(url) or not isinstance(title,str) or not title.strip():
            continue
        snippet = raw.get('snippet') if isinstance(raw.get('snippet'),str) else ''
        rows.append({'url':url,'title':title,'description':snippet, **index_card_negative(url,snippet)})
    return rows, len(data['organic'])


class SerperScraper(BaseScraper):
    name = 'France / Canada Search'
    search_engine = True

    def is_configured(self):
        return bool(Config.SERPER_API_KEY)

    async def search(self):
        if not hasattr(self,'content_failures'):
            self.reset_health()
        if not self.is_configured():
            self.skipped = True
            return []
        auxiliary = getattr(self,'auxiliary_work',False) or getattr(self,'manual_fresh',False)
        guard = Config.SERPER_GUARD_HOURS*3600
        key = 'serper:aux:last_run' if auxiliary else 'serper:last_run'
        if (auxiliary and time.time()-float(read('serper:last_run',0) or 0)<guard
                or not claim_window(key,guard)):
            self.skipped = True
            return []
        if float(read('serper:retry_after',0) or 0)>time.time():
            self.content_failures.append('Serper provider cooldown; candidates and cursors retained')
            return []
        cursor = int(read('serper:google_cursor',0))
        regional_cursor=int(read('serper:regional_cursor',0))
        recover = google_unavailable()
        regional=regional_queries()
        regional=regional[regional_cursor%4:]+regional[:regional_cursor%4]
        queries = [('regional',q,extra) for q,extra in regional]
        if recover:
            backup,_ = GoogleScraper('', '').plan_queries(cursor)
            queries += [('google_backup',q,extra) for q,extra in backup]
        self.coverage_stats = {'provider':'serper','regional_planned':4,'regional_attempted':0,
                               'google_backup_planned':len(queries)-4,'google_backup_attempted':0,
                               'regional_definitions':[{'query':q,**extra} for q,extra in regional_queries()],
                               'google_backup_cursor_start':cursor,
                               'valid_responses':0,'scope_mismatches':0,'raw_rows':0,'lifetime_allowance':Config.SERPER_TOTAL_CREDITS,
                               'query_results':[]}
        results, seen = [], {}
        deadline=time.monotonic()+180
        transport_failures=0
        async with self.make_client(timeout=30,follow_redirects=False) as client:
            for kind,query,extra in queries:
                if time.monotonic()>=deadline:
                    self.content_failures.append('Serper deadline reached; completed results and unattempted cursor retained')
                    break
                refusal={}
                if not reserve_credit(auxiliary=auxiliary,planned_per_cycle=4+Config.GOOGLE_QUERIES_PER_RUN,report=refusal):
                    self.coverage_stats['reservation_refusal']=refusal
                    reason=refusal.get('reason')
                    self.content_failures.append({
                        'auxiliary_daily':'Serper: limita turelor manuale 8/zi atinsă; creditul gratuit nu este declarat epuizat, turele programate sunt protejate',
                        'scheduled_capacity':'Serper: credit rezervat pentru turele programate; căutarea auxiliară este amânată',
                        'lifetime':'Serper: alocația TOTALĂ de credite este rezervată; necesită verificarea soldului',
                        'core_daily':'Serper: limita zilnică a turelor programate este rezervată',
                    }.get(reason,'Serper lifetime/daily limit; scheduled capacity protected'))
                    break
                # A timeout may have been billed. Advance only the attempted backup
                # slice; every unattempted matrix slot stays on the durable cursor.
                self.coverage_stats[kind+'_attempted'] += 1
                if kind=='regional':
                    write('serper:regional_cursor',regional_cursor+self.coverage_stats['regional_attempted'])
                else:
                    spent = self.coverage_stats['google_backup_attempted']
                    consumed = max(0,spent-min(len(GLOBAL_QUERIES),len(queries)-4))
                    write('serper:google_cursor',cursor+consumed)
                try:
                    observation={'kind':kind,'query':query,**{k:extra[k] for k in ('gl','hl') if k in extra}}
                    self.coverage_stats['query_results'].append(observation)
                    self.record_discovery_query(f'{kind} gl={extra.get("gl","default")} hl={extra.get("hl","default")}: {query}')
                    response = await client.post(API,headers={'X-API-KEY':Config.SERPER_API_KEY},json=payload(query,extra),timeout=min(30,max(.1,deadline-time.monotonic())))
                    observation['http']=response.status_code
                    if response.status_code!=200:
                        self.content_failures.append(f'Serper HTTP {response.status_code}; reservation retained')
                        if response.status_code in (401,402,403,429):
                            delay = retry_after_seconds(response.headers.get('retry-after','')) if response.status_code==429 else 86400
                            write('serper:retry_after',time.time()+delay)
                            break
                        continue
                    rows,raw = parse_response(response.json(),query)
                    observation.update(organic_rows=raw,retained_url_rows=len(rows),schema_valid=True)
                    transport_failures=0
                    self.fetched += raw
                    self.coverage_stats['raw_rows'] += raw
                    self.coverage_stats['valid_responses'] += 1
                    hosts=re.findall(r'\bsite:([^ /()]+)',query)
                    if hosts and rows and not any(any((urlsplit(row['url']).hostname or '').lower()==host
                            or (urlsplit(row['url']).hostname or '').lower().endswith('.'+host) for host in hosts) for row in rows):
                        self.coverage_stats['scope_mismatches']+=1
                        self.content_failures.append('Serper requested site scope not reflected in results; candidates retained')
                    for row in rows:
                        remember_indexed_closed(row)
                        identity = canonical_url(row['url'])
                        if identity in seen:
                            if row.get('_indexed_availability_negative'):
                                seen[identity].update({k:v for k,v in row.items() if k.startswith('_indexed_')})
                            continue
                        item = {**row,'id':self._make_id('serper',identity),'source':'search',
                                'platform':urlsplit(row['url']).hostname or self.name,
                                'price':'See listing','location':'Unknown','_native_search_query':query}
                        seen[identity] = item
                        results.append(item)
                except (httpx.HTTPError,ValueError,TypeError) as exc:
                    observation['error_type']=type(exc).__name__
                    self.content_failures.append('Serper '+type(exc).__name__+'; reservation retained')
                    log.warning('Serper request failed (%s); no automatic POST retry',type(exc).__name__)
                    transport_failures+=1
                    if not isinstance(exc,httpx.HTTPError) or transport_failures>=2:
                        break  # No paid query storm after an incompatible response/provider outage.
        log.info('Serper coverage: %s',json.dumps(self.coverage_stats,sort_keys=True))
        return results
