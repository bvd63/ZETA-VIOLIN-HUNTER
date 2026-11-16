"""RO context really hid US Strados offers; preserve it and add protected global search."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import database
from provider_state import read, reserve_work, store
from scrapers.ebay import EbayScraper, MARKETPLACES, MAX_PAGES
from work_context import spending_scope

ROW = {'itemId': 'v1|840131807941|0', 'itemWebUrl': 'https://www.ebay.com/itm/840131807941',
       'title': 'Custom Green Zeta Strados Modern Electric Violin', 'condition': 'Used',
       'shippingOptions': [{'shippingCost': {'value': '10', 'currency': 'USD'}}]}


class GlobalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(database, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.dbpatch.start()

    async def asyncTearDown(self):
        self.dbpatch.stop(); self.temp.cleanup()

    async def run_search(self, handler, *, scope='auxiliary', queries=None, markets=None):
        scraper = EbayScraper('fixture', 'fixture-secret')
        original = scraper.make_client
        with patch.object(scraper, 'make_client', side_effect=lambda **kw:
                          original(transport=httpx.MockTransport(handler), **kw)), \
             patch('scrapers.ebay.market_queries', return_value=queries or ['zeta', 'strados']), \
             patch('scrapers.ebay.MARKETPLACES', markets or ['EBAY_US', 'EBAY_GB']), spending_scope(scope):
            return scraper, await scraper.search()

    @staticmethod
    def oauth(request):
        return request.url.path.endswith('/oauth2/token')

    async def test_real_shipping_context_omission_gets_global_candidate_with_unknown_ro_shipping(self):
        calls = []
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            calls.append(request)
            return httpx.Response(200, json={'total': 0} if 'X-EBAY-C-ENDUSERCTX' in request.headers else {'total': 1, 'itemSummaries': [ROW]})
        scraper, items = await self.run_search(handler)
        self.assertEqual(len(items), 1); self.assertEqual(items[0]['url'], ROW['itemWebUrl'])
        self.assertIsNone(items[0]['ships_to_ro'])  # US shipping options do NOT mean Romania delivery
        self.assertEqual(len(calls), 8)
        self.assertTrue(all('X-EBAY-C-ENDUSERCTX' in c.headers for c in calls[:4]))
        self.assertTrue(all('X-EBAY-C-ENDUSERCTX' not in c.headers for c in calls[4:]))
        self.assertEqual(scraper.coverage_stats['contexts']['RO']['attempts'], 4)
        self.assertEqual(scraper.coverage_stats['contexts']['global']['attempts'], 4)
        self.assertEqual(scraper.discovery_queries_attempted, 8)

    async def test_every_original_query_market_page_and_header_is_preserved(self):
        calls = []
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            calls.append(request)
            return httpx.Response(200, json={'total': 0})
        _, items = await self.run_search(handler)
        expected = [(market, q) for market in ('EBAY_US', 'EBAY_GB') for q in ('zeta', 'strados')]
        for subset in (calls[:4], calls[4:]):
            self.assertEqual([(r.headers['X-EBAY-C-MARKETPLACE-ID'], r.url.params['q']) for r in subset], expected)
            for r in subset:
                self.assertEqual(dict(r.url.params), {'q': r.url.params['q'], 'limit': '50', 'offset': '0',
                    'fieldgroups': 'EXTENDED', 'sort': 'newlyListed', 'filter': 'buyingOptions:{FIXED_PRICE|AUCTION}', 'category_ids': '619'})
        self.assertEqual(items, [])
        self.assertEqual(MAX_PAGES, 2); self.assertEqual(len(MARKETPLACES), 16)

    async def test_global_aux_guard_never_consumes_scheduled_guard_or_skips_original_plan(self):
        calls = []
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            calls.append('RO' if 'X-EBAY-C-ENDUSERCTX' in request.headers else 'global')
            return httpx.Response(200, json={'total': 0})
        await self.run_search(handler, queries=['zeta'], markets=['EBAY_US'])
        self.assertEqual(calls, ['RO', 'global'])
        self.assertIsNone(read('ebay:global:last_run'))
        calls.clear()
        scraper, _ = await self.run_search(handler, queries=['zeta'], markets=['EBAY_US'])
        self.assertEqual(calls, ['RO'])
        self.assertIn('guarded', scraper.coverage_stats['contexts']['global']['state'])
        calls.clear()
        await self.run_search(handler, scope='core', queries=['zeta'], markets=['EBAY_US'])
        self.assertEqual(calls, ['RO', 'global'])

    async def test_duplicate_ro_item_keeps_original_ro_shipping_observation(self):
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            row = {**ROW, 'shippingOptions': []} if 'X-EBAY-C-ENDUSERCTX' in request.headers else ROW
            return httpx.Response(200, json={'total': 1, 'itemSummaries': [row]})
        _, items = await self.run_search(handler)
        self.assertEqual(len(items), 1)
        self.assertIs(items[0]['ships_to_ro'], False)

    async def test_global_rate_limit_stops_extras_but_original_ro_queries_are_complete(self):
        contexts = []
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            ro = 'X-EBAY-C-ENDUSERCTX' in request.headers
            contexts.append('RO' if ro else 'global')
            return httpx.Response(200, json={'total': 0}) if ro else httpx.Response(429)
        scraper, _ = await self.run_search(handler)
        self.assertEqual(contexts, ['RO'] * 4 + ['global'])
        self.assertIn('HTTP429', scraper.health_error())

    async def test_reservation_refusal_is_not_an_api_attempt_or_missing_ro_search(self):
        calls = []
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            calls.append(request)
            return httpx.Response(200, json={'total': 0})
        with patch('scrapers.ebay.reserve_work', return_value=False):
            scraper, _ = await self.run_search(handler)
        self.assertEqual(len(calls), 4)
        self.assertEqual(scraper.coverage_stats['contexts']['global']['attempts'], 0)
        self.assertIn('allowance', scraper.health_error())

    async def test_generic_violin_card_keeps_query_provenance_for_primary_identity(self):
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            return httpx.Response(200, json={'total': 1, 'itemSummaries': [{**ROW, 'title': 'Electric violin'}]})
        _, items = await self.run_search(handler, queries=['zeta'], markets=['EBAY_US'])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['_native_search_query'], 'zeta')
        self.assertTrue(items[0]['_query_identity_unverified'])

    async def test_bad_row_does_not_hide_later_instrument_and_challenge_is_not_healthy_zero(self):
        def handler(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            return httpx.Response(200, json={'itemSummaries': [None, ROW]})
        scraper, items = await self.run_search(handler, queries=['zeta'], markets=['EBAY_US'])
        self.assertEqual(len(items), 1)
        self.assertIn('invalid row', scraper.health_error())
        def challenge(request):
            if self.oauth(request): return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            return httpx.Response(200, json={'challenge': True})
        scraper, items = await self.run_search(challenge, queries=['zeta'], markets=['EBAY_US'])
        self.assertEqual(items, [])
        self.assertIn('schema', scraper.health_error())

    def test_new_extra_budget_preserves_full_core_allowance_without_touching_old_quotas(self):
        with store(): pass
        reserve = lambda aux: reserve_work('ebay_global', 1, 39680, 640, 19840, auxiliary=aux)
        self.assertEqual(sum(reserve(True) for _ in range(650)), 640)
        self.assertEqual(sum(reserve(False) for _ in range(650)), 640)
        with store() as c:
            self.assertEqual(c.execute("SELECT sum(units) FROM provider_reservations WHERE provider='ebay_global'").fetchone()[0], 1280)
            self.assertEqual(c.execute("SELECT count(*) FROM provider_reservations WHERE provider IN ('brave','serper','google')").fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
