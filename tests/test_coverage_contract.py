"""Owner contract: priorities supplement the historical coverage, not replace it.

The fixture was extracted from 61e0729, before October 25 changes. No HTTP.
"""
import json
import asyncio
from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(TEMP.name) / 'coverage.db')
from config import Config
import database
database.DB_PATH = os.environ['DB_PATH']
from keywords import MODEL_CODES, MODEL_NAMES, STRING_VARIANTS, VIOLIN_WORDS
from scrapers.apify_facebook import regional_plan
from scrapers.brave import BraveScraper
from scrapers.google import SITE_GROUPS, PRIORITY_QUERIES, MATRIX_KEYWORDS

BASE = json.loads((Path(__file__).parent / 'fixtures/coverage_before_2026_10_25.json').read_text())


class CoverageContractTests(unittest.TestCase):
    def test_removed_source_policy_keeps_other_japanese_marketplaces(self):
        from source_policy import is_disabled_source
        from filters import classify
        for url in ('https://auctions.yahoo.co.jp/jp/auction/a123',
                    'https://page.auctions.yahoo.co.jp/jp/auction/a123',
                    'https://paypayfleamarket.yahoo.co.jp/item/a123',
                    'https://buyee.jp/item/yahoo/auction/a123',
                    'https://zenmarket.jp/en/auction.aspx?itemCode=a123'):
            with self.subTest(url=url):
                self.assertTrue(is_disabled_source(url))
                self.assertEqual(classify({'title':'Zeta JLP5 violin', 'url':url}), 'disabled_source')
        for url in ('https://jp.mercari.com/item/m123', 'https://rakuten.co.jp/item/123',
                    'https://www.digimart.net/item/123', 'https://buyee.jp/mercari/item/m123',
                    'https://yahoo.co.jp.example/item/123', 'https://example.com/item/yahoo'):
            self.assertFalse(is_disabled_source(url), url)

    def test_removed_source_is_rejected_before_detail_network(self):
        from offer_verifier import verify_offer
        calls = []
        async def check():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda r: calls.append(r) or httpx.Response(200))) as client:
                state, _ = await verify_offer({'url':'https://auctions.yahoo.co.jp/jp/auction/a123'}, client)
                self.assertEqual(state, 'non_sale')
        asyncio.run(check())
        self.assertEqual(calls, [])

    def test_old_queue_retains_history_without_consuming_remaining_sources_slots(self):
        from discovery_queue import DiscoveryQueue
        blocked = {'url':'https://auctions.yahoo.co.jp/jp/auction/a123', 'title':'Zeta violin'}
        keep = {'url':'https://jp.mercari.com/item/m123', 'title':'Zeta violin'}
        with DiscoveryQueue('owner-removal-regression') as queue:
            # Simulate records written before the owner's new exclusion.
            with patch('discovery_queue.is_disabled_source', return_value=False):
                queue.put([blocked, keep])
            selected = queue.claim(1)
            self.assertEqual([x['url'] for x in selected], [keep['url']])
            row = queue.conn.execute('SELECT attempts,next_attempt FROM discovery_queue WHERE namespace=? AND key=?',
                                      (queue.namespace, blocked['url'])).fetchone()
            self.assertEqual(row[0], 0)
            self.assertGreater(row[1], 200000000000)
            self.assertEqual(queue.conn.execute('SELECT count(*) FROM discovery_queue WHERE namespace=?',
                                               (queue.namespace,)).fetchone()[0], 2)
            queue.put([blocked])  # A new global-index hit cannot reactivate it.
            self.assertEqual(queue.claim(1), [])

    def test_retired_stats_are_not_current_coverage_and_schedule_is_unchanged(self):
        from status_tracker import StatusTracker
        tracker = StatusTracker()
        try:
            tracker.record_scraper('Yahoo Auctions JP', 0, 0, error='Historical 404')
            tracker.record_scraper('Mercari JP', 1, 0)
            state = tracker.get_status()
            self.assertNotIn('Yahoo Auctions JP', state['scrapers'])
            self.assertIn('Mercari JP', state['scrapers'])
            self.assertEqual(state['schedule'], {'hours':'10,22', 'timezone':'Europe/Bucharest'})
        finally:
            tracker.close()

    def test_only_owner_removed_yahoo_is_absent(self):
        from main import build_scrapers
        with patch.object(Config, 'APIFY_TOKEN', ''):
            current = {type(s).__name__ for s in build_scrapers()}
        self.assertLessEqual(set(BASE['sources']) - {'YahooAuctionsJPScraper'}, current)
        self.assertNotIn('YahooAuctionsJPScraper', current)
        self.assertTrue({'TavilyScraper', 'ExaScraper'} <= current)

    def test_models_languages_strings_and_site_groups_retained(self):
        self.assertLessEqual(set(BASE['codes']), set(MODEL_CODES))
        self.assertEqual(BASE['names'], list(MODEL_NAMES))
        self.assertEqual(BASE['strings'], list(STRING_VARIANTS))
        self.assertLessEqual(BASE['languages'].items(), VIOLIN_WORDS.items())
        expected = [g.replace('site:auctions.yahoo.co.jp OR site:paypayfleamarket.yahoo.co.jp OR ', '')
                    for g in BASE['site_groups']]
        self.assertEqual(expected, SITE_GROUPS)

    def test_apify_queries_not_replaced_by_priorities(self):
        for cursor, expected in BASE['apify_samples'].items():
            self.assertEqual(regional_plan(int(cursor)), expected)

    def test_direct_market_queries_preserve_original_dates_languages_and_slots(self):
        from keywords import market_queries
        for sample in BASE['direct_query_samples']:
            args = {k:sample[k] for k in ('broad','limit')}
            current = market_queries(sample['language'], now=datetime.fromisoformat(sample['now']), **args)
            self.assertEqual(current, sample['expected'], sample)

    def test_brave_retains_fixed_queries_and_original_matrix_cadence(self):
        # OR-added model aliases widen these five auction queries.
        clean = lambda q: q.replace(' OR JV5 OR JLP4', '')
        self.assertEqual(BASE['priority_queries'], [clean(q) for q in PRIORITY_QUERIES])
        self.assertEqual(BASE['web'], [clean(q) for q in MATRIX_KEYWORDS])
        with patch.object(Config, 'BRAVE_QUERIES_PER_RUN', 16):
            seen = []
            cursor = 0
            for _ in range(20):
                queries, cursor = BraveScraper().plan_queries(cursor)
                self.assertEqual(len(queries), 16)
                self.assertTrue(set(PRIORITY_QUERIES) <= {q for q, _ in queries})
                self.assertTrue(all('freshness' not in p for _, p in queries))
                seen.extend(clean(q) for q, _ in queries[10:])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), len(BASE['web']) * len(BASE['site_groups']))


if __name__ == '__main__':
    unittest.main()
