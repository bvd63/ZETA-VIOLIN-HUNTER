"""Native discovery cards must reach primary verification, never become alerts alone.

All HTTP is simulated and each test owns a temporary SQLite database.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import database
from config import Config
from discovery_queue import DiscoveryQueue
from offer_verifier import inspect_page
from scrapers.marktplaats import MarktplaatsScraper
from scrapers.kijiji import KijijiScraper
from scrapers.olx import OlxScraper


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return self.payload


class Client:
    def __init__(self, payload):
        self.response = Response(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, *args, **kwargs):
        return self.response


async def adapter_cards(kind, title, query='Zeta'):
    if kind == 'marktplaats':
        scraper = MarktplaatsScraper()
        card = {'itemId': '123456', 'title': title, 'description': '',
                'vipUrl': '/v/muziek-en-instrumenten/violen/m123456'}
        with patch('scrapers.marktplaats.market_queries', return_value=[query]), \
             patch('scrapers.marktplaats.SITES', [('Marktplaats', 'https://www.marktplaats.nl', 'Netherlands')]), \
             patch.object(scraper, 'make_client', return_value=Client({'listings': [card]})):
            return scraper, await scraper.search()
    if kind == 'kijiji':
        scraper = KijijiScraper()
        card = {'id': '123456', 'title': title, 'description': '', 'url': '/v-violin/123456'}
        state = {'props': {'pageProps': {'__APOLLO_STATE__': {'StandardListing:123456': card}}}}
        page = '<script id="__NEXT_DATA__">' + json.dumps(state) + '</script>'
        with patch('scrapers.kijiji.market_queries', return_value=[query]), \
             patch.object(scraper, 'make_client', return_value=Client(page)):
            return scraper, await scraper.search()
    scraper = OlxScraper()
    card = {'id': '123456', 'title': title, 'description': '',
            'url': 'https://www.olx.pl/d/oferta/violin-ID123456.html'}
    with patch('scrapers.olx.market_queries', return_value=[query]), \
         patch('scrapers.olx.SITES', [('OLX PL', 'www.olx.pl', 'Poland', 'pl')]), \
         patch.object(scraper, 'make_client', return_value=Client({'data': [card]})):
        return scraper, await scraper.search()


def product_page(title='Zeta Jazz Fusion JV-4 violin', body='Used Zeta 4 string electric violin for sale'):
    return f'<main><h1>{title}</h1><div id="description">{body}</div><p>Price €1500</p></main>'


class NativeRecallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, 'DB_PATH', str(Path(self.tmp.name) / 'native.db'))
        self.db_patch.start()
        import main
        self.main = main
        from price_tracker import PriceTracker
        self.db = database.Database()
        with DiscoveryQueue('search:setup'):
            pass
        self.prices = PriceTracker()

    async def asyncTearDown(self):
        self.prices.close()
        self.db.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    async def run_card(self, kind, title, page=None, state=None):
        scraper, cards = await adapter_cards(kind, title)
        self.assertEqual(len(cards), 1, f'{kind}: candidate not retained from query')
        scraper.search = AsyncMock(return_value=cards)
        async def verify(item, _http):
            if state:
                return 'unknown', state
            return inspect_page(item, page, 200, item['url'])
        with patch('main.verify_offer', side_effect=verify) as primary, patch.object(Config, 'SCRAPER_RETRIES', 0):
            result = await self.main._run_scraper_with_resilience(scraper, self.db, self.prices, asyncio.Semaphore(1))
        return scraper, cards[0], result, primary

    async def test_incomplete_brand_and_model_cards_are_verified(self):
        for kind, title in [('marktplaats', 'Zeta'), ('kijiji', 'JV-4'), ('olx', 'Zeta')]:
            with self.subTest(adapter=kind):
                self.db.conn.execute('DELETE FROM pending_alerts')
                self.db.conn.execute('DELETE FROM discovery_queue')
                self.db.conn.commit()
                scraper, card, result, primary = await self.run_card(kind, title, product_page())
                self.assertEqual(len(result['new']), 1)
                self.assertEqual([x['url'] for x in self.db.pending()], [card['url']])
                self.assertFalse(self.db.was_url_alerted(card['url']))

    async def test_generic_violin_returned_by_brand_query_reaches_primary(self):
        for kind in ('marktplaats', 'kijiji', 'olx'):
            with self.subTest(adapter=kind):
                self.db.conn.execute('DELETE FROM pending_alerts')
                self.db.conn.execute('DELETE FROM discovery_queue')
                self.db.conn.commit()
                scraper, card, result, primary = await self.run_card(kind, 'Electric violin', product_page())
                self.assertEqual(len(result['new']), 1)
                self.assertEqual([x['url'] for x in self.db.pending()], [card['url']])

    async def test_generic_violin_from_unrelated_query_is_not_broadly_admitted(self):
        for kind in ('marktplaats', 'kijiji', 'olx'):
            with self.subTest(adapter=kind):
                _, cards = await adapter_cards(kind, 'Electric violin', query='Yamaha')
                self.assertEqual(cards, [])

    async def test_primary_accessory_album_or_other_product_never_enters_outbox(self):
        examples = [('Zeta pickup', 'Zeta violin pickup for sale'),
                    ('Jean-Luc Ponty CD', 'Zeta violin music album for sale'),
                    ('Zeta coffee table', 'Wood coffee table for sale')]
        for kind, (title, body) in zip(('marktplaats', 'kijiji', 'olx'), examples):
            with self.subTest(adapter=kind):
                self.db.conn.execute('DELETE FROM pending_alerts')
                self.db.conn.execute('DELETE FROM discovery_queue')
                self.db.conn.commit()
                _, _, result, primary = await self.run_card(kind, 'Zeta', product_page(title, body))
                primary.assert_awaited_once()
                self.assertEqual(result['new'], [])
                self.assertEqual(self.db.pending(), [])

    async def test_original_pickup_does_not_hide_a_complete_instrument(self):
        pages = [
            product_page('Zeta Jazz Fusion JV5 electric violin',
                         'Used five string Zeta violin for sale with original pickup included.'),
            product_page('Zeta pickup',
                         'Used Zeta 4 string electric violin for sale. Original pickup included.'),
        ]
        for kind, page in zip(('marktplaats', 'kijiji'), pages):
            with self.subTest(adapter=kind):
                self.db.conn.execute('DELETE FROM pending_alerts')
                self.db.conn.execute('DELETE FROM discovery_queue')
                self.db.conn.commit()
                _, card, result, primary = await self.run_card(kind, 'Zeta', page)
                primary.assert_awaited_once()
                self.assertEqual(len(result['new']), 1)
                self.assertEqual([x['url'] for x in self.db.pending()], [card['url']])

    async def test_vague_pickup_title_with_complete_violin_in_same_sentence(self):
        page = product_page('Zeta pickup',
                            'Used Zeta 4 string electric violin for sale with original pickup included.')
        _, card, result, primary = await self.run_card('marktplaats', 'Zeta', page)
        primary.assert_awaited_once()
        self.assertEqual(len(result['new']), 1)
        self.assertEqual([x['url'] for x in self.db.pending()], [card['url']])

    async def test_pickup_card_requires_primary_identity(self):
        pages = [
            (product_page('Zeta pickup',
                          'Used Zeta 4 string electric violin for sale with original pickup included.'), True),
            (product_page('Zeta pickup', 'Zeta violin pickup for sale'), False),
        ]
        for page, eligible in pages:
            with self.subTest(eligible=eligible):
                self.db.conn.execute('DELETE FROM pending_alerts')
                self.db.conn.execute('DELETE FROM discovery_queue')
                self.db.conn.commit()
                _, card, result, primary = await self.run_card('marktplaats', 'Zeta pickup', page)
                primary.assert_awaited_once()
                self.assertEqual(len(result['new']), int(eligible))
                self.assertEqual([x['url'] for x in self.db.pending()], [card['url']] if eligible else [])

    async def test_blocked_pickup_card_is_durable_not_an_alert(self):
        scraper, card, result, primary = await self.run_card('marktplaats', 'Zeta pickup', state='HTTP 403')
        primary.assert_awaited_once()
        self.assertEqual(result['new'], [])
        self.assertEqual(self.db.pending(), [])
        self.assertFalse(self.db.is_seen(card['id']))
        self.assertFalse(self.db.was_url_alerted(card['url']))
        self.db.close()
        self.db = database.Database()
        with DiscoveryQueue('search:' + scraper.name) as queue:
            queue.conn.execute('UPDATE discovery_queue SET next_attempt=0')
            queue.conn.commit()
            saved = queue.claim(1)
        self.assertEqual([x['url'] for x in saved], [card['url']])
        self.assertTrue(saved[0]['_direct_item_candidate'])

    async def test_inaccessible_weak_card_survives_restart_without_alert_or_seen(self):
        for kind in ('marktplaats', 'kijiji', 'olx'):
            with self.subTest(adapter=kind):
                self.db.conn.execute('DELETE FROM discovery_queue')
                self.db.conn.commit()
                failure = 'HTTP 403' if kind != 'kijiji' else 'timeout'
                card_title = 'Electric violin' if kind == 'kijiji' else 'Zeta'
                scraper, card, result, primary = await self.run_card(kind, card_title, state=failure)
                primary.assert_awaited_once()
                self.assertEqual(result['new'], [])
                self.assertEqual(self.db.pending(), [])
                self.assertFalse(self.db.is_seen(card['id']))
                self.assertFalse(self.db.was_url_alerted(card['url']))
                self.db.close()
                self.db = database.Database()  # restart/read the durable queue
                namespace = 'search:' + scraper.name
                with DiscoveryQueue(namespace) as queue:
                    queue.conn.execute('UPDATE discovery_queue SET next_attempt=0 WHERE namespace=?', (namespace,))
                    queue.conn.commit()
                    saved = queue.claim(1)
                self.assertEqual([x['url'] for x in saved], [card['url']])
                self.assertEqual(saved[0]['source'], 'search')
                self.assertTrue(saved[0]['_direct_item_candidate'])
                self.assertEqual(saved[0]['_native_card_source'], scraper.name)
                self.assertEqual(saved[0]['_native_search_query'], 'Zeta')
                self.assertEqual(saved[0]['_query_identity_unverified'], card_title == 'Electric violin')
                async def verify(item, _http):
                    return inspect_page(item, product_page(), 200, item['url'])
                with patch('main.verify_offer', side_effect=verify):
                    recovered = await self.main._verify_search_hits(saved, self.db, None, {}, namespace)
                self.assertEqual(len(recovered), 1)
                self.assertEqual([x['url'] for x in self.db.pending()], [card['url']])
                self.db.discard_pending(card['url'])


if __name__ == '__main__':
    unittest.main()
