"""Native card hints retain only bounded query-related leads. No network calls."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import database
from config import Config
from filters import classify
from offer_verifier import inspect_page
from scrapers.subito import SubitoScraper
from scrapers.schibsted import SchibstedScraper
from scrapers.hibid import HiBidScraper
from scrapers.shopgoodwill import ShopGoodwillScraper


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
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def get(self, *args, **kwargs): return self.response
    async def post(self, *args, **kwargs): return self.response


async def get_cards(kind, title, query='Zeta', *, description='', event='Music auction'):
    if kind == 'subito':
        scraper = SubitoScraper()
        card = {'subject':title, 'body':'', 'urls':{'default':'https://www.subito.it/annunci/item/123456'}}
        state = {'props':{'pageProps':{'initialState':{'items':{'originalList':[card]}}}}}
        response = '<script id="__NEXT_DATA__">' + json.dumps(state) + '</script>'
        with patch('scrapers.subito.market_queries', return_value=[query]), patch.object(scraper,'make_client',return_value=Client(response)):
            return scraper, await scraper.search()
    if kind == 'schibsted':
        scraper = SchibstedScraper()
        product = {'@type':'Product','name':title,'description':'','url':'https://www.finn.no/item/123456'}
        response = '<script type="application/ld+json">' + json.dumps({'@type':'ItemList','itemListElement':[{'item':product}]}) + '</script>'
        with patch('scrapers.schibsted.market_queries',return_value=[query]), \
             patch('scrapers.schibsted.SITES',[('FINN','https://www.finn.no/search','q','Norway','NOK','no')]), \
             patch.object(scraper,'make_client',return_value=Client(response)):
            return scraper, await scraper.search()
    if kind == 'hibid':
        scraper = HiBidScraper()
        lot = {'id':'123456','lead':title,'description':description,'lotState':{'status':'OPEN'},
               'auction':{'eventName':event,'eventDateEnd':'2026-11-01'}}
        response = {'data':{'lotSearch':{'pagedResults':{'results':[lot]}}}}
        with patch('scrapers.hibid.market_queries',return_value=[query]), patch.object(scraper,'make_client',return_value=Client(response)):
            return scraper, await scraper.search()
    scraper = ShopGoodwillScraper()
    response = {'searchResults':{'items':[{'itemId':'123456','title':title,'description':''}]}}
    with patch('scrapers.shopgoodwill.market_queries',return_value=[query]), patch.object(scraper,'make_client',return_value=Client(response)):
        return scraper, await scraper.search()


class NativeCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_and_card_hints_are_preserved(self):
        for kind in ('subito','schibsted','hibid','shopgoodwill'):
            for title, identity_unknown in [('Electric violin',True),('Zeta',False),('Zeta pickup',False),('Jean Luc Ponty CD',False)]:
                with self.subTest(adapter=kind,title=title):
                    _, cards = await get_cards(kind,title)
                    self.assertEqual(len(cards),1)
                    self.assertEqual(cards[0]['_native_search_query'],'Zeta')
                    self.assertEqual(cards[0]['_query_identity_unverified'],identity_unknown)
                    if title == 'Zeta pickup':
                        self.assertFalse(cards[0]['description'].strip())
                    if title == 'Jean Luc Ponty CD':
                        self.assertNotEqual(classify(cards[0]),'')
            with self.subTest(adapter=kind,query='Yamaha'):
                _, cards = await get_cards(kind,'Electric violin','Yamaha')
                self.assertEqual(cards,[])

    async def test_hibid_event_context_cannot_become_product_identity(self):
        from notifier import TelegramNotifier
        for blocked in (True, False):
            with self.subTest(blocked=blocked), tempfile.TemporaryDirectory() as directory, \
                 patch.object(database,'DB_PATH',str(Path(directory)/'test.db')):
                import main
                from price_tracker import PriceTracker
                db, prices = database.Database(), PriceTracker()
                try:
                    scraper, cards = await get_cards('hibid','Electric violin',description='Working condition',
                                                      event='Zeta estate auction')
                    self.assertEqual(len(cards),1)
                    card = cards[0]
                    self.assertEqual(card['description'],'Working condition')
                    self.assertEqual(card['auction_name'],'Zeta estate auction')
                    self.assertEqual(card['auction_date'],'2026-11-01')
                    self.assertTrue(card['_query_identity_unverified'])
                    message = TelegramNotifier('', '')._format_listing(1,card)
                    self.assertIn('Zeta estate auction',message)
                    self.assertIn('01.11.2026',message)
                    scraper.search = AsyncMock(return_value=cards)
                    page = ('<main><h1>Yamaha electric violin</h1>'
                            '<div id="description">Used Yamaha electric violin for sale.</div><p>Price $1500</p></main>')
                    async def verify(item,http):
                        return ('unknown','HTTP 403') if blocked else inspect_page(item,page,200,item['url'])
                    with patch('main.verify_offer',side_effect=verify) as check, patch.object(Config,'SCRAPER_RETRIES',0):
                        result=await main._run_scraper_with_resilience(scraper,db,prices,asyncio.Semaphore(1))
                    check.assert_awaited_once()
                    self.assertEqual(result['new'],[])
                    self.assertEqual(db.pending(),[])
                    if blocked:
                        self.assertEqual(db.conn.execute("SELECT count(*) FROM discovery_queue WHERE namespace=?",('search:HiBid',)).fetchone()[0],1)
                finally:
                    prices.close();db.close()

    def test_explicit_pickup_without_instrument_is_not_promoted(self):
        from native_candidates import incomplete_pickup_card
        card={'title':'Zeta pickup','description':'Pickup only for a violin; instrument not included.',
              'url':'https://example.com/item/123','platform':'Fixture','_native_search_query':'Zeta'}
        reason=classify(card)
        self.assertEqual(reason,'noise')
        self.assertFalse(incomplete_pickup_card(card,reason))

    async def test_gumtree_extra_card_text_does_not_override_product_description(self):
        from native_candidates import card_hints
        from scrapers.gumtree import GumtreeScraper
        html = """<div data-q='search-result'><a href='/p/violins/electric-violin/123456'>
            <article><h2 data-q='tile-title'>Electric violin</h2><p data-q='tile-description'>Working condition</p>
            <span data-q='tile-location'>Zeta</span></article></a></div>"""
        tile=GumtreeScraper._extract(html)[0]
        self.assertIn('Zeta',tile['text'])
        self.assertNotIn('Zeta',tile['description'])
        self.assertTrue(card_hints(tile['title'],tile['description'],'Zeta')['_query_identity_unverified'])
        scraper=GumtreeScraper()
        scraper.reset_health()
        with patch('scrapers.gumtree.market_queries',return_value=['Zeta']), \
             patch.object(scraper,'make_client',return_value=Client(html)):
            cards=await scraper.search()
        self.assertEqual(len(cards),1)
        self.assertTrue(cards[0]['_query_identity_unverified'])
        self.assertEqual(cards[0]['description'],'Working condition')

    async def test_query_only_flag_requires_primary_even_if_metadata_looks_conclusive(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database,'DB_PATH',str(Path(directory)/'test.db')):
            import main
            from filters import has_primary_identity
            from price_tracker import PriceTracker
            db, prices = database.Database(), PriceTracker()
            try:
                scraper, cards = await get_cards('subito','Electric violin')
                card = cards[0]
                card['brand'] = 'Zeta'  # unverified card metadata, not a primary response
                self.assertTrue(card['_query_identity_unverified'])
                self.assertTrue(has_primary_identity(card))
                self.assertEqual(classify(card),'')
                scraper.search = AsyncMock(return_value=cards)
                with patch('main.verify_offer',AsyncMock(return_value=('unknown','HTTP 403'))) as check, \
                     patch.object(Config,'SCRAPER_RETRIES',0):
                    result = await main._run_scraper_with_resilience(scraper,db,prices,asyncio.Semaphore(1))
                check.assert_awaited_once()
                self.assertEqual(result['new'],[])
                self.assertEqual(db.pending(),[])
                self.assertFalse(db.is_seen(card['id']))
                self.assertFalse(db.was_url_alerted(card['url']))
                self.assertEqual(db.conn.execute('SELECT count(*) FROM discovery_queue WHERE namespace=?',
                                                 ('search:Subito',)).fetchone()[0],1)
            finally:
                prices.close();db.close()

    async def test_subito_query_only_card_requires_primary_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database,'DB_PATH',str(Path(directory)/'test.db')):
            import main
            from price_tracker import PriceTracker
            db, prices = database.Database(), PriceTracker()
            try:
                scraper,cards = await get_cards('subito','Electric violin')
                scraper.search = AsyncMock(return_value=cards)
                page = ('<main><h1>Zeta Jazz Fusion JV5 violin</h1>'
                        '<div id="description">Used 5 string Zeta electric violin for sale.</div><p>Price €1500</p></main>')
                async def verify(item,http): return inspect_page(item,page,200,item['url'])
                with patch('main.verify_offer',side_effect=verify) as check, patch.object(Config,'SCRAPER_RETRIES',0):
                    result=await main._run_scraper_with_resilience(scraper,db,prices,asyncio.Semaphore(1))
                check.assert_awaited_once()
                self.assertEqual(len(result['new']),1)
                self.assertEqual([row['url'] for row in db.pending()],[cards[0]['url']])
                self.assertFalse(db.was_url_alerted(cards[0]['url']))
            finally:
                prices.close();db.close()


if __name__ == '__main__': unittest.main()
