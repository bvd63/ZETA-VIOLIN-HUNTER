"""Offline billing, durable-cursor and interrupted-discovery regressions."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(TEMP.name) / "bot.db")
from config import Config
import database
database.DB_PATH = os.environ["DB_PATH"]
from provider_state import store, reserve, used, read, write, claim_window
from discovery_queue import DiscoveryQueue, stage, complete
from scrapers.search_reserves import TavilyScraper, ExaScraper, plan, GROUPS, WEB_KEYWORDS


class SearchReservesTests(unittest.TestCase):
    def setUp(self):
        with store() as conn:
            conn.execute("DELETE FROM provider_state")
            conn.execute("DELETE FROM provider_reservations")
            conn.commit()
        with DiscoveryQueue("test") as queue:
            queue.conn.execute("DELETE FROM discovery_queue")
            queue.conn.commit()
        p = patch.multiple(Config, TAVILY_API_KEY="fake", EXA_API_KEY="fake",
                           TAVILY_QUERIES_PER_RUN=8, TAVILY_EXTRA_QUERIES_PER_RUN=6, EXA_QUERIES_PER_RUN=4,
                           EXA_EXTRA_QUERIES_PER_RUN=0,
                           TAVILY_ROLLING_CREDITS=900, EXA_ROLLING_BUDGET_USD=8)
        p.start()
        self.addCleanup(p.stop)

    def run_scraper(self, scraper, handler):
        original = scraper.make_client
        with patch.object(scraper, "make_client", side_effect=lambda **kw:
                          original(transport=httpx.MockTransport(handler), **kw)):
            return asyncio.run(scraper.search())

    def test_reservations_are_atomic_persistent_and_keep_uncertain_charges(self):
        self.assertTrue(reserve("x", 2, 3, 3, now=100000))
        self.assertFalse(reserve("x", 2, 3, 3, now=100000))
        self.assertEqual(used("x", now=100001), 2)
        self.assertTrue(reserve("x", 2, 3, 3, now=100000 + 32 * 86400))

    def test_additive_facebook_queries_keep_all_original_slots_and_fit_budget(self):
        with patch.multiple(Config,TAVILY_EXTRA_QUERIES_PER_RUN=8,TAVILY_ROLLING_CREDITS=1000,EXA_EXTRA_QUERIES_PER_RUN=2):
            tavily=TavilyScraper(); extras=tavily.extra_queries(0)
            self.assertEqual(len(extras),8)
            self.assertEqual(extras[4][1],['willhaben.at'])
            self.assertEqual(extras[5][1],['gumtree.com'])
            self.assertEqual([x[1] for x in extras[-2:]],[['facebook.com'],['facebook.com']])
            self.assertLessEqual((8+len(extras))*62,1000)
            self.assertEqual(len(ExaScraper().extra_queries(0)),2)
            self.assertLessEqual(6*62*17000,8_000_000)
            self.assertLessEqual(6*2*17000,ExaScraper.daily_limit)
            seen=[]
            for engine,expected in ((TavilyScraper(),16),(ExaScraper(),6)):
                def handler(request):
                    seen.append(json.loads(request.content))
                    return httpx.Response(200,json={'results':[]})
                before=len(seen)
                self.run_scraper(engine,handler)
                self.assertEqual(len(seen)-before,expected)
            self.assertEqual([p['query'] for p in seen[:8]],[q for q,_ in plan(0,8)])

    def test_public_facebook_leads_enter_existing_detail_queue_without_actor_calls(self):
        from facebook_discovery import seed_detail_candidates,queries,US_REGIONS
        from scrapers.apify_facebook import store as apify_store
        with patch.object(Config,'APIFY_TOKEN','fake'):
            for candidate in ({'url':'https://www.facebook.com/marketplace/item/123456/','title':'Zeta JLP5 violin','source':'search'},
                              {'url':'https://www.facebook.com/marketplace/nyc/','title':'Zeta violin'},
                              {'url':'https://facebook.com.evil.test/marketplace/item/123/','title':'Zeta violin'}):
                seed_detail_candidates([candidate])
            with apify_store() as conn:
                row=conn.execute('SELECT candidate,checked FROM apify_items WHERE url=?',
                                 ('https://www.facebook.com/marketplace/item/123456/',)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[1],0)
        planned=' '.join(q for cursor in range(100) for q,_ in queries(cursor,2))
        self.assertTrue(all(region in planned for region in US_REGIONS))
        self.assertIn('Zeta JLP',planned)
        self.assertIn('Strados',planned)

    def test_tavily_and_exa_ignore_yahoo_hits_but_keep_other_japanese_sources(self):
        for scraper in (TavilyScraper(), ExaScraper()):
            def handler(request):
                return httpx.Response(200, json={'results':[
                    {'title':'Zeta violin', 'url':'https://auctions.yahoo.co.jp/jp/auction/a123'},
                    {'title':'Zeta violin', 'url':'https://jp.mercari.com/item/m123'}]})
            found = self.run_scraper(scraper, handler)
            self.assertEqual([x['url'] for x in found], ['https://jp.mercari.com/item/m123'])

    def test_duplicate_negative_full_extract_is_persisted_for_tavily_and_exa(self):
        from offer_verifier import saved_indexed_closed_reason
        url='https://reverb.com/item/82035982-zeta'; other='https://reverb.com/item/82035983-zeta'
        title='Zeta Jazz violin'
        for scraper in (TavilyScraper(), ExaScraper()):
            with self.subTest(engine=scraper.name):
                with store() as conn:
                    conn.execute('DELETE FROM provider_state');conn.execute('DELETE FROM provider_reservations');conn.commit()
                calls=[]
                def handler(request):
                    calls.append(request)
                    return httpx.Response(200,json={'results':[
                        {'url':url,'title':title,'content':'Used violin'},
                        {'url':url,'title':title,'content':'# '+title+'\nThis gear has sold\nAbout this listing'},
                        {'url':other,'title':title,'content':'Used violin'}]})
                found=self.run_scraper(scraper,handler)
                self.assertEqual(len(found),2)
                self.assertTrue(saved_indexed_closed_reason({'url':url}))
                self.assertFalse(saved_indexed_closed_reason({'url':other}))
                self.assertEqual(len(calls),14 if isinstance(scraper,TavilyScraper) else 4)

    def test_duplicate_negative_cards_are_persisted_for_google_and_brave(self):
        from offer_verifier import saved_indexed_closed_reason
        from scrapers.google import GoogleScraper
        from scrapers.brave import BraveScraper
        url='https://reverb.com/item/82035982-zeta'; other='https://reverb.com/item/82035983-zeta'
        for engine in ('google','brave'):
            with self.subTest(engine=engine):
                with store() as conn:
                    conn.execute('DELETE FROM provider_state');conn.execute('DELETE FROM provider_reservations');conn.commit()
                calls=[]
                def handler(request):
                    calls.append(request)
                    if engine=='google':
                        return httpx.Response(200,json={'items':[{'link':url,'title':'Zeta violin','snippet':'Used violin'},
                            {'link':url,'title':'Zeta violin','snippet':'This gear has sold'},
                            {'link':other,'title':'Zeta violin','snippet':'Used violin'}]})
                    return httpx.Response(200,json={'web':{'results':[{'url':url,'title':'Zeta violin','description':'Used violin'},
                        {'url':url,'title':'Zeta violin','extra_snippets':['This listing has ended']},
                        {'url':other,'title':'Zeta violin','description':'Used violin'}]}})
                scraper=GoogleScraper('fixture','fixture') if engine=='google' else BraveScraper()
                original=scraper.make_client
                with patch.object(scraper,'make_client',side_effect=lambda **kw: original(transport=httpx.MockTransport(handler),**kw)), \
                     patch.object(scraper,'_should_run',return_value=True), \
                     patch.object(scraper,'plan_queries',return_value=([('zeta',{})],0)), \
                     patch('scrapers.'+engine+'.reserve_request',return_value=True), \
                     patch('asyncio.sleep',new_callable=AsyncMock), \
                     patch.object(Config,'BRAVE_API_KEY','fixture'), \
                     patch.object(Config,'GOOGLE_QUERIES_PER_RUN',1):
                    found=asyncio.run(scraper.search())
                self.assertEqual(len(calls),1)
                self.assertEqual(len(found),2)
                self.assertTrue(saved_indexed_closed_reason({'url':url}))
                self.assertFalse(saved_indexed_closed_reason({'url':other}))

    def test_daily_and_rolling_limits_are_independent(self):
        self.assertTrue(reserve("x", 2, 10, 2, now=100000))
        self.assertFalse(reserve("x", 1, 10, 2, now=100001))
        self.assertTrue(reserve("x", 2, 10, 2, now=200000))

    def test_restart_guard_is_durable(self):
        self.assertTrue(claim_window("guard", 100, now=1000))
        self.assertFalse(claim_window("guard", 100, now=1050))
        self.assertTrue(claim_window("guard", 100, now=1100))

    def test_tavily_never_enables_paid_depth_or_answer_generation(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"title": "Zeta violin", "url": "https://reverb.com/item/123", "content": "Used Zeta"}]})
        found = self.run_scraper(TavilyScraper(), handler)
        self.assertEqual(len(calls), 14)
        self.assertEqual(len(found), 1)
        self.assertTrue(all(p["search_depth"] == "basic" and p["auto_parameters"] is False and
                            p["include_answer"] is False for p in calls))
        self.assertEqual([p["query"] for p in calls[:8]], [q for q,_ in plan(0,8)])
        self.assertEqual(read("tavily:extra_cursor"), 6)
        self.assertEqual(calls[-2]["include_domains"], ["willhaben.at"])
        self.assertEqual(calls[-1]["include_domains"], ["gumtree.com"])
        self.assertEqual(read("tavily:cursor"), 8)
        self.assertEqual(used("tavily"), 14)

    def test_exa_has_bounded_result_and_content_cost(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"results": []})
        self.run_scraper(ExaScraper(), handler)
        self.assertEqual(used("exa"), 4 * 17000)
        self.assertTrue(all(p["type"] == "auto" and p["numResults"] == 10 and
                            p["contents"] == {"highlights": True} for p in calls))

    def test_additional_search_slots_preserve_base_and_cover_all_model_aliases(self):
        from keywords import MODEL_CODES, MODEL_NAMES, ADDITIONAL_WEB_KEYWORDS
        scraper = TavilyScraper()
        expanded = [q for cursor in range(0, 200, 4) for q, _ in scraper.extra_queries(cursor)]
        for alias in list(MODEL_CODES) + ['Zeta ' + name for name in MODEL_NAMES] + ADDITIONAL_WEB_KEYWORDS:
            self.assertTrue(any(alias in q for q in expanded), alias)
        for local in ('제타 바이올린','제타 전자바이올린'):
            self.assertTrue(any(local in q for q in expanded), local)
        self.assertLessEqual((8 + len(scraper.extra_queries(0))) * 62, 900)
        self.assertLessEqual((8 + len(scraper.extra_queries(0))) * 2, scraper.daily_limit)

    def test_exa_source_preferences_do_not_exclude_other_marketplaces(self):
        scraper = ExaScraper()
        payload = scraper.payload('Zeta Jazz Fusion OR "5-strings" for sale auction',
                                  ["reverb.com", "govdeals.com"])
        self.assertEqual(set(payload), {"query", "type", "numResults", "contents"})
        self.assertIn("reverb.com", payload["query"])
        self.assertIn("govdeals.com", payload["query"])
        self.assertIn('"5-strings"', payload["query"])
        self.assertIn("Zeta Jazz Fusion", payload["query"])
        self.assertNotIn(" OR ", payload["query"])
        self.assertNotIn("Source preference", scraper.payload("Strados violin", [])["query"])

    def test_exa_accepts_violin_from_outside_preferred_sources(self):
        def handler(request):
            self.assertNotIn("includeDomains", json.loads(request.content))
            return httpx.Response(200, json={"results": [{
                "title": "Used Zeta Jazz Fusion violin", "url": "https://another-dealer.example/item/123",
                "highlights": ["1993 five-string Zeta violin for sale"]}]})
        found = self.run_scraper(ExaScraper(), handler)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["platform"], "another-dealer.example")
        self.assertIn("1993 five-string", found[0]["description"])

    def test_quota_rejection_keeps_charge_and_does_not_advance_unattempted_queries(self):
        calls = []
        scraper = TavilyScraper()
        self.run_scraper(scraper, lambda r: calls.append(r) or httpx.Response(429, headers={"Retry-After": "120"}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(used("tavily"), 1)
        self.assertEqual(read("tavily:cursor"), 1)
        self.assertIn("429", scraper.health_error())
        self.run_scraper(TavilyScraper(), lambda r: self.fail("guard must block"))

    def test_uncertain_post_is_not_retried_for_free(self):
        def handler(request):
            raise httpx.ReadTimeout("token-bearing-error")
        self.run_scraper(ExaScraper(), handler)
        self.assertEqual(used("exa"), 4 * 17000)
        self.assertEqual(read("exa:cursor"), 4)

    def test_no_http_when_local_allowance_cannot_reserve(self):
        with patch.object(Config, "TAVILY_ROLLING_CREDITS", 0):
            scraper = TavilyScraper()
            self.run_scraper(scraper, lambda r: self.fail("no allowance"))
        self.assertIn("reservation limit", scraper.health_error())

    def test_all_vocabulary_and_domains_remain_in_rotation(self):
        for count, offset in ((8, 0), (4, 7)):
            queries, hosts = set(), set()
            for cursor in range(0, len(GROUPS) * len(WEB_KEYWORDS) * 4, count):
                for query, group in plan(cursor, count, offset):
                    queries.add(query)
                    hosts.update(group)
            self.assertTrue(all(any(keyword in q for q in queries) for keyword in WEB_KEYWORDS))
            self.assertTrue({"reverb.com", "craigslist.org", "govdeals.com", "facebook.com", "tarisio.com"} <= hosts)

    def test_deferred_candidates_survive_restart_and_disappear_from_index(self):
        items = [{"url": f"https://example.com/item/{i}", "title": "Zeta violin"} for i in range(6)]
        first, deferred = stage("test", items, 2)
        self.assertEqual(deferred, 4)
        for item in first:
            complete("test", item, 86400)
        second, deferred = stage("test", [], 2)
        self.assertEqual(deferred, 2)
        self.assertTrue({r["url"] for r in first}.isdisjoint(r["url"] for r in second))

    def test_expired_claim_can_be_recovered_but_parallel_worker_cannot_steal_it(self):
        with DiscoveryQueue("test") as q:
            q.put([{"url": "https://example.com/item/1", "title": "Zeta violin"}])
            now = __import__("time").time()
            self.assertEqual(len(q.claim(1, now=now)), 1)
            self.assertEqual(q.claim(1, now=now + 1), [])
        with DiscoveryQueue("test") as q:
            self.assertEqual(len(q.claim(1, now=now + 1201)), 1)

    def test_failed_metering_does_not_erase_previous_account_usage(self):
        from resource_monitor import refresh_accounts
        write("apify:account", {"usage_usd": 3.8, "checked": 100})
        with patch.object(Config, "APIFY_TOKEN", "fake"), patch.object(Config, "PUSHOVER_APP_TOKEN", ""), \
                patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ReadTimeout("secret"))):
            asyncio.run(refresh_accounts())
        self.assertEqual(read("apify:account")["usage_usd"], 3.8)
        self.assertEqual(read("apify:account")["error"], "ReadTimeout")

    def test_new_critical_resource_problem_is_not_hidden_by_earlier_warning(self):
        from resource_monitor import tick
        data = {"tavily": {"configured": True, "reserved": 850, "limit": 900, "unit": "credite"}}
        bot = type("Bot", (), {"send": AsyncMock(return_value=True)})()
        with patch("resource_monitor.refresh_accounts", AsyncMock()), patch("resource_monitor.snapshot", return_value=data):
            asyncio.run(tick(bot))
            asyncio.run(tick(bot))
            self.assertEqual(bot.send.await_count, 1)
            data["tavily"]["reserved"] = 900
            asyncio.run(tick(bot))
            self.assertEqual(bot.send.await_count, 2)

    def test_govdeals_deferrals_are_not_discarded_and_all_queries_run(self):
        from scrapers.govdeals import GovDealsScraper, GovDealsClient
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        rows = [{"accountId": 1, "assetId": i + 1, "assetShortDescription": "Zeta violin",
                 "assetAuctionEndDateUtc": end} for i in range(32)]
        async def asset(aid, account):
            return {"assetId": int(aid), "accountId": int(account), "assetLongDesc": "Used Zeta violin"}
        search = AsyncMock(return_value=rows)
        with patch.object(GovDealsClient, "initialize", AsyncMock()), \
                patch.object(GovDealsClient, "search", search), patch.object(GovDealsClient, "asset", side_effect=asset):
            first = asyncio.run(GovDealsScraper().search())
            self.assertEqual(search.await_count, 6)
            self.assertEqual(len(first), 30)
            search.return_value = []
            second = asyncio.run(GovDealsScraper().search())
            self.assertEqual(search.await_count, 12)
            self.assertEqual(len(second), 2)
        self.assertTrue({r["url"] for r in first}.isdisjoint(r["url"] for r in second))

    def test_identity_in_real_page_is_not_rejected_for_missing_index_snippet(self):
        import main
        from scrapers.base import BaseScraper
        from price_tracker import PriceTracker
        db, prices = database.Database(), PriceTracker()
        class Search(BaseScraper):
            name = "snippet fixture"
            async def search(self):
                return [{"id": "generic", "title": "Electric violin", "description": "",
                         "url": "https://reverb.com/item/1234", "source": "search"}]
        async def verify(item, http):
            item.update({"description": "Used Zeta Jazz Fusion 5 string violin", "condition": "used"})
            return "live", "fixture"
        try:
            with patch("main.verify_offer", side_effect=verify):
                result = asyncio.run(main._run_scraper_with_resilience(Search(), db, prices, asyncio.Semaphore(1)))
            self.assertEqual(len(result["new"]), 1)
            self.assertEqual(len(db.pending()), 1)
        finally:
            db.close()
            prices.close()

    def test_one_hanging_page_cannot_erase_completed_violin_checks(self):
        import main
        db = database.Database()
        items = [{"id": str(i), "title": "Used Zeta Strados violin", "condition": "used",
                  "url": f"https://reverb.com/item/timeout{i}", "source": "search"} for i in range(2)]
        async def verify(item, http):
            if item["id"] == "1":
                await asyncio.sleep(10)
            return "live", "fixture"
        async def run():
            async with httpx.AsyncClient() as http:
                dropped = {}
                found = await main._verify_search_hits(items, db, http, dropped)
                self.assertEqual([item["id"] for item in found], ["0"])
                self.assertEqual(dropped["liveness_deferred"], 1)
        try:
            with patch("main.verify_offer", side_effect=verify), patch("main.LIVENESS_TIMEOUT_SEC", .01):
                asyncio.run(run())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
