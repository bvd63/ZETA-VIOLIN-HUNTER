"""Failure/restart/cost regressions for the owner's five-point milestone."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import httpx
import database
from config import Config
from provider_state import store, read
from craigslist_queries import stage_queries, finish_queries, pending_count
from scrapers.craigslist import CraigslistScraper
from scrapers.reverb import ReverbScraper
from scrapers.facebook_international import reserve_region
from facebook_regions import regional_entry, WEIGHTS, REGIONS


class MilestoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(database,'DB_PATH',str(Path(self.temp.name)/'test.db'));self.dbpatch.start()
        self.config=patch.multiple(Config,SERPER_TOTAL_CREDITS=2500,GOOGLE_QUERIES_PER_RUN=48,RUNS_PER_DAY=2,
            CRAIGSLIST_MAX_US_CITIES=0,CRAIGSLIST_COUNTRIES='US,CA',REVERB_INCREMENTAL_ENABLED=True)
        self.config.start()
    async def asyncTearDown(self):
        self.config.stop();self.dbpatch.stop();self.temp.cleanup()

    async def craigslist(self,handler):
        scraper=CraigslistScraper();original=scraper.make_client
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)),patch('scrapers.craigslist.asyncio.sleep',AsyncMock()):
            results=await scraper.search()
        return scraper,results

    async def test_transient_search403_recovers_exact_query_and_keeps_other_three(self):
        calls=[];counts=Counter()
        def handler(req):
            if req.url.path=='/Areas':return httpx.Response(200,json=[{'AreaID':654,'Hostname':'lewiston','Country':'US'}])
            q=req.url.params['query'];calls.append(q);counts[q]+=1
            if q==CraigslistScraper.plan_queries()[2][0] and counts[q]==1:return httpx.Response(403)
            return httpx.Response(200,json={'data':{'items':[]}})
        s,found=await self.craigslist(handler)
        self.assertEqual(len(calls),5);self.assertEqual(len(counts),4)
        self.assertEqual(s.coverage_stats['search_valid'],4)
        self.assertEqual(s.coverage_stats['search_recovered'],1)
        self.assertEqual(pending_count(),0);self.assertEqual(s.health_error(),'');self.assertEqual(found,[])

    async def test429_cooldown_survives_restart_without_moving_due_or_losing_other_queries(self):
        calls=[]
        def handler(req):
            if req.url.path=='/Areas':return httpx.Response(200,json=[{'AreaID':654,'Hostname':'lewiston','Country':'US'}])
            q=req.url.params['query'];calls.append(q)
            return httpx.Response(429,headers={'Retry-After':'86400'}) if q==CraigslistScraper.plan_queries()[0][0] else httpx.Response(200,json={'data':{'items':[]}})
        await self.craigslist(handler)
        pending=stage_queries([]);due=next(iter(pending.values()))['due']
        second,_=await self.craigslist(handler)
        self.assertEqual(len(calls),7);self.assertEqual(second.coverage_stats['search_deferred'],1)
        self.assertEqual(next(iter(stage_queries([]).values()))['due'],due)
        self.assertEqual(pending_count(),1)

    async def test_area_reference_failure_retains_complete_validated_list(self):
        areas=[{'AreaID':654,'Hostname':'lewiston','Country':'US'},{'AreaID':25,'Hostname':'toronto','Country':'CA'}]
        first=True;calls=[]
        def handler(req):
            nonlocal first
            if req.url.path=='/Areas':
                if first:first=False;return httpx.Response(200,json=areas)
                return httpx.Response(403)
            calls.append(req.url.params['batch'])
            return httpx.Response(200,json={'data':{'items':[]}})
        await self.craigslist(handler);second,_=await self.craigslist(handler)
        self.assertEqual(second.coverage_stats['areas'],2)
        self.assertEqual(len(calls),16)
        self.assertIn('complete region list',second.health_error())

    async def test_cancelled_unfinished_query_survives_without_claiming_success(self):
        work=[((654,'lewiston','US'),'zeta','sss')]
        stage_queries(work)
        self.assertEqual(pending_count(),1)
        self.assertEqual(next(iter(stage_queries([]).values()))['attempts'],0)
        finish_queries([(work[0],'HTTP 403',3600)])
        self.assertEqual(next(iter(stage_queries([]).values()))['attempts'],1)
        finish_queries([(work[0],'',0)]);self.assertEqual(pending_count(),0)

    async def test_serper_additions_leave_every_original_daily_slot_available(self):
        from scrapers.serper import reserve_credit
        now=datetime(2026,11,30,12,tzinfo=timezone.utc).timestamp()
        with patch.object(Config,'SERPER_TOTAL_CREDITS',108):
            self.assertEqual(sum(reserve_region(now) for _ in range(8)),4)
            self.assertEqual(sum(reserve_credit(now=now,planned_per_cycle=52) for _ in range(104)),104)
            self.assertFalse(reserve_credit(now=now,planned_per_cycle=52))

    async def test_regional_reservations_are_atomic_and_one_time_credit_does_not_renew(self):
        now=datetime(2026,11,30,12,tzinfo=timezone.utc).timestamp()
        with store():pass
        with patch.object(Config,'SERPER_TOTAL_CREDITS',108):
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(lambda _:reserve_region(now),range(12))),4)
            self.assertFalse(reserve_region(now+86400))

    async def test_auxiliary_regions_protect_both_original_and_new_scheduled_slots(self):
        now=datetime(2026,11,30,12,tzinfo=timezone.utc).timestamp()
        with patch.object(Config,'SERPER_TOTAL_CREDITS',110):
            self.assertTrue(reserve_region(now,auxiliary=True));self.assertTrue(reserve_region(now,auxiliary=True))
            self.assertFalse(reserve_region(now,auxiliary=True))
            self.assertEqual(sum(reserve_region(now) for _ in range(4)),4)
            from scrapers.serper import reserve_credit
            self.assertEqual(sum(reserve_credit(now=now,planned_per_cycle=52) for _ in range(104)),104)

    async def test_regional_index_uses_real_query_echo_and_never_retries_paid_post(self):
        from scrapers.facebook_international import FacebookInternationalScraper
        s=FacebookInternationalScraper();s.auxiliary_work=True;requests=[]
        def handler(req):
            requests.append(req);q=json.loads(req.content)['q']
            return httpx.Response(200,json={'searchParameters':{'q':q},'organic':[
                {'title':'Zeta JV45','link':'https://www.facebook.com/marketplace/item/123456/','snippet':'Used Zeta jazz fusion'},
                {'title':'Other','link':'https://www.facebook.com/marketplace/toronto/'}]})
        original=s.make_client
        with patch.object(Config,'SERPER_API_KEY','fixture'),patch.object(s,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            found=await s.search();again=await s.search()
        self.assertEqual(len(requests),2);self.assertEqual(len(found),2);self.assertEqual(again,[])
        self.assertEqual(read('facebook:international:index:cursor'),2)
        self.assertIsNone(read('facebook:international:index:last_run'))
        self.assertEqual([json.loads(r.content)['gl'] for r in requests],['us','ca'])
        self.assertTrue(all('verification' not in r for r in found))

    async def test_all_terms_visit_all_cities_and_all_countries_remain_in_rotation(self):
        entries=[regional_entry(i) for i in range(12000)]
        self.assertEqual({e['country'] for e in entries},set(REGIONS))
        for country,(language,cities) in REGIONS.items():
            selected=[e for e in entries if e['country']==country]
            terms={e['term'] for e in selected}
            self.assertTrue(any('JV44' in t for t in terms))
            self.assertEqual({(e['term'],e['city']) for e in selected},{(t,c) for t in terms for c in cities})
        self.assertEqual(Counter(WEIGHTS)['US'],4);self.assertEqual(Counter(WEIGHTS)['CA'],4)
        self.assertTrue(any(e['country']=='CA' and e['language']=='fr' for e in entries))
        for e in entries[:24]:
            self.assertIn('JV44',e['query']);self.assertNotIn('1993',e['query'])
            self.assertNotIn('violin',e['query'])

    async def test_reverb_page1_survives_cancellation_during_page2(self):
        from discovery_queue import DiscoveryQueue
        from tests.test_reverb_workflow import api_item
        async def handler(req):
            if req.url.params.get('page')=='2':raise asyncio.CancelledError()
            return httpx.Response(200,json={'listings':[api_item()]})
        scraper=ReverbScraper();scraper.incremental=True;original=scraper.make_client
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            with self.assertRaises(asyncio.CancelledError):await scraper.search()
        with DiscoveryQueue('search:Reverb') as queue:
            cards=queue.claim(10)
        self.assertEqual(len(cards),1);self.assertIn('/item/123-',cards[0]['url'])
        self.assertNotIn('verification',cards[0])

    async def test_extra_reverb_uses_only_brand_query_and_same_delivery_dedup(self):
        import main
        from tests.test_reverb_workflow import api_item
        calls=[]
        def handler(req):
            calls.append(req)
            return httpx.Response(200,json={'listings':[api_item()]})
        original=ReverbScraper.make_client
        notifier=type('Notifier',(),{'send_listings':AsyncMock(side_effect=lambda rows:rows)})()
        async def live(item,http):return 'live','fixture primary identity/state'
        with patch.object(ReverbScraper,'make_client',lambda self,**kw:original(self,transport=httpx.MockTransport(handler),**kw)), \
             patch('main.TelegramNotifier',return_value=notifier),patch('main.verify_offer',AsyncMock(side_effect=live)), \
             patch('main._local_now',return_value=datetime(2026,11,16,23,15)),patch('main.build_scrapers') as full:
            await main.reverb_incremental_tick();await main.reverb_incremental_tick()
        full.assert_not_called();self.assertEqual(len(calls),2)
        self.assertTrue(all(r.url.params.get('make')=='Zeta' and 'query' not in r.url.params for r in calls))
        self.assertEqual(notifier.send_listings.await_count,1)
        self.assertEqual(read('reverb:incremental:health')['sent'],1)
        self.assertEqual(read('reverb:incremental:health')['paid_searches'],0)
        db=database.Database();self.assertTrue(db.was_url_alerted('https://reverb.com/item/123-zeta-jv5'));db.close()

    async def test_extra_reverb_leaves_core_window_and_closed_offers_untouched(self):
        import main
        with patch('main._local_now',return_value=datetime(2026,11,16,10,5)),patch.object(ReverbScraper,'search',AsyncMock()) as search:
            await main.reverb_incremental_tick()
        search.assert_not_awaited();self.assertIsNone(read('reverb:incremental:last_attempt'))

    async def test_private_reverb_endpoint_cannot_run_without_auth_or_force_past_guard(self):
        import main
        from types import SimpleNamespace
        with patch.object(Config,'SEARCH_TOKEN','fixture'),patch('main.reverb_incremental_tick',AsyncMock()) as tick:
            response=await main.handle_reverb_search(SimpleNamespace(headers={},query={'fresh':'1'}))
            self.assertEqual(response.status,401);tick.assert_not_awaited()
            response=await main.handle_reverb_search(SimpleNamespace(headers={'X-Search-Token':'fixture'},query={'fresh':'1'}))
            self.assertEqual(json.loads(response.body)['status'],'guarded');tick.assert_awaited_once_with()

    async def test_saved_reverb_page_cannot_erase_later_primary_sold_evidence(self):
        from tests.test_reverb_workflow import api_item
        from offer_verifier import availability_conflict
        from discovery_queue import DiscoveryQueue
        def handler(req):
            return httpx.Response(200,json={'listings':[api_item('live' if req.url.params['page']=='1' else 'sold')]})
        s=ReverbScraper();s.incremental=True;original=s.make_client
        with patch.object(s,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            found=await s.search()
        self.assertEqual(found,[])
        with DiscoveryQueue('search:Reverb') as queue:saved=queue.claim(1)[0]
        self.assertTrue(availability_conflict(saved,'unknown'))

    async def test_extra_actor_expired_output_does_not_block_original_discovery(self):
        from scrapers.apify_facebook import ApifyFacebookScraper,store as fbstore
        s=ApifyFacebookScraper();s.reset_health()
        with fbstore() as c:
            c.execute("INSERT INTO apify_jobs VALUES('old','regional','expired','2026-11',27000,0,0)");c.commit()
        starts=[]
        async def start(client,phase,urls,cap):starts.append(phase);return '',[]
        async def expired(client,run):raise RuntimeError('Apify HTTP 404')
        with patch.object(s,'is_configured',return_value=True),patch('apify_auth.check_access',AsyncMock(return_value=True)),patch.object(s,'_start',side_effect=start),patch.object(s,'read_run',side_effect=expired):
            await s.search()
        self.assertEqual(starts,['discovery'])
        with fbstore() as c:
            done,cap=c.execute("SELECT done,cap FROM apify_jobs WHERE id='old'").fetchone()
        self.assertEqual((done,cap),(1,27000))
        self.assertEqual(s.coverage_stats['additional_regions']['state'],'expired_output_reservation_retained')

    async def test_delivery_timing_does_not_claim_read_or_phone_sound(self):
        from alert_receipts import AlertReceipts
        from pipeline_health import snapshot
        r=AlertReceipts()
        r.conn.execute("INSERT INTO alert_receipts(id,chat_id,url,text,created_at,delivered_at) VALUES('one','x','https://example.org/item/1','fixture',100,103)")
        r.conn.commit();s=snapshot(r.conn,200)
        self.assertEqual(s['delivery_latency_seconds']['max'],3)
        self.assertEqual(s['owner_response_recorded'],0);self.assertEqual(s['open_delivered'],1)
        self.assertFalse(s['physical_phone_sound_verified']);r.close()

if __name__=='__main__':unittest.main()
