"""Offline tests for query provenance and native-card primary verification."""
import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import database
from config import Config
from offer_verifier import inspect_page
from scrapers.mercari_jp import MercariJPScraper
from scrapers.shopify_dealers import ShopifyDealersScraper


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class ShopifyClient:
    def __init__(self, collection, suggestions):
        self.collection, self.suggestions = collection, suggestions
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, params):
        self.requests.append((url, params))
        if '/collections/' in url:
            return Response({'products': self.collection})
        return Response({'resources': {'results': {'products': self.suggestions.get(params['q'], [])}}})


def product(pid='1', title='Electric violin', body='', **extra):
    return {'id': pid, 'title': title, 'handle': 'violin-' + pid, 'body_html': body, **extra}


class NativeAsiaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, 'DB_PATH', str(Path(self.tmp.name) / 'test.db'))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    async def mercari(self, mapping):
        class Mercapi:
            async def search(self, query):
                return types.SimpleNamespace(items=mapping.get(query, []))
        scraper = MercariJPScraper()
        with patch.dict(sys.modules, {'mercapi': types.SimpleNamespace(Mercapi=Mercapi)}), \
             patch('scrapers.mercari_jp.market_queries', return_value=list(mapping)):
            return scraper, await scraper.search()

    async def shopify(self, collection, suggestions):
        scraper = ShopifyDealersScraper()
        client = ShopifyClient(collection, suggestions)
        with patch('scrapers.shopify_dealers.STORES', [('Dealer', 'https://example.com', 'USA', ['used'], 'Used')]), \
             patch('scrapers.shopify_dealers.market_queries', return_value=list(suggestions)), \
             patch('scrapers.shopify_dealers.REQUEST_GAP_SECONDS', 0), \
             patch.object(scraper, 'make_client', return_value=client):
            cards = await scraper.search()
        return scraper, cards, client

    async def test_mercari_uses_actual_query_and_excludes_sold(self):
        item = types.SimpleNamespace(id_='m123456', name='Electric violin', status='on_sale', price=100000)
        sold = types.SimpleNamespace(id_='m123457', name='Zeta violin', status='ITEM_STATUS_SOLD_OUT', price=100000)
        _, cards = await self.mercari({'Yamaha': [item], 'ゼータ バイオリン': [item, sold]})
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['_native_search_query'], 'ゼータ バイオリン')
        self.assertTrue(cards[0]['_query_identity_unverified'])

    async def test_shopify_queries_belong_to_matching_suggestions_only(self):
        _, cards, client = await self.shopify(
            [product('1'), product('2', 'Zeta violin', 'Used Zeta violin')],
            {'Yamaha': [product('3')], 'Zeta': [product('4')]})
        self.assertEqual({c['url'].rsplit('/', 1)[-1] for c in cards}, {'violin-2', 'violin-4'})
        found = next(c for c in cards if c['url'].endswith('violin-4'))
        self.assertTrue(found['_query_identity_unverified'])
        self.assertEqual(found['_native_search_query'], 'Zeta')
        self.assertEqual(len(client.requests), 3)

    async def test_duplicate_preserves_full_collection_and_all_queries(self):
        original = product('1', 'Electric violin', 'Used Zeta Jazz Fusion violin in good condition', tags=['used'])
        _, cards, _ = await self.shopify([original], {'Yamaha': [product('1')], 'Zeta': [product('1')]})
        self.assertEqual(len(cards), 1)
        self.assertIn('Zeta Jazz Fusion', cards[0]['description'])
        self.assertEqual(cards[0]['condition'], 'Used')
        self.assertEqual(cards[0]['_native_search_queries'], ['Yamaha', 'Zeta'])
        self.assertFalse(cards[0]['_query_identity_unverified'])

    async def test_collection_tag_hint_is_unverified_and_not_a_search_query(self):
        import main
        from price_tracker import PriceTracker
        from discovery_queue import DiscoveryQueue
        scraper, cards, _ = await self.shopify(
            [product('1', tags=['zeta']), product('2'), product('3', 'Amplifier', tags=['zeta'])], {})
        tagged = next(c for c in cards if c['url'].endswith('violin-1'))
        self.assertFalse(any(c['url'].endswith('violin-2') for c in cards))
        self.assertTrue(tagged['_query_identity_unverified'])
        self.assertEqual(tagged['_native_identity_hint'], 'product tags')
        self.assertNotIn('_native_search_query', tagged)
        unrelated = next(c for c in cards if c['url'].endswith('violin-3'))
        self.assertNotIn('_query_identity_unverified', unrelated)
        scraper.search = AsyncMock(return_value=[tagged])
        db, prices = database.Database(), PriceTracker()
        try:
            with patch('main.verify_offer', AsyncMock(return_value=('unknown', 'HTTP 403'))), \
                 patch.object(Config, 'SCRAPER_RETRIES', 0):
                result = await main._run_scraper_with_resilience(scraper, db, prices, asyncio.Semaphore(1))
            self.assertEqual(result['new'], [])
            self.assertEqual(db.pending(), [])
            with DiscoveryQueue('search:' + scraper.name) as queue:
                queue.conn.execute('UPDATE discovery_queue SET next_attempt=0 WHERE namespace=?', (queue.namespace,))
                queue.conn.commit()
                saved = queue.claim(1)
            self.assertEqual(saved[0]['_native_identity_hint'], 'product tags')
            self.assertNotIn('_native_search_query', saved[0])
            page = '<main><h1>Zeta Jazz violin</h1><div id="description">Used Zeta violin</div><p>For sale $1500</p></main>'
            async def verify(listing, http):
                return inspect_page(listing, page, 200, listing['url'])
            with patch('main.verify_offer', side_effect=verify):
                recovered = await main._verify_search_hits(saved, db, None, {}, 'search:' + scraper.name)
            self.assertEqual(len(recovered), 1)
        finally:
            prices.close()
            db.close()

    async def test_weak_cards_require_primary_identity_and_survive_block(self):
        import main
        from price_tracker import PriceTracker
        from discovery_queue import DiscoveryQueue
        cases = []
        item = types.SimpleNamespace(id_='m999999', name='Electric violin', status='on_sale', price=100000)
        cases.append(await self.mercari({'Zeta': [item]}))
        dealer, cards, _ = await self.shopify([], {'Zeta': [product('4')]})
        cases.append((dealer, cards))
        db, prices = database.Database(), PriceTracker()
        try:
            for scraper, cards in cases:
                scraper.search = AsyncMock(return_value=cards)
                with patch('main.verify_offer', AsyncMock(return_value=('unknown', 'HTTP 403'))), \
                     patch.object(Config, 'SCRAPER_RETRIES', 0):
                    result = await main._run_scraper_with_resilience(scraper, db, prices, asyncio.Semaphore(1))
                self.assertEqual(result['new'], [])
                self.assertEqual(db.pending(), [])
                with DiscoveryQueue('search:' + scraper.name) as queue:
                    queue.conn.execute('UPDATE discovery_queue SET next_attempt=0 WHERE namespace=?', (queue.namespace,))
                    queue.conn.commit()
                    saved = queue.claim(1)
                self.assertEqual(saved[0]['_native_search_query'], 'Zeta')
                self.assertTrue(saved[0]['_query_identity_unverified'])
                page = '<main><h1>Zeta Jazz violin</h1><div id="description">Used Zeta violin</div><p>For sale $1500</p></main>'
                async def verify(listing, http):
                    return inspect_page(listing, page, 200, listing['url'])
                with patch('main.verify_offer', side_effect=verify):
                    recovered = await main._verify_search_hits(saved, db, None, {}, 'search:' + scraper.name)
                self.assertEqual(len(recovered), 1)
                db.discard_pending(cards[0]['url'])
        finally:
            prices.close()
            db.close()


if __name__ == '__main__':
    unittest.main()
