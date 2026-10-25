"""Offline API regressions: observed schemas, billing guards and interrupted runs."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(TEMP.name) / "bot.db")
import database
database.DB_PATH = os.environ["DB_PATH"]
from config import Config
from filters import classify
from keywords import STRING_VARIANTS, WEB_KEYWORDS, market_queries
from scrapers.apify_facebook import ApifyFacebookScraper, normalize, available, regional_plan, store


FENTON = {"listingTitle": "(Price lowered)Zeta Strados 5 string electric violin",
          "itemUrl": "https://www.facebook.com/marketplace/item/1655832142144071/",
          "listingPrice": {"amount": "3000", "currency": "USD"},
          "description": {"text": "Purchased in 2001. Comes with a case."},
          "locationText": {"text": "Fenton, MI"}, "condition": "Used - like new",
          "isLive": True, "isSold": False, "isPending": False}
PIEDMONT_CARD = {"marketplace_listing_title": "Zeta Strados Electric Violin",
                "listingUrl": "https://www.facebook.com/marketplace/item/931979966125754/?ref=search",
                "listing_price": {"amount": "3000"},
                "location": {"reverse_geocode": {"city": "Piedmont", "state": "CA"}},
                "is_live": True, "is_sold": False, "is_pending": False}
CASE, STRADOS_2001 = json.loads((Path(__file__).parent / "fixtures/facebook_2200_filter_regression.json").read_text())


class ApifyTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(Config, APIFY_TOKEN="test-token", APIFY_ENABLED=True,
            APIFY_DISCOVERY_TASK_ID="discoveryTask", APIFY_DETAILS_TASK_ID="detailsTask",
            APIFY_MONTHLY_LIMIT_USD=4, APIFY_DISCOVERY_CAP_USD=.065, APIFY_DETAILS_CAP_USD=.02,
            APIFY_GUARD_HOURS=10, APIFY_DETAIL_TTL_HOURS=72)
        self.config.start()
        self.addCleanup(self.config.stop)
        with store() as conn:
            for table in ("apify_jobs", "apify_items", "apify_state", "apify_costs"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()

    def scraper(self, handler):
        scraper = ApifyFacebookScraper()
        scraper.reset_health()
        original = scraper.make_client
        scraper.make_client = lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
        return scraper

    def test_manual_discovery_keeps_regular_guard_and_obeys_global_budget(self):
        scraper=ApifyFacebookScraper();scraper.reset_health();scraper._cursor=0
        previous=str(time.time())
        with store() as conn:
            conn.execute("INSERT INTO apify_state VALUES ('last_discovery',?)",(previous,));conn.commit()
        scraper.manual_fresh=True
        self.assertTrue(scraper._reserve('discovery',.065))
        self.assertFalse(scraper._reserve('discovery',.065))
        with store() as conn:
            self.assertEqual(conn.execute("SELECT value FROM apify_state WHERE key='last_discovery'").fetchone()[0],previous)
        with patch.object(Config,'APIFY_MONTHLY_LIMIT_USD',.065):
            self.assertFalse(scraper._reserve('details',.02))

    def test_verified_terminal_cost_releases_only_known_unused_reservation(self):
        from apify_costs import settle, spent_micro
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        with store() as conn:
            conn.execute('INSERT INTO apify_jobs VALUES (?,?,?,?,?,?,?)',
                         ('one','details','knownRun',month,20000,time.time(),1))
            conn.commit()
        for data in ({'status':'RUNNING','usageTotalUsd':.001}, {'status':'SUCCEEDED'},
                     {'status':'SUCCEEDED','usageTotalUsd':float('nan')},
                     {'status':'FAILED','usageTotalUsd':-1}):
            self.assertFalse(settle('knownRun', data))
        with store() as conn:
            self.assertEqual(spent_micro(conn, month), 20000)
        self.assertTrue(settle('knownRun', {'status':'SUCCEEDED','usageTotalUsd':.0124}))
        with store() as conn:
            self.assertEqual(spent_micro(conn, month), 12400)
            self.assertEqual(conn.execute('SELECT cap FROM apify_jobs').fetchone()[0], 20000)
        settle('knownRun', {'status':'SUCCEEDED','usageTotalUsd':0})
        with store() as conn:
            self.assertEqual(spent_micro(conn, month), 12400)  # no later undercount

    def test_unknown_post_response_keeps_full_cost_and_terminal_read_settles(self):
        from apify_costs import spent_micro
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        with store() as conn:
            conn.execute('INSERT INTO apify_jobs VALUES (?,?,?,?,?,?,?)',
                         ('uncertain','details',None,month,20000,time.time(),0))
            conn.execute('INSERT INTO apify_jobs VALUES (?,?,?,?,?,?,?)',
                         ('known','details','knownRun',month,20000,time.time(),0))
            conn.commit()
        def handler(request):
            if '/actor-runs/' in request.url.path:
                return httpx.Response(200,json={'data':{'status':'SUCCEEDED','usageTotalUsd':.0124,'defaultDatasetId':'dataset'}})
            return httpx.Response(200,json=[])
        async def read():
            scraper = self.scraper(handler)
            async with scraper.make_client() as client:
                await scraper.read_run(client, 'knownRun')
        asyncio.run(read())
        with store() as conn:
            self.assertEqual(spent_micro(conn, month), 32400)

    def test_reconcile_old_finished_runs_uses_only_metadata_gets(self):
        from apify_costs import reconcile, spent_micro
        from datetime import datetime, timezone
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        with store() as conn:
            conn.execute('INSERT INTO apify_jobs VALUES (?,?,?,?,?,?,?)',
                         ('done','discovery','oldRun',month,65000,time.time(),1))
            conn.commit()
        calls=[]
        def handler(request):
            calls.append(request.method)
            return httpx.Response(200,json={'data':{'status':'SUCCEEDED','usageTotalUsd':.062}})
        async def check():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                self.assertEqual(await reconcile(client),1)
                self.assertEqual(await reconcile(client),0)
        asyncio.run(check())
        self.assertEqual(calls,['GET'])
        with store() as conn:
            self.assertEqual(spent_micro(conn, month),62000)

    def test_both_real_schemas_and_strict_availability(self):
        fenton = normalize(FENTON)
        piedmont = normalize(PIEDMONT_CARD)
        self.assertEqual(fenton["description"], "Purchased in 2001. Comes with a case.")
        self.assertEqual(piedmont["location"], "Piedmont, CA")
        self.assertEqual(piedmont["price"], "3000 USD")
        self.assertTrue(available(fenton))
        self.assertEqual(classify(fenton), "")
        for field in ("isLive", "isSold", "isPending"):
            altered = {key: value for key, value in FENTON.items() if key != field}
            self.assertFalse(available(normalize(altered)))
        self.assertFalse(available(normalize({**FENTON, "isSold": True})))
        self.assertFalse(available(normalize({**FENTON, "isPending": True})))

    def test_error_rows_wrong_hosts_and_noise(self):
        self.assertEqual(normalize({"error": "no_items", "url": "https://www.facebook.com/api/graphql/"}), {})
        self.assertEqual(normalize({**FENTON, "itemUrl": "https://facebook.com.evil.test/marketplace/item/1/"}), {})
        self.assertEqual(normalize({**FENTON, "itemUrl": "http://127.0.0.1/marketplace/item/1/"}), {})
        self.assertNotEqual(classify(normalize({**FENTON, "listingTitle": "High Sierra Zeta hydration backpack"})), "")
        self.assertNotEqual(classify(normalize({**FENTON, "listingTitle": "Zeta Educator Electric Viola"})), "")
        self.assertTrue(normalize({**FENTON, "location": [], "listingPhotos": "invalid", "primaryListingPhoto": {"image": "invalid"}}))

    def test_actual_2200_case_and_violin_are_classified_correctly(self):
        self.assertTrue(available(normalize(CASE)))
        self.assertEqual(classify(normalize(CASE)), "noise")
        self.assertTrue(available(normalize(STRADOS_2001)))
        self.assertEqual(classify(normalize(STRADOS_2001)), "")

    def test_reclassifies_fresh_cache_without_paid_runs(self):
        with store() as conn:
            for raw in (CASE, STRADOS_2001):
                item = normalize(raw)
                data = json.dumps(item)
                conn.execute("INSERT INTO apify_items VALUES (?,?,?,?)", (item["url"], data, data, time.time()))
            conn.commit()
        requests = []
        with patch.object(Config, "APIFY_MONTHLY_LIMIT_USD", 0):
            results = asyncio.run(self.scraper(lambda r: requests.append(r) or httpx.Response(500)).search())
        self.assertEqual(requests, [])
        self.assertEqual([item["url"] for item in results], [STRADOS_2001["itemUrl"]])

    def test_cloud_predeploy_check_reads_private_tasks_without_starting_runs(self):
        from apify_preview import preview
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            return httpx.Response(200, json={"data": {"id": request.url.path.rsplit("/", 1)[1]}})
        with patch.object(ApifyFacebookScraper, "make_client", lambda self, **kw:
                httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)):
            result = asyncio.run(preview("", False, True))
        self.assertEqual(result["tasks_verified"], 2)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.method == "GET" for request in requests))

    def test_expired_interrupted_run_does_not_stall_future_discovery(self):
        with store() as conn:
            conn.execute("INSERT INTO apify_jobs VALUES ('old','discovery','expiredRun','2026-09',65000,0,0)")
            conn.commit()
        scraper = self.scraper(lambda r: httpx.Response(404))
        self.assertEqual(asyncio.run(scraper.search()), [])
        with store() as conn:
            self.assertEqual(conn.execute("SELECT done FROM apify_jobs WHERE id='old'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT cap FROM apify_jobs WHERE id='old'").fetchone()[0], 65000)

    def test_global_string_vocabulary_is_bounded_and_rotates(self):
        from datetime import datetime, timedelta
        found = set()
        for n in range(140):
            result = market_queries(now=datetime(2026, 10, 1, 10) + timedelta(hours=n*12), limit=8)
            self.assertLessEqual(len(result), 8)
            self.assertIn("Zeta violin", result)
            found.update(result)
        for variant in STRING_VARIANTS:
            self.assertIn(f"Zeta {variant} violin", found)
            self.assertIn('"' + variant + '"', " ".join(WEB_KEYWORDS))
        self.assertIn("query=zeta+strados", regional_plan(0)[0]["url"])
        self.assertNotEqual(regional_plan(0), regional_plan(1))

    def test_two_stage_filters_before_details_and_uses_auth_header(self):
        starts = []
        # A case from an older deployment must not consume a detail slot either.
        with store() as conn:
            item = normalize(CASE)
            conn.execute("INSERT INTO apify_items (url,candidate) VALUES (?,?)", (item["url"], json.dumps(item)))
            conn.commit()
        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            self.assertNotIn("token", request.url.params)
            if request.method == "POST":
                body = json.loads(request.content)
                starts.append((str(request.url.path), body))
                self.assertIn("maxTotalChargeUsd", request.url.params)
                run = "discoveryRun" if len(starts) == 1 else "detailsRun"
                return httpx.Response(201, json={"data": {"id": run}})
            if "actor-runs" in request.url.path:
                dataset = "cards" if "discoveryRun" in request.url.path else "details"
                return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": dataset}})
            rows = [PIEDMONT_CARD, {"marketplace_listing_title": CASE["listingTitle"], "listingUrl": CASE["itemUrl"]},
                    {**PIEDMONT_CARD, "listingUrl": "https://www.facebook.com/marketplace/item/2/",
                     "marketplace_listing_title": "High Sierra Zeta backpack"}] if "/cards/" in request.url.path else [
                         {**FENTON, "listingTitle": PIEDMONT_CARD["marketplace_listing_title"], "itemUrl": PIEDMONT_CARD["listingUrl"]}]
            return httpx.Response(200, json=rows)
        scraper = self.scraper(handler)
        result = asyncio.run(scraper.search())
        self.assertEqual(len(result), 1)
        self.assertFalse(starts[0][1]["includeListingDetails"])
        self.assertEqual(len(starts[1][1]["startUrls"]), 1)
        self.assertTrue(starts[1][1]["includeListingDetails"])
        self.assertEqual(scraper.health_error(), "")
        # No second paid run during the restart/manual-trigger guard.
        asyncio.run(scraper.search())
        self.assertEqual(len(starts), 2)

    def test_budget_is_reserved_before_post_and_fails_closed(self):
        requests = []
        scraper = self.scraper(lambda r: requests.append(r) or httpx.Response(500))
        with patch.object(Config, "APIFY_MONTHLY_LIMIT_USD", .064):
            self.assertEqual(asyncio.run(scraper.search()), [])
        self.assertEqual(requests, [])
        self.assertIn("monthly budget", scraper.health_error())

    def test_preferred_detail_slot_does_not_starve_old_strados(self):
        with store() as conn:
            for number, title in [(1,'Zeta Strados violin'), (2,'Zeta JLP5 violin'), (3,'Zeta Jazz JV44 violin')]:
                item = normalize({**FENTON, 'itemUrl':f'https://www.facebook.com/marketplace/item/{number}/',
                                  'listingTitle':title})
                conn.execute('INSERT INTO apify_items (url,candidate) VALUES (?,?)',
                             (item['url'],json.dumps(item)))
            conn.commit()
        scraper = self.scraper(lambda r: self.fail('No live Apify requests'))
        calls = []
        async def start(client, phase, urls, cap):
            calls.append((phase, urls))
            return '', []
        with patch.object(scraper, '_start', side_effect=start):
            asyncio.run(scraper.search())
        selected = next(urls for phase,urls in calls if phase=='details')
        self.assertEqual([entry['url'].split('/')[-2] for entry in selected], ['2','1'])

    def test_incomplete_card_gets_identity_from_full_details(self):
        starts = []
        card = {**PIEDMONT_CARD, "marketplace_listing_title": "Electric violin 5 strings with case"}
        detail = {**FENTON, "itemUrl": PIEDMONT_CARD["listingUrl"],
                  "listingTitle": card["marketplace_listing_title"],
                  "description": {"text": "Excellent condition. " * 40 + "Zeta JV5, 1997."}}
        def handler(request):
            if request.method == "POST":
                starts.append(json.loads(request.content))
                return httpx.Response(201, json={"data": {"id": "cardsRun" if len(starts) == 1 else "detailsRun"}})
            if "actor-runs" in request.url.path:
                dataset = "cards" if "cardsRun" in request.url.path else "details"
                return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": dataset}})
            return httpx.Response(200, json=[card if "/cards/" in request.url.path else detail])
        scraper = self.scraper(handler)
        result = asyncio.run(scraper.search())
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(starts[1]["startUrls"]), 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(classify(result[0]), "")
        self.assertTrue(result[0]["description"].endswith("Zeta JV5, 1997."))

    def test_post_timeout_never_repeats_same_discovery_slot(self):
        posts = []
        def handler(request):
            posts.append(request)
            raise httpx.ReadTimeout("Uncertain POST", request=request)
        scraper = self.scraper(handler)
        asyncio.run(scraper.search())
        asyncio.run(scraper.search())
        self.assertEqual(len(posts), 1)
        with store() as conn:
            self.assertEqual(conn.execute("SELECT SUM(cap) FROM apify_jobs").fetchone()[0], 65000)

    def test_success_status_with_error_row_remains_partial(self):
        def handler(request):
            if "actor-runs" in request.url.path:
                return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset"}})
            return httpx.Response(200, json=[PIEDMONT_CARD, {"error": "no_items"}])
        scraper = self.scraper(handler)
        async def run():
            async with scraper.make_client() as client:
                return await scraper.read_run(client, "knownRun")
        self.assertEqual(len(asyncio.run(run())), 2)
        self.assertIn("partial coverage", scraper.health_error())

    def test_interrupted_run_resumes_without_new_paid_post(self):
        posts = []
        with store() as conn:
            conn.execute("INSERT INTO apify_jobs VALUES ('reservation','details','knownRun','2026-10',20000,0,0)")
            conn.commit()
        def handler(request):
            if request.method == "POST":
                posts.append(request)
            if "actor-runs" in request.url.path:
                return httpx.Response(200, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "dataset"}})
            return httpx.Response(200, json=[FENTON])
        result = asyncio.run(self.scraper(handler).search())
        self.assertEqual(posts, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], FENTON["itemUrl"])


if __name__ == "__main__":
    unittest.main()
