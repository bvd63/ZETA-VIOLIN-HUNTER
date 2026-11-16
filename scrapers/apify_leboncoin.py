"""Optional native French discovery, sharing the existing protected Apify ledger.

Never hands the Actor Telegram tokens or enables its own monitor/notifications.
The existing Facebook budget/account reserve must permit every new launch.
"""
import json
import logging
import time

from apify_auth import check_access, REASON
from apify_costs import settle
from config import Config
from leboncoin_api import ACTOR, actor_input, normalize, remember, remember_status, timestamp
from provider_state import claim_window, read
from scrapers.apify_facebook import ApifyFacebookScraper, IDENTIFIER, TERMINAL, store

log=logging.getLogger(__name__)
CAP=.03


class ApifyLeboncoinScraper(ApifyFacebookScraper):
    name='Leboncoin (native Apify)'

    def is_configured(self):
        return bool(Config.APIFY_ENABLED and Config.APIFY_TOKEN and Config.APIFY_LEBONCOIN_ENABLED)

    async def read_native_run(self,client,run_id):
        deadline=time.monotonic()+210
        while time.monotonic()<deadline:
            run=(await self._request(client,'GET',f'/actor-runs/{run_id}',params={'waitForFinish':20}))['data']
            if run.get('id')!=run_id or run.get('actId')!=ACTOR:
                raise ValueError('Leboncoin run identity mismatch')
            if run.get('status') in TERMINAL:
                break
        else:
            raise TimeoutError('Leboncoin run pending; persisted ID retained')
        settle(run_id,run)
        self._run_finished=timestamp(run.get('finishedAt'))
        self._run_started=timestamp(run.get('startedAt'))
        if run.get('status')!='SUCCEEDED':
            self.content_failures.append('Leboncoin Actor '+str(run.get('status'))+'; partial coverage')
        dataset=run.get('defaultDatasetId','')
        if not isinstance(dataset,str) or not IDENTIFIER.fullmatch(dataset):
            raise ValueError('Leboncoin dataset missing')
        rows=await self._request(client,'GET',f'/datasets/{dataset}/items',params={'format':'json','clean':'true','limit':100})
        if not isinstance(rows,list):
            raise ValueError('Leboncoin dataset invalid')
        if len(rows)>=100:
            self.content_failures.append('Leboncoin dataset limit; partial coverage')
        return rows

    def consume(self,rows,requested, *, observed_at, fresh=False):
        results=[]
        observed=set()
        self.coverage_stats={'provider':'apify','requested_keywords':requested,'observed_searches':0,'complete_searches':0,'ad_rows':0,'partial_status_rows':0,'new_run_observations':fresh}
        for row in rows:
            if not isinstance(row,dict):
                continue
            if row.get('type')=='search':
                keyword=row.get('searchKeyword')
                if keyword not in requested or keyword in observed:
                    continue
                observed.add(keyword)
                if str(row.get('searchPlace') or '').strip().lower() not in ('','france','france entière','toute la france'):
                    self.content_failures.append('Leboncoin returned a local scope for a whole-France request')
                if fresh:
                    self.record_discovery_query(f'native keyword={keyword!r} country=FR pages={row.get("pagesRead")} complete={row.get("complete")}')
                else:
                    log.info('Leboncoin resumed/cached search observation: %s',keyword)
                self.coverage_stats['observed_searches']+=1
                self.coverage_stats['complete_searches']+=int(row.get('complete') is True)
                if row.get('complete') is not True or row.get('detailsFailed'):
                    self.content_failures.append('Leboncoin incomplete search/details; retained for next cycle')
            elif row.get('type')=='ad':
                self.fetched+=1
                item=normalize(row)
                if not item:
                    self.content_failures.append('Leboncoin row identity/schema mismatch')
                    continue
                row_time=timestamp(row.get('scrapedAt'))
                remember(item,checked=min(observed_at,row_time) if row_time else observed_at)
                self.coverage_stats['ad_rows']+=1
                item['id']=self._make_id('leboncoin',item['_leboncoin_id'])
                item['relevance_score']=self._relevance_score(item['title'],item['description'])
                results.append(item)
            elif row.get('type') in ('status','summary'):
                remember_status(row,checked=observed_at)
                if row.get('type')=='status' and row.get('status') in ('failed','search_not_applied','limit_reached','source_unavailable','invalid','ambiguous','unknown_place'):
                    self.coverage_stats['partial_status_rows']+=1
                    self.content_failures.append('Leboncoin '+row['status'])
        if observed!=set(requested):
            self.content_failures.append('Leboncoin missing per-keyword coverage; no complete-inventory claim')
        return results

    async def search(self):
        if not hasattr(self,'failures'):
            self.reset_health()
        try:
            return await self._search_native()
        except Exception as exc:
            label=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
            self.content_failures.append('Leboncoin deferred: '+label)
            log.warning('Leboncoin collection deferred (%s); reservation/run ID retained',label)
            return []

    async def _search_native(self):
        started=time.time()
        if not hasattr(self,'failures'):
            self.reset_health()
        if not self.is_configured():
            self.skipped=True
            return []
        input_data=actor_input(pilot=getattr(self,'pilot',False))
        auxiliary=getattr(self,'auxiliary_work',False) or getattr(self,'manual_fresh',False)
        with store() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS apify_query_inputs(reservation TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            conn.commit()
            pending=conn.execute("SELECT id,run_id FROM apify_jobs WHERE phase='leboncoin' AND done=0 ORDER BY created LIMIT 1").fetchone()
        async with self.make_client(timeout=30,follow_redirects=False,headers={'Authorization':'Bearer '+Config.APIFY_TOKEN}) as client:
            if not await check_access(client):
                self.content_failures.append(REASON)
                return []
            if pending:
                reservation,run_id=pending
                if not run_id:
                    self.content_failures.append('Leboncoin previous POST outcome uncertain; no duplicate launch')
                    return []
                with store() as conn:
                    saved=conn.execute('SELECT payload FROM apify_query_inputs WHERE reservation=?',(reservation,)).fetchone()
                if not saved:
                    self.content_failures.append('Leboncoin pending input unavailable; no invented query coverage')
                    return []
                input_data=json.loads(saved[0])
            else:
                guard=10*3600
                key='leboncoin:aux:last_run' if auxiliary else 'leboncoin:last_run'
                if (auxiliary and time.time()-float(read('leboncoin:last_run',0) or 0)<guard or not claim_window(key,guard)):
                    self.skipped=True
                    return []
                # Even a scheduled new collector must protect the old Facebook
                # plan; never use a new phase to bypass the account guard.
                previous=getattr(self,'auxiliary_work',False)
                self.auxiliary_work=True
                try:
                    reservation=self._reserve('leboncoin',CAP)
                finally:
                    self.auxiliary_work=previous
                if not reservation:
                    return []
                with store() as conn:
                    conn.execute('INSERT INTO apify_query_inputs VALUES(?,?)',(reservation,json.dumps(input_data,sort_keys=True)))
                    conn.commit()
                log.info('Leboncoin Actor requested input: keywords=%s country=FR category=instruments_de_musique; cap=$%.3f',input_data['keywords'],CAP)
                try:
                    run=(await self._request(client,'POST',f'/acts/{ACTOR}/runs',params={'maxTotalChargeUsd':CAP,'timeout':180,'memory':256,'restartOnError':'false'},json=input_data))['data']
                    run_id=run.get('id')
                    if not isinstance(run_id,str) or not IDENTIFIER.fullmatch(run_id):
                        raise ValueError('Leboncoin launch ID missing; reservation retained')
                    with store() as conn:
                        conn.execute('UPDATE apify_jobs SET run_id=? WHERE id=?',(run_id,reservation));conn.commit()
                except RuntimeError as exc:
                    if str(exc)=='Apify HTTP 401':
                        with store() as conn:
                            conn.execute('UPDATE apify_jobs SET done=1 WHERE id=?',(reservation,));conn.commit()
                    raise
            rows=await self.read_native_run(client,run_id)
            result=self.consume(rows,input_data['keywords'],observed_at=self._run_finished,
                                fresh=not pending and self._run_started>=started-5)
            with store() as conn:
                conn.execute('UPDATE apify_jobs SET done=1 WHERE id=?',(reservation,));conn.commit()
            log.info('Leboncoin Actor run %s coverage: %s',run_id,json.dumps(self.coverage_stats,sort_keys=True))
            return result
