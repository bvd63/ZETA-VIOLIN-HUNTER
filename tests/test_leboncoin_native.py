"""Real missed offer and native discovery/primary negative/queue contracts."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from filters import classify
from keywords import market_queries
from leboncoin_api import cached_offer, closed_reason, remember
from leboncoin_native import NativeClient, normalize_native, search_payload
from offer_verifier import verify_offer
from scrapers.leboncoin_native import LeboncoinNativeScraper

ROW = json.loads((Path(__file__).parent / 'fixtures/leboncoin_native_3269447854.json').read_text())
URL = ROW['url']


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch('database.DB_PATH', str(Path(self.temp.name) / 'native.db'))
        self.db.start()

    def tearDown(self):
        self.db.stop()
        self.temp.cleanup()

    def test_actual_missed_violin_is_eligible_with_full_body_price_condition_and_no_age_gate(self):
        item = normalize_native(ROW, primary=True, expected_id='3269447854')
        self.assertEqual(classify(item), '')
        self.assertEqual(item['description'], ROW['body'])
        self.assertEqual(item['price'], '3500 EUR')
        self.assertEqual(item['condition'], 'État satisfaisant')
        self.assertEqual(item['_leboncoin_observed_state'], 'live')
        self.assertEqual(item['date_posted'], '2026-09-14')

    def test_active_search_card_is_never_primary_or_available_evidence(self):
        item = normalize_native(ROW)
        self.assertEqual(item['_leboncoin_observed_state'], 'unknown')
        self.assertFalse(item['_leboncoin_primary_checked'])

    def test_wrong_id_category_intent_host_credentials_and_missing_primary_body_are_invalid(self):
        for changes in ({'list_id': True}, {'list_id': 123}, {'category_id': '12'},
                        {'ad_type': 'request'}, {'url': URL.replace('.fr/', '.fr.evil.example/')},
                        {'url': URL.replace('www.', 'user:secret@www.')}, {'body': None}, {'body': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(normalize_native({**ROW, **changes}, primary=True), {})
        self.assertEqual(normalize_native(ROW, primary=True, expected_id='123'), {})

    def test_physical_state_and_missing_or_unrecognized_status_never_imply_availability(self):
        for status in ('', 'unknown', None):
            item = normalize_native({**ROW, 'status': status, 'state': 'active',
                                     'attributes': [{'key': 'state', 'value': 'active'}]}, primary=True)
            self.assertEqual(item['_leboncoin_observed_state'], 'unknown')

    def test_explicit_negative_overrides_active_and_details_are_bound(self):
        for field in ('sold', 'is_sold', 'deleted', 'expired'):
            self.assertEqual(normalize_native({**ROW, field: True}, primary=True)['_leboncoin_observed_state'], 'dead')
        for changes in ({'status': 'reserved'}, {'pending': True}, {'is_live': False}):
            self.assertEqual(normalize_native({**ROW, **changes}, primary=True)['_leboncoin_observed_state'], 'held')
        self.assertEqual(normalize_native({**ROW, 'sold': 'true'}, primary=True)['_leboncoin_observed_state'], 'unknown')

    def test_nonfinite_boolean_or_ambiguous_prices_are_not_currency_amounts(self):
        for price in ([True], [float('nan')], [float('inf')], [-1], [1, 3500], '3500'):
            self.assertEqual(normalize_native({**ROW, 'price': price})['price'], 'N/A')

    def test_whole_france_full_body_and_all_original_terms(self):
        for term in market_queries('fr', broad=True, limit=8):
            payload = search_payload(term)
            self.assertEqual(payload['filters']['keywords'], {'text': term})
            self.assertEqual(payload['filters']['location'], {})
            self.assertNotIn('title_only', payload['filters']['keywords'])
            self.assertNotIn('date', str(payload))

    def test_matching_primary_refresh_populates_cache_and_existing_verifier(self):
        item = {'url': URL, 'title': 'Instrument', 'source': 'search'}
        async def refresh(url):
            remember(normalize_native(ROW, primary=True, expected_id='3269447854'))
        async def run():
            with patch('leboncoin_native.refresh_primary', side_effect=refresh):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail('No blocked HTML after valid primary'))) as client:
                    return await verify_offer(item, client)
        state, why = asyncio.run(run())
        self.assertEqual(state, 'live'); self.assertIn('API mobil public', why)
        self.assertTrue(item['_primary_content_checked'])
        self.assertEqual(item['description'], ROW['body'])
        self.assertEqual(classify(item), '')

    def test_new_positive_primary_can_reopen_old_closed_but_failed_refresh_preserves_negative(self):
        remember(normalize_native({**ROW, 'status': 'sold'}, primary=True), checked=time.time()-7*3600)
        async def run(failed):
            async def refresh(url):
                if failed: raise RuntimeError('403')
                remember(normalize_native(ROW, primary=True))
            with patch('leboncoin_native.refresh_primary', side_effect=refresh):
                async with httpx.AsyncClient() as client:
                    return await verify_offer({'url': URL}, client)
        self.assertEqual(asyncio.run(run(True))[0], 'dead')
        self.assertTrue(closed_reason(URL))
        self.assertEqual(asyncio.run(run(False))[0], 'live')
        self.assertFalse(closed_reason(URL))

    def test_primary_unknown_keeps_identity_but_reserved_blocks_alert_route(self):
        for status, conflict in (('unknown', False), ('reserved', True)):
            remember(normalize_native({**ROW, 'status': status}, primary=True))
            item = {'url': URL}
            async def run():
                async with httpx.AsyncClient() as client: return await verify_offer(item, client)
            self.assertEqual(asyncio.run(run())[0], 'unknown')
            self.assertEqual(bool(item.get('_availability_conflict')), conflict)
            if not conflict: self.assertTrue(item['_primary_content_checked'])

    def test_individual_404_is_terminal_403_is_unknown_and_wrong_id_does_not_cache(self):
        async def run(status, row=ROW):
            client = NativeClient()
            class Response:
                def json(self): return row
            async def request(*args, **kwargs):
                if status != 200: raise RuntimeError('Leboncoin native HTTP '+str(status))
                return Response()
            client.request = request
            return await client.primary(URL)
        with self.assertRaises(RuntimeError): asyncio.run(run(403))
        self.assertFalse(closed_reason(URL))
        with self.assertRaises(ValueError): asyncio.run(run(200, {**ROW, 'list_id': 123}))
        self.assertIsNone(cached_offer(URL))
        self.assertIsNone(asyncio.run(run(404)))
        self.assertTrue(closed_reason(URL))

    def test_challenge_or_unexpected_empty_search_is_not_healthy_zero(self):
        async def run(data):
            client = NativeClient()
            class Response:
                def json(self): return data
            async def request(*args, **kwargs): return Response()
            client.request = request
            return await client.search('zeta')
        for data in ({}, {'ads': [], 'total': True}, {'ads': [], 'total': 3}, {'ads': {}, 'total': 0}):
            with self.assertRaises(ValueError): asyncio.run(run(data))
        self.assertEqual(asyncio.run(run({'ads': [], 'total': 0})), ([], 0))

    def _collector(self, *, fail=False, paginated=False):
        calls = []
        class FakeClient:
            def __init__(self, observer): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def search(self, keyword, offset=0):
                calls.append((keyword, offset))
                if fail: raise RuntimeError('Leboncoin native HTTP 403')
                return [copy.deepcopy(ROW)], 100 if paginated else 1
        async def run():
            with patch('scrapers.leboncoin_native.NativeClient', FakeClient), patch('scrapers.leboncoin_native.asyncio.sleep'):
                scraper = LeboncoinNativeScraper()
                return scraper, await scraper.search()
        return *asyncio.run(run()), calls

    def test_actual_broad_search_card_is_deduplicated_preserved_and_requires_primary(self):
        scraper, items, calls = self._collector()
        self.assertEqual([q for q, _ in calls], market_queries('fr', broad=True, limit=8))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['url'], URL)
        self.assertEqual(items[0]['source'], 'search')
        self.assertTrue(items[0]['_query_identity_unverified'])
        self.assertIsNone(cached_offer(URL))
        self.assertEqual(scraper.discovery_queries_attempted, len(calls))

    def test_older_pages_rotate_durably_while_newest_is_always_refreshed(self):
        first, _, calls = self._collector(paginated=True)
        self.assertEqual([o for _, o in calls], [0, 35] * len(market_queries('fr', broad=True, limit=8)))
        self.assertIn('partial inventory', first.health_error())
        _, _, calls = self._collector(paginated=True)
        self.assertEqual([o for _, o in calls], [0, 70] * len(market_queries('fr', broad=True, limit=8)))
        _, _, calls = self._collector(paginated=True)
        self.assertEqual([o for _, o in calls], [0, 35] * len(market_queries('fr', broad=True, limit=8)))

    def test_blocked_first_search_stops_repeated_blocked_posts_and_reports_missing_terms(self):
        scraper, items, calls = self._collector(fail=True)
        self.assertEqual(items, []); self.assertEqual(len(calls), 1)
        self.assertIn('coverage incomplete', scraper.health_error())


if __name__ == '__main__': unittest.main()
