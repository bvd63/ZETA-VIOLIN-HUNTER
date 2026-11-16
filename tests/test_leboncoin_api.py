import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from config import Config
from leboncoin_api import actor_input, cached_offer, closed_reason, item_id, normalize, remember, remember_status, ACTOR
from offer_verifier import verify_offer
from scrapers.apify_leboncoin import ApifyLeboncoinScraper
from scrapers.apify_facebook import store
from keywords import market_queries

URL='https://www.leboncoin.fr/ad/instruments_de_musique/3269447854'
ROW={'type':'ad','url':URL,'listId':'3269447854','title':'Violon Zeta 5 corde',
     'description':'Je vends cette violon Zeta électrique noir. Original cinq cordes. Prix 3500€ négociable.',
     'status':'active','detailsIncluded':True,'price':3500,'currency':'EUR','city':'Saint-Denis',
     'publishedAt':'2026-09-14T09:35:00Z','condition':'État satisfaisant','adType':'offer'}


class LeboncoinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=patch('database.DB_PATH',str(Path(self.temp.name)/'lbc.db'));self.db.start()
    def tearDown(self):self.db.stop();self.temp.cleanup()
    def test_input_preserves_full_french_rotation_and_global_france_no_age_or_title_gate(self):
        inp=actor_input()
        self.assertEqual(inp['keywords'],market_queries('fr',broad=True,limit=8))
        self.assertEqual(inp['locations'],[]);self.assertFalse(inp['searchInTitleOnly'])
        self.assertTrue(inp['includeAdDetails']);self.assertFalse(inp['includeSellerProfiles'])
        for key in ('monitorName','onlyNew','postedWithinDays','postedAfter','telegramBotToken','webhookUrl'):
            self.assertNotIn(key,inp)
        self.assertEqual(actor_input(pilot=True)['keywords'],['zeta'])
    def test_full_primary_text_price_and_old_publication_retained(self):
        item=normalize(ROW)
        self.assertEqual(item['description'],ROW['description']);self.assertEqual(item['price'],'3500 EUR')
        self.assertEqual(item['date_posted'],'2026-09-14');self.assertEqual(item['_leboncoin_observed_state'],'live')
    def test_wrong_id_lookalike_credentials_ports_wanted_and_summary_rejected(self):
        for row in ({**ROW,'listId':'123'}, {**ROW,'url':URL.replace('www.leboncoin.fr','leboncoin.fr.evil.example')},
                    {**ROW,'url':URL.replace('www.leboncoin.fr','user:pass@www.leboncoin.fr')},
                    {**ROW,'url':URL.replace('www.leboncoin.fr','www.leboncoin.fr:444')},
                    {**ROW,'adType':'wanted'}, {**ROW,'type':'summary'}):
            self.assertEqual(normalize(row),{})
        self.assertEqual(item_id('https://www.leboncoin.fr/instruments_de_musique/3269447854.htm'),'3269447854')
    def test_missing_details_is_unknown_and_cannot_erase_sold_evidence(self):
        remember(normalize({**ROW,'status':'sold'}))
        remember(normalize({**ROW,'detailsIncluded':False}))
        self.assertEqual(cached_offer(URL)['listing']['_leboncoin_observed_state'],'dead')
        self.assertTrue(closed_reason(URL))
        remember(normalize(ROW))
        self.assertEqual(closed_reason(URL),'')
        self.assertEqual(cached_offer(URL)['listing']['_leboncoin_observed_state'],'live')
    def test_missing_search_result_is_not_per_id_not_found(self):
        for scope in ('search','run','input'):
            remember_status({'type':'status','scope':scope,'status':'not_found','key':'3269447854'},checked=time.time())
        self.assertEqual(closed_reason(URL),'')
        remember_status({'type':'status','scope':'ad','status':'failed','key':'3269447854'},checked=time.time())
        self.assertEqual(closed_reason(URL),'')
        remember_status({'type':'status','scope':'ad','status':'not_found','key':'3269447854'},checked=time.time())
        self.assertTrue(closed_reason(URL))
        self.assertEqual(closed_reason(URL.replace('3269447854','3269447855')),'')
    def test_stale_positive_is_not_current_stock_but_negative_remains(self):
        remember(normalize(ROW),checked=1000)
        self.assertIsNone(cached_offer(URL,now=1000+6*3600+1))
        remember(normalize({**ROW,'status':'sold'}),checked=1000)
        self.assertIsNotNone(cached_offer(URL,now=1000+30*86400))
    def test_verifier_uses_bound_fresh_primary_description_and_status_without_blocked_html(self):
        remember(normalize(ROW))
        item={'url':URL,'title':'Instrument'}
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:self.fail('No redundant HTTP'))) as client:
                return await verify_offer(item,client)
        self.assertEqual(asyncio.run(run())[0],'live')
        self.assertEqual(item['description'],ROW['description']);self.assertTrue(item['_primary_content_checked'])
    def test_reserved_is_not_declared_sold_or_alertable_active(self):
        remember(normalize({**ROW,'status':'reserved'}))
        item={'url':URL,'title':ROW['title']}
        async def run():
            async with httpx.AsyncClient() as client:return await verify_offer(item,client)
        self.assertEqual(asyncio.run(run())[0],'unknown');self.assertTrue(item['_availability_conflict'])
    def test_partial_searches_and_unattempted_keywords_are_visible(self):
        scraper=ApifyLeboncoinScraper();scraper.reset_health()
        rows=[{**ROW},{'type':'search','searchKeyword':'zeta','pagesRead':1,'complete':False}]
        output=scraper.consume(rows,['zeta','strados'],observed_at=time.time(),fresh=True)
        self.assertEqual([x['url'] for x in output],[URL])
        self.assertEqual(scraper.coverage_stats['observed_searches'],1)
        self.assertEqual(scraper.coverage_stats['complete_searches'],0)
        self.assertTrue(scraper.health_error())
    def test_wrong_actor_cannot_feed_primary_observations(self):
        scraper=ApifyLeboncoinScraper();scraper.reset_health()
        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(scraper,'_request',return_value={'data':{'id':'Run123','actId':'wrong','status':'SUCCEEDED'}}):
                    with self.assertRaises(ValueError):await scraper.read_native_run(client,'Run123')
        asyncio.run(run());self.assertIsNone(cached_offer(URL))
    def test_uncertain_post_does_not_launch_again_or_modify_budget(self):
        with store() as conn:
            conn.execute("INSERT INTO apify_jobs(id,phase,period,cap,created) VALUES('pending','leboncoin','2026-11',30000,?)",(time.time(),));conn.commit()
        scraper=ApifyLeboncoinScraper()
        async def run():
            with patch.object(Config,'APIFY_LEBONCOIN_ENABLED',True),patch.object(Config,'APIFY_TOKEN','test-key'),patch('scrapers.apify_leboncoin.check_access',return_value=True),patch.object(scraper,'_request',side_effect=AssertionError('No repeated POST')):
                return await scraper.search()
        self.assertEqual(asyncio.run(run()),[])
        self.assertIn('uncertain',scraper.health_error())

    def test_reading_old_run_does_not_make_old_stock_or_queries_fresh(self):
        scraper=ApifyLeboncoinScraper();scraper.reset_health()
        scraper.consume([ROW,{'type':'search','searchKeyword':'zeta','pagesRead':1,'complete':True}],['zeta'],observed_at=1000,fresh=False)
        self.assertIsNone(cached_offer(URL))
        self.assertEqual(scraper.discovery_queries_attempted,0)
        self.assertFalse(scraper.coverage_stats['new_run_observations'])
    def test_old_negative_and_unreadable_refresh_cannot_overwrite_newer_active_observation(self):
        remember(normalize(ROW),checked=2000)
        remember_status({'type':'status','scope':'ad','status':'not_found','key':'3269447854'},checked=1000)
        remember(normalize({**ROW,'status':'sold'}),checked=1000)
        self.assertEqual(closed_reason(URL),'')
        self.assertEqual(cached_offer(URL,now=2001)['listing']['_leboncoin_observed_state'],'live')
    def test_invalid_observation_time_cannot_create_permanently_fresh_stock(self):
        for checked in (float('nan'),float('inf'),True,-1):
            with self.assertRaises(ValueError):remember(normalize(ROW),checked=checked)
    def test_local_paris_results_do_not_claim_whole_france_coverage(self):
        scraper=ApifyLeboncoinScraper();scraper.reset_health()
        scraper.consume([ROW,{'type':'search','searchKeyword':'zeta','pagesRead':1,'complete':True,'searchPlace':'Paris'}],['zeta'],observed_at=time.time(),fresh=True)
        self.assertIn('local scope',scraper.health_error())

if __name__=='__main__':unittest.main()
