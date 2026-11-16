"""Extra public-index FB discovery; original Google/Serper/US queries unchanged."""
import calendar
import json
import logging
import time
from datetime import datetime, timezone
from config import Config
from provider_state import store, read, write, claim_window
from facebook_regions import regional_plan
from facebook_discovery import item_id
from scrapers.base import BaseScraper
from scrapers.serper import API, payload, parse_response
from http_limits import retry_after_seconds
from offer_verifier import remember_indexed_closed

log = logging.getLogger(__name__)


def reserve_region(now=None, *, auxiliary=False):
    """Two extra slots/core; lifetime pool and complete original plan protected."""
    now = time.time() if now is None else now
    stamp = datetime.fromtimestamp(now,timezone.utc)
    day = stamp.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    daily_original = (4+Config.GOOGLE_QUERIES_PER_RUN)*Config.RUNS_PER_DAY
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        total = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper'").fetchone()[0]
        core = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper:core' AND created>=?",(day,)).fetchone()[0]
        regional = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper:facebook' AND created>=?",(day,)).fetchone()[0]
        extra = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider='serper:facebook:aux' AND created>=?",(day,)).fetchone()[0]
        protected = (calendar.monthrange(stamp.year,stamp.month)[1]-stamp.day)*daily_original + max(0,daily_original-core)
        if auxiliary:
            protected += (calendar.monthrange(stamp.year,stamp.month)[1]-stamp.day)*2*Config.RUNS_PER_DAY + max(0,2*Config.RUNS_PER_DAY-regional)
        if ((extra>=2 if auxiliary else regional>=2*Config.RUNS_PER_DAY)
                or total+1+protected>Config.SERPER_TOTAL_CREDITS):
            conn.rollback()
            return False
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                         [('serper',now,1),('serper:facebook:aux' if auxiliary else 'serper:facebook',now,1)])
        conn.commit()
        return True


class FacebookInternationalScraper(BaseScraper):
    name = 'Facebook regional index'
    search_engine = True

    def is_configured(self):
        return bool(Config.SERPER_API_KEY)

    async def search(self):
        if not hasattr(self,'failures'):
            self.reset_health()
        self.coverage_stats = {'kind':'public_index_not_fresh_marketplace','planned':2,'attempts':[],
                               'country_priority':['US','CA','GB','FR','DE','ES']}
        if not self.is_configured() or getattr(self,'manual_fresh',False):
            self.skipped = True
            return []
        auxiliary = getattr(self,'auxiliary_work',False)
        key = 'facebook:international:index:aux:last_run' if auxiliary else 'facebook:international:index:last_run'
        if not claim_window(key,10*3600):
            self.skipped = True
            return []
        if float(read('serper:retry_after',0) or 0)>time.time():
            self.content_failures.append('regional index cooldown; original queries/cursor preserved')
            return []
        cursor = int(read('facebook:international:index:cursor',0))
        results = []
        async with self.make_client(timeout=25,follow_redirects=False) as client:
            for slot in regional_plan(cursor):
                if not reserve_region(auxiliary=auxiliary):
                    self.content_failures.append('additional regional credits protected for original Google/FR/CA queries')
                    self.skipped = not self.coverage_stats['attempts']
                    break
                observation = {k:slot[k] for k in ('country','city','language','query')}
                self.coverage_stats['attempts'].append(observation)
                write('facebook:international:index:cursor',cursor+len(self.coverage_stats['attempts']))
                self.record_discovery_query(slot['query'])
                try:
                    response = await client.post(API,headers={'X-API-KEY':Config.SERPER_API_KEY},
                        json=payload(slot['query'],{'gl':slot['country'].lower(),'hl':slot['language']}))
                    observation['http'] = response.status_code
                    if response.status_code!=200:
                        self.content_failures.append('regional index HTTP '+str(response.status_code))
                        if response.status_code in (401,402,403,429):
                            delay = retry_after_seconds(response.headers.get('Retry-After')) if response.status_code==429 else 86400
                            write('serper:retry_after',time.time()+delay)
                        break
                    rows,raw = parse_response(response.json(),slot['query'])
                    self.fetched += raw
                    eligible = [row for row in rows if item_id(row['url'])]
                    observation.update(schema_valid=True,organic_rows=raw,marketplace_items=len(eligible))
                    for row in eligible:
                        remember_indexed_closed(row)
                        results.append({**row,'id':self._make_id('facebook_region',row['url']),
                            'platform':'Facebook Marketplace','source':'search','price':'See listing',
                            'location':'Unknown','_requested_country':slot['country'],'_requested_city':slot['city']})
                except Exception as exc:
                    observation['error'] = type(exc).__name__
                    self.content_failures.append('regional index '+type(exc).__name__+'; billed reservation retained')
                    break  # No automatic retry of possibly charged POST.
        log.info('Facebook regional index coverage: %s',json.dumps(self.coverage_stats,ensure_ascii=False))
        return results
