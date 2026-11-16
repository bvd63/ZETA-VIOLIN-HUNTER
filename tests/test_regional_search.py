"""Real missed French offer, protected additive plans and uncertain API charges."""
import asyncio
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import database
from config import Config
from filters import classify
from provider_state import store, write
from scrapers.google import GoogleScraper
from scrapers.serper import SerperScraper, google_unavailable, parse_response, payload, regional_queries, reserve_credit

URL = 'https://www.leboncoin.fr/ad/instruments_de_musique/3269447854'
TITLE = 'Violon Zeta 5 corde'
BODY = '''Je vends cette violon Zeta électrique noir. Type de produit : violon électrique.
État satisfaisant. Korg Roland Yamaha violon guitare électrique Fbt Dynacord ev rcf Hk.
Prix 3500€ négociable. Violon fabriqué aux États-Unis, original cinq cordes.'''


class RegionalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch('database.DB_PATH',str(Path(self.temp.name)/'test.db'))
        self.patch.start()
        self.key = patch.object(Config,'SERPER_API_KEY','test-not-a-real-key')
        self.key.start()

    def tearDown(self):
        self.key.stop(); self.patch.stop(); self.temp.cleanup()

    def test_real_french_listing_is_eligible_even_with_other_brand_tags_and_old_publication(self):
        item = {'url':URL,'title':TITLE,'description':BODY,'price':'3500 EUR','location':'France','date_posted':'2026-09-14'}
        self.assertEqual(classify(item),'')

    def test_explicit_regions_preserve_broad_models_and_do_not_require_violin_or_date(self):
        queries = regional_queries()
        self.assertEqual(len(queries),4)
        self.assertEqual([p['gl'] for _,p in queries],['fr','fr','ca','ca'])
        self.assertEqual([p['hl'] for _,p in queries],['fr','fr','fr','en'])
        self.assertIn('site:leboncoin.fr',queries[0][0])
        for q,p in queries:
            for term in ('Zeta','Strados','JV5','JV44','JLP5'):
                self.assertIn(term,q)
            self.assertNotIn('dateRestrict',p)
            self.assertNotIn('tbs',payload(q,p))

    def test_lifetime_allowance_does_not_reset_after_month_boundary(self):
        start = datetime(2026,11,30,12,tzinfo=timezone.utc).timestamp()
        with patch.object(Config,'SERPER_TOTAL_CREDITS',10):
            self.assertEqual(sum(reserve_credit(auxiliary=True,now=start,planned_per_cycle=2) for _ in range(10)),6)
            self.assertEqual(sum(reserve_credit(now=start,planned_per_cycle=2) for _ in range(6)),4)
            self.assertFalse(reserve_credit(now=start+86400,planned_per_cycle=2))
        with store() as c:
            self.assertEqual(c.execute("SELECT sum(units) FROM provider_reservations WHERE provider='serper'").fetchone()[0],10)

    def test_parallel_reservations_cannot_overdraw(self):
        with patch.object(Config,'SERPER_TOTAL_CREDITS',5):
            # Initialize schema before testing the concurrent transactions.
            with store(): pass
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(lambda _:reserve_credit(planned_per_cycle=20),range(16))),5)

    def test_wrong_query_challenge_and_invalid_layout_are_not_healthy_zero(self):
        for data in ({}, {'error':'blocked'}, {'searchParameters':{'q':'wrong'},'organic':[]}, {'searchParameters':{'q':'q'}}, {'searchParameters':{'q':'q'},'organic':{}}):
            with self.assertRaises(ValueError): parse_response(data,'q')
        self.assertEqual(parse_response({'searchParameters':{'q':'q'},'organic':[]},'q'),([],0))

    def test_bad_rows_do_not_hide_later_real_french_offer(self):
        data={'searchParameters':{'q':'q'},'organic':[None,{'link':'http://127.0.0.1/a','title':'Zeta'}, {'link':URL,'title':TITLE,'snippet':BODY}]}
        rows,raw=parse_response(data,'q')
        self.assertEqual(raw,3); self.assertEqual([r['url'] for r in rows],[URL])

    def test_google_eligibility_uses_last_attempted_not_skipped_or_old_failure(self):
        with patch.object(Config,'GOOGLE_API_KEY','test'),patch.object(Config,'GOOGLE_CSE_ID','test'):
            with store() as c:
                c.execute('CREATE TABLE scraper_stats(id INTEGER PRIMARY KEY,scraper TEXT,raw_count INTEGER,error TEXT)')
                c.execute("INSERT INTO scraper_stats VALUES(1,'Google Search',0,'API_PROJECT_NOT_ELIGIBLE')")
                c.execute("INSERT INTO scraper_stats VALUES(2,'Google Search',-1,'')"); c.commit()
            self.assertTrue(google_unavailable())
            with store() as c:
                c.execute("INSERT INTO scraper_stats VALUES(3,'Google Search',1,'')"); c.commit()
            self.assertFalse(google_unavailable())

    def run_scraper(self, handler, *, unavailable=False, reserve=True):
        calls=[]
        def transport(request):
            calls.append(request)
            return handler(request)
        scraper=SerperScraper()
        async def go():
            with patch('scrapers.serper.google_unavailable',return_value=unavailable),(patch('scrapers.serper.reserve_credit',return_value=reserve) if reserve is not None else nullcontext()),patch.object(scraper,'make_client',side_effect=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(transport),**kw)):
                return await scraper.search()
        return scraper,asyncio.run(go()),calls

    def test_four_regional_calls_keep_primary_url_and_description(self):
        def reply(request):
            q=json.loads(request.content)['q']
            return httpx.Response(200,json={'searchParameters':{'q':q},'organic':[{'link':URL,'title':TITLE,'snippet':BODY}]})
        scraper,rows,calls=self.run_scraper(reply)
        self.assertEqual(len(calls),4); self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['description'],BODY)
        self.assertEqual(scraper.coverage_stats['valid_responses'],4)
        self.assertTrue(all(r.headers['X-API-KEY']=='test-not-a-real-key' for r in calls))
        self.assertTrue(all('key=' not in str(r.url) for r in calls))

    def test_backup_preserves_entire_google_plan_after_all_four_new_queries(self):
        def reply(request):
            q=json.loads(request.content)['q']
            return httpx.Response(200,json={'searchParameters':{'q':q},'organic':[]})
        write('serper:google_cursor',42)
        scraper,_,calls=self.run_scraper(reply,unavailable=True)
        original,_=GoogleScraper('','').plan_queries(42)
        self.assertEqual([json.loads(r.content)['q'] for r in calls[4:]],[q for q,_ in original])
        self.assertEqual(scraper.coverage_stats['google_backup_attempted'],len(original))

    def test_unfunded_search_does_not_send_any_http_or_advance_matrix(self):
        _,rows,calls=self.run_scraper(lambda _:self.fail('No HTTP expected'),unavailable=True,reserve=False)
        self.assertEqual(rows,[]); self.assertEqual(calls,[])
        from provider_state import read
        self.assertEqual(read('serper:google_cursor',42),42)

    def test_timeout_post_is_not_retried_and_real_reservation_remains(self):
        def fail(request): raise httpx.ReadTimeout('not a free retry',request=request)
        scraper,_,calls=self.run_scraper(fail,reserve=None)
        self.assertEqual(len(calls),2)
        self.assertEqual(len({r.content for r in calls}),2)
        self.assertEqual(len(scraper.content_failures),2)
        with store() as conn:
            self.assertEqual(conn.execute("SELECT sum(units) FROM provider_reservations WHERE provider='serper'").fetchone()[0],2)

    def test_quota_rejection_stops_without_advancing_unattempted_backup(self):
        scraper,_,calls=self.run_scraper(lambda _:httpx.Response(402,json={'message':'credits exhausted'}),unavailable=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(scraper.coverage_stats['google_backup_attempted'],0)

    def test_one_off_credit_allowance_warns_before_exhaustion(self):
        from resource_monitor import warning_events
        events=warning_events({'serper':{'configured':True,'reserved':2000,'limit':2500,'unit':'cereri'}})
        self.assertTrue(any(key=='serper:budget:80' for key,_ in events))

    def test_ignored_site_scope_is_reported_but_does_not_discard_a_real_violin(self):
        def reply(request):
            q=json.loads(request.content)['q']
            return httpx.Response(200,json={'searchParameters':{'q':q},'organic':[{'link':'https://reverb.com/item/123-zeta-jv5','title':'Zeta JV5 violin'}]})
        scraper,rows,_=self.run_scraper(reply)
        self.assertEqual(len(rows),1)
        self.assertEqual(scraper.coverage_stats['scope_mismatches'],4)
        self.assertIn('scope',scraper.health_error())


if __name__=='__main__': unittest.main()
