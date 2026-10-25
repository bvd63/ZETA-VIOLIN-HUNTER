"""Owner contract: priorities supplement the historical coverage, not replace it.

The fixture was extracted from 61e0729, before October 25 changes. No HTTP.
"""
import json
from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
    def test_no_historical_source_removed(self):
        from main import build_scrapers
        with patch.object(Config, 'APIFY_TOKEN', ''):
            current = {type(s).__name__ for s in build_scrapers()}
        self.assertLessEqual(set(BASE['sources']), current)
        self.assertTrue({'TavilyScraper', 'ExaScraper'} <= current)

    def test_models_languages_strings_and_site_groups_retained(self):
        self.assertLessEqual(set(BASE['codes']), set(MODEL_CODES))
        self.assertEqual(BASE['names'], list(MODEL_NAMES))
        self.assertEqual(BASE['strings'], list(STRING_VARIANTS))
        self.assertLessEqual(BASE['languages'].items(), VIOLIN_WORDS.items())
        self.assertEqual(BASE['site_groups'], SITE_GROUPS)

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
