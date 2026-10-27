"""Regressions for observed Willhaben category and Gumtree DOM failures.

Reduced public DOM structure; synthetic items; no network or real alerts.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(TEMP.name) / 'parsers.db')
from filters import classify
from keywords import market_queries
from scrapers.base import BaseScraper
from scrapers.gumtree import GumtreeScraper
from scrapers.willhaben import WillhabenScraper, SEARCH_URL, CATEGORY_URL, search_plan
from offer_verifier import is_item_url, inspect_page

CARDS = (Path(__file__).parent / 'fixtures/gumtree_cards_2026.html').read_text()


def next_page(result):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({
        'props': {'pageProps': {'searchResult': result}}}) + '</script>'


def run(scraper, handler, browser=(0, ""), will_result=None, gum_result=None):
    def client(**kwargs):
        return BaseScraper.make_client(scraper, transport=httpx.MockTransport(handler), **kwargs)
    with patch.object(scraper, 'make_client', client), \
         patch('scrapers.gumtree.PublicSearchBrowser.fetch', AsyncMock(return_value=browser)), \
         patch('scrapers.willhaben.fresh_willhaben_search', AsyncMock(return_value=will_result or {})), \
         patch('scrapers.gumtree.fresh_gumtree_search', AsyncMock(return_value=gum_result or {})), \
         patch('asyncio.sleep',AsyncMock()):
        return asyncio.run(scraper.search())


class MarketplaceParserTests(unittest.TestCase):
    def test_gumtree_legacy_title_and_paragraph_cannot_borrow_location_brand(self):
        for heading in ('<h2>Electric violin</h2>', 'Electric violin'):
            for location in ('span','p'):
                html=('<article data-q="search-result"><a href="/p/violins/electric-violin/123456789">'
                      +heading+f'<{location} data-q="tile-location">Zeta Road</{location}>'
                      '<span data-q="tile-price">£500</span></a></article>')
                for query in ('Zeta','electric violin'):
                    with self.subTest(heading=heading,location=location,query=query), patch(
                            'scrapers.gumtree.market_queries',return_value=[query]):
                        found=run(GumtreeScraper(),lambda r:httpx.Response(200,text=html))
                    self.assertEqual(len(found),int(query=='Zeta'))
                    if found:
                        self.assertEqual(found[0]['title'],'Electric violin')
                        self.assertEqual(found[0]['description'],'')
                        self.assertTrue(found[0]['_query_identity_unverified'])
                        self.assertEqual(classify(found[0]),'non_zeta')

    def test_gumtree_unscoped_legacy_body_is_a_lead_never_primary_identity(self):
        html=('<article data-q="search-result"><a href="/p/violins/electric-violin/123456789">'
              '<h2>Electric violin</h2></a><p>Used Zeta 4 string violin for sale</p></article>')
        with patch('scrapers.gumtree.market_queries',return_value=['electric violin']):
            found=run(GumtreeScraper(),lambda r:httpx.Response(200,text=html))
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['description'],'')
        self.assertTrue(found[0]['_query_identity_unverified'])
        self.assertEqual(classify(found[0]),'non_zeta')

    def test_willhaben_sparse_cards_keep_query_without_asserting_identity(self):
        payload={'text':'1 Anzeige', 'extras':{'links':[
            '/iad/kaufen-und-verkaufen/d/electric-violin-123456/']}}
        native=next_page({'rowsFound':1,'advertSummaryList':{'advertSummary':[
            {'id':'123456','attributes':{'attribute':[
                {'name':'HEADING','values':['Electric violin']},
                {'name':'SEO_URL','values':['kaufen-und-verkaufen/d/electric-violin-123456/']} ]}}
        ]}})
        for query in ('Zeta', 'Yamaha'):
            for fallback in (False, True):
                with self.subTest(query=query, fallback=fallback), patch(
                        'scrapers.willhaben.search_plan', return_value=[(query,SEARCH_URL)]):
                    found=run(WillhabenScraper(), lambda r:httpx.Response(403 if fallback else 200,text=native),
                              will_result=payload)
                self.assertEqual(len(found), int(query=='Zeta'))
                if found:
                    self.assertEqual(found[0]['_native_search_query'], 'Zeta')
                    self.assertTrue(found[0]['_query_identity_unverified'])
                    self.assertEqual(classify(found[0]), 'non_zeta')

    def test_gumtree_sparse_native_and_extracted_cards_keep_brand_query_only(self):
        payload={'text':'# 1 ad for Zeta', 'extras':{'links':[
            '/p/violins/electric-violin/123456789']}}
        native=('<article data-q="search-result"><a href="/p/violins/electric-violin/123456789">'
                '<h2>Electric violin</h2></a><span data-q="tile-location">Zeta Road</span></article>')
        for query in ('Zeta', 'electric violin'):
            for fallback in (False, True):
                with self.subTest(query=query, fallback=fallback), patch(
                        'scrapers.gumtree.market_queries', return_value=[query]):
                    found=run(GumtreeScraper(), lambda r:httpx.Response(247 if fallback else 200,text=native),
                              browser=(247,''), gum_result=payload)
                self.assertEqual(len(found), int(query=='Zeta'))
                if found:
                    self.assertEqual(found[0]['description'], '')
                    self.assertEqual(found[0]['_native_search_query'], 'Zeta')
                    self.assertTrue(found[0]['_query_identity_unverified'])
                    self.assertEqual(classify(found[0]), 'non_zeta')

    def test_gumtree_last_live_route_recovers_explicit_empty_after_two_blocks(self):
        payload={'text':'Jazz standard violin - Gumtree\n\n# 0 ads for jazz standard violin in All Classifieds',
                 'extras':{'links':[]}}
        scraper=GumtreeScraper()
        with patch('scrapers.gumtree.market_queries',return_value=['jazz standard violin']):
            self.assertEqual(run(scraper,lambda r:httpx.Response(247),browser=(247,''),gum_result=payload),[])
        self.assertEqual(scraper.health_error(),'')
        self.assertEqual(scraper.requests_recovered,3)

    def test_gumtree_extracted_real_item_survives_partial_result_set(self):
        payload={'text':'# 4 ads for Zeta violin', 'extras':{'links':[
            '/p/violins/zeta-jv5-electric-violin/123456789',
            'https://unrelated.example/p/violins/zeta/99999']}}
        scraper=GumtreeScraper()
        with patch('scrapers.gumtree.market_queries',return_value=['Zeta violin']):
            found=run(scraper,lambda r:httpx.Response(247),browser=(247,''),gum_result=payload)
        self.assertEqual(len(found),1)
        self.assertEqual(classify(found[0]),'')
        self.assertIn('partial',scraper.health_error())
        with self.assertRaises(ValueError): GumtreeScraper._extract_exa({'text':'Access denied'})
        with self.assertRaises(ValueError): GumtreeScraper._extract_exa({'text':'# 1 ad for zeta','extras':{'links':[]}})

    def test_willhaben_live_extraction_keeps_real_relative_item_links(self):
        violin='/iad/kaufen-und-verkaufen/d/zeta-geige-violin-5-strings-zehr-gute-zustand-1363838209/'
        keyboard='/iad/kaufen-und-verkaufen/d/alesis-q49-midi-keyboard-914382744/'
        payload={'text':'Musikinstrumente / Noten | willhaben\n\n2 Anzeigen in Musikinstrumente / Noten\nZeta Geige Violin',
                 'extras':{'links':[keyboard]*4+[violin]*4+['https://evil.example'+violin]}}
        adverts,complete=WillhabenScraper._extract_exa(payload)
        self.assertTrue(complete)
        self.assertEqual(len(adverts),2)
        self.assertEqual(adverts[-1]['id'],'1363838209')
        self.assertIn('zeta geige violin',WillhabenScraper._attrs(adverts[-1])['HEADING'])
        scraper=WillhabenScraper()
        with patch('scrapers.willhaben.search_plan',return_value=[('zeta',CATEGORY_URL)]):
            found=run(scraper,lambda r:httpx.Response(403),will_result=payload)
        self.assertEqual(len(found),1)
        self.assertEqual(classify(found[0]),'')
        self.assertEqual(found[0]['url'],'https://www.willhaben.at'+violin)
        self.assertEqual(scraper.health_error(),'')
        self.assertEqual(scraper.requests_recovered,1)

    def test_willhaben_partial_extraction_is_not_claimed_as_complete(self):
        payload={'text':'5.900 Anzeigen\nZeta Geige', 'extras':{'links':[
            '/iad/kaufen-und-verkaufen/d/zeta-geige-1363838209/']}}
        scraper=WillhabenScraper()
        with patch('scrapers.willhaben.search_plan',return_value=[('zeta',SEARCH_URL)]):
            found=run(scraper,lambda r:httpx.Response(403),will_result=payload)
        self.assertEqual(len(found),1)
        self.assertIn('partial',scraper.health_error())
        for result in ({'text':'Access denied'}, {'text':'2 Anzeigen','extras':{'links':[]}}):
            with self.assertRaises(ValueError): WillhabenScraper._extract_exa(result)
        self.assertEqual(WillhabenScraper._extract_exa({'text':'0 Anzeigen'}),([],True))
        self.assertTrue(WillhabenScraper._extract_exa({'text':'1 Anzeige',
            'extras':{'links':payload['extras']['links']}})[1])

    def test_gumtree_browser_recovers_exact_query_without_false_access_alarm(self):
        scraper=GumtreeScraper()
        calls=[]
        def blocked(request):
            calls.append(str(request.url));return httpx.Response(247)
        with patch('scrapers.gumtree.market_queries',return_value=['Zeta violin','Zeta JV5']):
            found=run(scraper,blocked,browser=(200,CARDS))
        self.assertTrue(any(not classify(item) for item in found))
        self.assertEqual(scraper.health_error(),'')
        self.assertEqual(scraper.requests_recovered,4)
        self.assertEqual(scraper.requests_attempted,6)
        self.assertTrue(any('JV5' in url for url in calls))

    def test_gumtree_browser_challenge_stays_an_access_error(self):
        scraper=GumtreeScraper()
        with patch('scrapers.gumtree.market_queries',return_value=['Zeta violin']):
            self.assertEqual(run(scraper,lambda r:httpx.Response(247),browser=(200,'<h1>Access denied</h1>')),[])
        self.assertIn('247',scraper.health_error())
        self.assertEqual(scraper.requests_recovered,0)

    def test_gumtree_browser_explicit_empty_page_is_a_valid_completed_query(self):
        scraper=GumtreeScraper()
        with patch('scrapers.gumtree.market_queries',return_value=['Zeta violin']):
            self.assertEqual(run(scraper,lambda r:httpx.Response(247),browser=(200,'<h1>0 ads for Zeta violin</h1>')),[])
        self.assertEqual(scraper.health_error(),'')
        self.assertEqual(scraper.requests_recovered,2)

    def test_marketplace_item_routes_are_host_scoped(self):
        paths = [('www.willhaben.at', '/iad/kaufen-und-verkaufen/d/zeta-geige-123/'),
                 ('www.gumtree.com', '/p/violins/zeta-jv5/123')]
        for host, path in paths:
            self.assertTrue(is_item_url('https://' + host + path))
            self.assertFalse(is_item_url('https://blog.example' + path))
        self.assertFalse(is_item_url(CATEGORY_URL))
        self.assertFalse(is_item_url('https://www.gumtree.com/search?q=zeta'))

    def test_marketplace_primary_body_replaces_search_snippet(self):
        for url in ('https://www.willhaben.at/iad/kaufen-und-verkaufen/d/zeta-geige-123/',
                    'https://www.gumtree.com/p/violins/zeta-jv5/123'):
            listing = {'url':url, 'title':'Zeta violin', 'description':'Old search snippet'}
            state, _ = inspect_page(listing, '<main><h1>Zeta JV5</h1>Used Geige. Price £900.</main>', 200, url)
            self.assertEqual(state, 'live')
            self.assertNotIn('Old search snippet', listing['description'])
            self.assertTrue(listing['_primary_content_checked'])

    def test_strong_index_identity_can_alert_on_blocked_known_item_route(self):
        from unittest.mock import AsyncMock
        import main
        import database
        database.DB_PATH = os.environ['DB_PATH']
        db = database.Database()
        try:
            for host, path in [('www.willhaben.at', '/iad/kaufen-und-verkaufen/d/zeta-jv5-123/'),
                               ('www.gumtree.com', '/p/violins/zeta-jv5/123')]:
                item = {'id':host, 'url':'https://' + host + path, 'title':'Zeta JV5 5 Strings',
                        'description':'Used violin', 'platform':host, 'source':'search'}
                async def verify():
                    async with httpx.AsyncClient() as client:
                        return await main._verify_search_hits([item], db, client, {})
                with patch('main.verify_offer', AsyncMock(return_value=('unknown', 'HTTP 403'))):
                    found = asyncio.run(verify())
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['verification'], 'unknown')
                self.assertFalse(db.is_seen(item['id']))
                self.assertIn(item['url'], [x['url'] for x in db.pending()])
        finally:
            db.close()

    def test_new_div_cards_and_legacy_articles_both_parse(self):
        cards = GumtreeScraper._extract(CARDS)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]['title'], 'Zeta JLP 5 Strings')
        self.assertEqual(cards[0]['price'], '£ 1,850')
        self.assertEqual(cards[0]['location'], 'London')
        self.assertEqual(cards[0]['description'], 'Violon électrique ancien. Case only has minor scuffs.')
        self.assertEqual(cards[0]['url'], 'https://www.gumtree.com/p/violins/zeta-jlp/9000000001')
        self.assertEqual(cards[1]['title'], 'Zeta JV5')

    def test_new_testids_work_without_legacy_data_q(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(CARDS, 'lxml')
        for el in soup.select('[data-q]'):
            del el['data-q']
        self.assertEqual(GumtreeScraper._extract(str(soup))[0]['title'], 'Zeta JLP 5 Strings')

    def test_duplicate_urls_are_one_item(self):
        self.assertEqual(len(GumtreeScraper._extract(CARDS + CARDS)), 2)

    def test_gumtree_retains_description_tail_and_multilingual_identity(self):
        long = 'Historical notes. ' * 100 + ' Zeta Geige Jazz Fusion, gebraucht.'
        html = CARDS.replace('Violon électrique ancien. Case only has minor scuffs.', long)
        scraper = GumtreeScraper()
        found = run(scraper, lambda request: httpx.Response(200, text=html))
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]['description'], long)
        self.assertEqual(classify(found[0]), '')
        self.assertEqual(scraper.requests_attempted, len(market_queries('en', broad=True, limit=8, extra=('electric violin',))))
        self.assertEqual(scraper.health_error(), '')

    def test_external_ad_links_are_not_instruments(self):
        bad = '<div data-q="search-result"><a href="https://ads.example/p/violin/zeta/123">Zeta</a></div>'
        with self.assertRaises(ValueError):
            GumtreeScraper._extract(bad)

    def test_empty_search_is_healthy_but_challenge_or_layout_is_not(self):
        self.assertEqual(GumtreeScraper._extract('<h1>0 ads for zeta violin in All Classifieds</h1>'), [])
        scraper = GumtreeScraper()
        self.assertEqual(run(scraper, lambda request: httpx.Response(200, text='<h1>Please enable JavaScript</h1>')), [])
        self.assertIn('layout change', scraper.health_error())

    def test_willhaben_correct_music_category_plus_every_global_query(self):
        plan = search_plan()
        self.assertEqual(plan[:len(plan)-2], [(q, SEARCH_URL) for q in market_queries('de', broad=True)])
        self.assertEqual(plan[-2:], [('zeta', CATEGORY_URL), ('strados', CATEGORY_URL)])
        self.assertTrue(CATEGORY_URL.endswith('/musikinstrumente-noten-6540'))

    def test_willhaben_detail_fields_and_duplicate_global_category_results(self):
        attrs = {'HEADING':'Zeta 5 Strings', 'BODY_DYN':'a' * 1000 + ' Geige Jazz Fusion, gebraucht.',
                 'SEO_URL':'kaufen-und-verkaufen/d/zeta-123/', 'PRICE_FOR_DISPLAY':'€ 1.900', 'LOCATION':'Wien'}
        ad = {'id':'123', 'attributes':{'attribute':[{'name':k,'values':[v]} for k,v in attrs.items()]}}
        html = next_page({'rowsFound':1, 'advertSummaryList':{'advertSummary':[ad]}})
        scraper = WillhabenScraper()
        calls = []
        def handler(request):
            calls.append((str(request.url).split('?')[0], request.url.params['keyword']))
            return httpx.Response(200, text=html)
        found = run(scraper, handler)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['description'], attrs['BODY_DYN'])
        self.assertEqual(classify(found[0]), '')
        self.assertEqual(calls, [(url, q) for q, url in search_plan()])
        self.assertEqual(scraper.health_error(), '')

    def test_willhaben_empty_search_and_unrecognized_200_are_distinct(self):
        self.assertEqual(WillhabenScraper._extract(next_page({'rowsFound':0})), [])
        for html in ('<h1>Access denied</h1>', '<script id="__NEXT_DATA__">{}</script>',
                     next_page({'rowsFound':1, 'advertSummaryList':{'advertSummary':[]}})):
            with self.subTest(html=html), self.assertRaises(ValueError):
                WillhabenScraper._extract(html)
        scraper = WillhabenScraper()
        self.assertEqual(run(scraper, lambda request: httpx.Response(200, text='<h1>Access denied</h1>')), [])
        self.assertIn('search data missing', scraper.health_error())

    def test_willhaben_403_remains_visible(self):
        scraper = WillhabenScraper()
        self.assertEqual(run(scraper, lambda request: httpx.Response(403)), [])
        self.assertIn('HTTP 403', scraper.health_error())


if __name__ == '__main__':
    unittest.main()
