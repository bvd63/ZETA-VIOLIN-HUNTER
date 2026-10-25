"""Offline regressions for real missed auctions/noise, quotas and delivery failures."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(TEMP.name) / 'bot.db')

from config import Config
import database
database.DB_PATH = os.environ['DB_PATH']
from database import Database, canonical_url
from filters import classify
from keywords import market_queries, MODEL_CODES
from scrapers.govdeals import parse_asset
from scrapers.base import BaseScraper
from offer_verifier import inspect_page, verify_offer
from notifier import TelegramNotifier
from search_budget import reserve_request
from price_tracker import PriceTracker, comparison_group
from scrapers.google import GoogleScraper, PRIORITY_QUERIES
from scrapers.brave import BraveScraper

TITLE = 'Asset #370: Zeta Electric Violins (2), Electric Viola (1)'
ASSET = {'assetId':370, 'accountId':12180, 'auctionId':2, 'assetShortDesc':TITLE,
         'assetLongDesc':'This lot includes (2) electric violins (Zeta JV44) and (1) electric viola.',
         'assetAuctionEndDate':'2026-08-07T12:41:47', 'assetStatusCd':'SOA', 'willShip':False}


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.dns_patch = patch('offer_verifier.public_dns', AsyncMock(return_value=True))
        self.dns_patch.start()
        self.addCleanup(self.dns_patch.stop)
        self.db = Database()
        for table in ('seen_listings','alerts','delivered_urls','pending_alerts','listing_activity','liveness_cache'):
            self.db.conn.execute(f'DELETE FROM {table}')
        self.db.conn.execute('CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY, last_run TEXT)')
        self.db.conn.execute('DELETE FROM scraper_runs')
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()

    def listing(self, **kwargs):
        return {'id':'x', 'title':'Zeta Jazz Fusion JV44', 'url':'https://example.com/item/123',
                'price':'2000 USD', 'platform':'Test', **kwargs}

    def test_real_govdeals_mixed_lot(self):
        active = {**ASSET, 'assetStatusCd':'AUC'}
        listing = parse_asset(active, now=datetime(2026,8,1,tzinfo=timezone.utc))
        self.assertEqual(classify(listing), '')
        self.assertTrue(listing['mixed_lot'])
        self.assertTrue(listing['pickup_only'])
        self.assertEqual(listing['auction_end'], '2026-08-07T16:41:47+00:00')

    def test_real_govdeals_closed_lot(self):
        self.assertEqual(parse_asset(ASSET), {})

    def test_historical_auction_house_is_prioritized_each_run(self):
        for scraper in (GoogleScraper('fake', 'fake'), BraveScraper()):
            queries = ' '.join(q for q, _ in scraper.plan_queries(0)[0])
            for host in ('gardinerhoulgate.co.uk', 'musicalinstrument-auctions.co.uk', 'tarisio.com', 'ha.com'):
                self.assertIn('site:' + host, queries)

    def test_gardiner_closed_lot_is_not_active_opportunity(self):
        url='https://auctions.gardinerhoulgate.co.uk/catalogue/lot/abcdef/musical-instruments-lot-3457/'
        page='<main><h1>Rare four string Zeta electric violin</h1>Hammer £3,400 Watch Lot Closed Auction Date: 12th Jun 2026</main>'
        self.assertEqual(inspect_page(self.listing(url=url),page,200,url)[0],'dead')

    def test_historical_marketplace_models(self):
        for title in ('SV244 Strados electric violin', 'Zeta Imbus Fusion JVS-4', 'ZETA Modernist Electric Violin'):
            self.assertEqual(classify(self.listing(title=title)), '')

    def test_same_auction_url_with_new_verified_deadline_can_alert(self):
        listing=self.listing(auction=True,auction_end='2026-10-10T20:00:00+00:00')
        self.db.record_alert(listing,2000)
        self.assertTrue(self.db.was_url_alerted(listing['url'],listing['auction_end']))
        self.assertFalse(self.db.was_url_alerted(listing['url'],'2026-11-10T20:00:00+00:00'))
        self.assertTrue(self.db.was_url_alerted(listing['url']))

    def test_live_probe_aircraft_kits_and_music_records_are_rejected(self):
        examples=[
            ('タミヤ 1/48 フォッケウルフ Fw190 D-9 JV44 プラモデル', ''),
            ('JV44: The Galland Circus', ''),
            ('Lancia Strados scala 1:24 da restauro', ''),
            ('Jean-Luc Ponty — The Jean-Luc Ponty Experience', ''),
            ('Jean-Luc Ponty — A Taste For Passion', 'electric violin performed by Jean-Luc Ponty'),
            ('patti smith gone again', 'Jean-Luc Ponty played violin'),
            ('Mahavishnu Orchestra - Apocalypse', 'Jean-Luc Ponty violin'),
        ]
        for title,description in examples:
            with self.subTest(title=title):
                self.assertNotEqual(classify(self.listing(title=title,description=description)), '')

    def test_old_false_positive_does_not_reappear_in_active_list(self):
        invalid=self.listing(title='Fw190 D-9 JV44 1/72 scale')
        self.db.mark_seen(invalid['id'],invalid)
        self.db.touch_active(invalid)
        self.assertEqual(self.db.active_listings(),[])

    def test_user_yahoo_lp_and_artist_genre_records_are_rejected(self):
        titles=[
            'ドイツ盤LP! Stuff Smith / Stephane Grappelly / Svend Asmussen / Jean-Luc Ponty / Violin-Summit 67年 [SABA/SB15 099ST] ヴァイオリン',
            'ジャン=リュック・ポンティ 極光 ジャズ ヴァイオリン エレクトリック プログレ ジャズ・ロック リマスター 紙 美品 Jean-luc Ponty AURORA',
            'ジャン=リュック・ポンティ 桃源への旅立ち ヴァイオリン プログレ ジャズ ロック リマスター 紙 美品 Jean-luc Ponty IMAGINARY VOYAGE',
            'ジャン=リュック・ポンティ コズミック・メッセンジャー ヴァイオリン プログレ ジャズ ロック Jean-luc Ponty COSMIC MESSENGER 紙',
            'ジャン=リュック・ポンティ ウィングス・オブ・ミュージック ヴァイオリン プログレ ジャズ ロック Jean-luc Ponty WINGS OF MUSIC 紙',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertNotEqual(classify(self.listing(title=title)), '')

    def test_japanese_media_format_boundaries_preserve_instrument_descriptions(self):
        for title in ('輸入ジャズCD Zeta ヴァイオリン', 'ドイツ盤LP Zeta violin', 'Zeta バイオリン ＣＤ', 'Zeta violin ＬＰ'):
            with self.subTest(title=title):
                self.assertEqual(classify(self.listing(title=title)), 'noise')
        self.assertEqual(classify(self.listing(title='Zeta electric violin serial ABCD123',description='Includes a demo CD and imported LP recording.')), '')

    def test_artist_discovery_requires_zeta_identity_but_jlp5_is_a_model_code(self):
        title='Jean-Luc Ponty signature 5-string electric violin'
        self.assertEqual(classify(self.listing(title=title)), 'non_zeta')
        self.assertEqual(classify(self.listing(title=title,description='Zeta Jazz Fusion JLP5')), '')
        self.assertEqual(classify(self.listing(title='JLP5 バイオリン 5弦')), '')

    def test_artist_discovery_queries_include_local_violin_word(self):
        found=set()
        for day in range(1,29):
            found.update(market_queries('nl',broad=True,now=datetime(2026,10,day,10)))
        self.assertNotIn('Jean-Luc Ponty',found)
        self.assertIn('Jean-Luc Ponty viool',found)

    def test_price_drop_queue_survives_restart_independently(self):
        listing=self.listing()
        info={'old_price':2000,'new_price':1500,'drop_pct':25}
        self.db.enqueue_price_drop(listing,info)
        self.assertEqual(self.db.pending(),[])
        second=Database()
        try:
            self.assertEqual(second.pending_price_drops(),[(listing,info)])
            second.finish_price_drop(listing['url'])
            self.assertEqual(second.pending_price_drops(),[])
        finally:
            second.close()

    def test_private_redirect_never_fetched(self):
        requests=[]
        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(302,headers={'location':'http://127.0.0.1/status'})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                state,_=await verify_offer(self.listing(),client)
                self.assertEqual(state,'non_sale')
        asyncio.run(run())
        self.assertEqual(requests,[self.listing()['url']])

    def test_viola_alone_still_excluded(self):
        self.assertEqual(classify(self.listing(title='Zeta Electric Viola ZV0020')), 'noise')
        self.assertEqual(classify(self.listing(title='Zeta violin/viola')), 'noise')

    def test_reverb_title_without_exact_phrase(self):
        title = 'ZETA Jazz Fusion 4-string MIDI Electric Violin White JV44 Serial 0103'
        self.assertEqual(classify(self.listing(title=title)), '')

    def test_reverb_brand_in_description_tail_is_not_lost(self):
        from scrapers.reverb import ReverbScraper
        description = '<p>' + 'Excellent condition, includes original packaging. ' * 12 + 'Made by Zeta, Jazz Fusion violin.</p>'
        item = {'id':123, 'title':'Electric violin 5 strings', 'description':description,
                'condition':{'display_name':'Used'}, 'state':{'slug':'live'},
                '_links':{'web':{'href':'https://reverb.com/item/123-violin'}}}
        scraper = ReverbScraper()
        def handler(request):
            return httpx.Response(200,json={'listings':[item] if request.url.params['page']=='1' else []})
        original = scraper.make_client
        with patch.object(scraper,'make_client',side_effect=lambda **kw: original(transport=httpx.MockTransport(handler),**kw)):
            listings = asyncio.run(scraper.search())
        self.assertEqual(len(listings),1)
        self.assertGreater(len(listings[0]['description']),300)
        self.assertEqual(classify(listings[0]),'')

    def test_gumtree_247_is_reported_as_access_failure(self):
        from scrapers.gumtree import GumtreeScraper
        scraper = GumtreeScraper()
        async def run():
            async with scraper.make_client(transport=httpx.MockTransport(lambda r: httpx.Response(247))) as client:
                await client.get('https://www.gumtree.com/search?q=zeta')
        asyncio.run(run())
        self.assertEqual(scraper.requests_ok,0)
        self.assertIn('HTTP 247',scraper.health_error())

    def test_media_and_concert_noise(self):
        for title in ('Zeta violin magazine 1994', 'Zeta Jazz Fusion violin review',
                      'Zeta electric violin print ad', 'Jean-Luc Ponty concert tickets'):
            with self.subTest(title=title):
                self.assertEqual(classify(self.listing(title=title)), 'noise')

    def test_editorial_url_without_title_hint(self):
        self.assertEqual(classify(self.listing(source='search',url='https://site.com/en/blog/zeta-violin')), 'non_sale')

    def test_article_with_affiliate_product_is_not_offer(self):
        page = '<main><h1>Zeta violin</h1><script type="application/ld+json">' + json.dumps([
            {'@type':'Article','headline':'Zeta violin'},
            {'@type':'Product','name':'Zeta violin','offers':{'price':'2000','priceCurrency':'USD'}}]) + '</script></main>'
        self.assertEqual(inspect_page(self.listing(), page, 200, 'https://site.com/zeta')[0], 'non_sale')

    def test_sold_primary_offer_ignores_sidebar_cart(self):
        page = '<main><h1>Zeta violin</h1><script type="application/ld+json">' + json.dumps(
            {'@type':'Product','name':'Zeta violin','offers':{'availability':'https://schema.org/SoldOut'}}) + '</script></main><footer>Add to cart</footer>'
        self.assertEqual(inspect_page(self.listing(), page, 200, self.listing()['url'])[0], 'dead')

    def test_live_offer_enriches_title_price(self):
        listing = self.listing(title='Electric instrument')
        page = '<main><script type="application/ld+json">' + json.dumps(
            {'@type':'Product','name':'Zeta JV44 violin','offers':{'price':'1900','priceCurrency':'EUR','availability':'https://schema.org/InStock'}}) + '</script></main>'
        self.assertEqual(inspect_page(listing, page, 200, listing['url'])[0], 'live')
        self.assertEqual(listing['price'], '1900 EUR')
        self.assertEqual(classify(listing), '')

    def test_block_is_unknown_not_live(self):
        self.assertEqual(inspect_page(self.listing(), 'Access denied', 403, self.listing()['url'])[0], 'unknown')

    def test_year_2014_environment_does_not_hide_recent_listing(self):
        with patch.object(Config,'MAX_YEAR',2014), patch.object(Config,'FILTER_TEXT_YEARS',False):
            self.assertTrue(BaseScraper()._year_in_range('Zeta made in 1999; listed in 2026'))
            self.assertTrue(BaseScraper()._year_in_range('Used Zeta purchased in 2020'))

    def test_shared_discovery_rotation_is_bounded_and_complete(self):
        found=set()
        for day in range(1,29):
            for hour in (10,22):
                queries=market_queries('de',broad=True,limit=8,now=datetime(2026,10,day,hour))
                self.assertLessEqual(len(queries),8)
                self.assertIn('zeta',queries)
                self.assertIn('Zeta Jazz Fusion',queries)
                found.update(queries)
        self.assertTrue(set(MODEL_CODES) <= found)
        self.assertIn('Zetta Geige',found)

    def test_each_search_plan_prioritizes_all_auction_groups(self):
        for scraper in (GoogleScraper('fake','fake'), BraveScraper()):
            plan,_ = scraper.plan_queries(0)
            for query in PRIORITY_QUERIES:
                self.assertIn(query,[q for q,_ in plan])

    def test_daily_quota_survives_connections_and_reserves_first(self):
        self.assertTrue(reserve_request('google',2))
        self.assertTrue(reserve_request('google',2))
        self.assertFalse(reserve_request('google',2))
        self.assertEqual(int(self.db.conn.execute("SELECT last_run FROM scraper_runs WHERE scraper LIKE 'google_quota:%'").fetchone()[0]),2)

    def test_brave_monthly_and_daily_limits(self):
        for _ in range(32):
            self.assertTrue(reserve_request('brave',960))
        self.assertFalse(reserve_request('brave',960))
        self.db.conn.execute('DELETE FROM scraper_runs')
        self.db.conn.commit()
        self.assertTrue(reserve_request('brave',1))
        self.assertFalse(reserve_request('brave',1))

    def test_exact_url_dedup_between_engines(self):
        listing=self.listing(url='https://www.reverb.com/item/102519209-long-title?utm_source=google')
        self.db.record_alert(listing,2000)
        self.assertTrue(self.db.was_url_alerted('https://reverb.com/en/item/102519209-other-title?utm_source=brave'))
        self.assertFalse(self.db.was_url_alerted('https://reverb.com/item/102519210-other-instrument'))
        self.assertEqual(canonical_url('https://www.ebay.de/itm/Title/12345'),canonical_url('https://ebay.com/itm/12345'))

    def test_pending_survives_restart_and_removed_only_after_delivery(self):
        listing=self.listing()
        self.db.enqueue(listing)
        second=Database()
        try:
            self.assertEqual(second.pending()[0]['title'],listing['title'])
            second.record_alert(listing,2000)
            self.assertEqual(second.pending(),[])
        finally:
            second.close()

    def test_not_configured_telegram_does_not_report_delivery(self):
        self.assertFalse(asyncio.run(TelegramNotifier('','').send('test')))

    def test_bids_and_mixed_lots_are_not_price_comparables(self):
        self.assertEqual(comparison_group(self.listing(auction=True)), '')
        self.assertEqual(comparison_group(self.listing(price='200 USD (current bid)')), '')
        self.assertEqual(comparison_group(self.listing(title=TITLE,mixed_lot=True)), '')

    def test_fallback_reverb_api_checks_real_state(self):
        def handler(request):
            return httpx.Response(200,json={'title':'Zeta Jazz Fusion JV44','state':{'slug':'live'},'price':{'amount':'1910','currency':'USD'}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                listing=self.listing(url='https://reverb.com/item/102519209-zeta-jazz-fusion')
                state,_=await verify_offer(listing,client)
                self.assertEqual(state,'live')
                self.assertEqual(listing['price'],'1910 USD')
        asyncio.run(run())

    def test_mercari_preserves_yen_currency_before_range_check(self):
        from scrapers.mercari_jp import MercariJPScraper
        item=SimpleNamespace(id_='m123',name='Zeta JV44 electric violin',price=300000,
                             status='ITEM_STATUS_ON_SALE',thumbnails=[])
        fake=SimpleNamespace(search=AsyncMock(return_value=SimpleNamespace(items=[item])))
        with patch.dict(sys.modules, {'mercapi':SimpleNamespace(Mercapi=lambda:fake)}):
            listings=asyncio.run(MercariJPScraper().search())
        self.assertEqual(len(listings),1)
        self.assertEqual(listings[0]['price'],'300000 JPY')

    def test_direct_host_fallback_is_not_suppressed(self):
        import main
        class Search(BaseScraper):
            name='Search fixture'
            async def search(inner):
                return [self.listing(source='search',url='https://reverb.com/item/102519209-fixture')]
        pt=PriceTracker()
        try:
            with patch('main.verify_offer',AsyncMock(return_value=('live','fixture'))):
                result=asyncio.run(main._run_scraper_with_resilience(Search(),self.db,pt,asyncio.Semaphore(1)))
            self.assertEqual(len(result['new']),1)
            self.assertEqual(len(self.db.active_listings()),1)
        finally:
            pt.close()

    def test_queue_retains_failed_delivery_and_deduplicates_success(self):
        import main
        pt=PriceTracker()
        listing=self.listing()
        try:
            fail=SimpleNamespace(send_listings=AsyncMock(return_value=[]))
            self.assertEqual(asyncio.run(main._deliver(fail,self.db,pt,[listing])),0)
            self.assertFalse(self.db.is_seen(listing['id']))
            self.assertEqual(len(self.db.pending()),1)
            success=SimpleNamespace(send_listings=AsyncMock(side_effect=lambda batch: batch))
            self.assertEqual(asyncio.run(main._deliver(success,self.db,pt,[listing,dict(listing)])),1)
            self.assertEqual(self.db.pending(),[])
            self.assertEqual(asyncio.run(main._deliver(success,self.db,pt,[listing])),0)
            self.assertEqual(success.send_listings.await_count,1)
        finally:
            pt.close()

    def test_failed_listing_does_not_abort_other_deliveries(self):
        notifier=TelegramNotifier('fake','fake')
        notifier._send_violin=AsyncMock(side_effect=[RuntimeError('delivery failed'),True])
        with patch.object(Config,'SEND_PHOTOS',False):
            delivered=asyncio.run(notifier.send_listings([self.listing(), self.listing(id='other')]))
        self.assertEqual(len(delivered),1)
        self.assertEqual(delivered[0]['id'],'other')

    def test_successful_empty_source_is_not_watchdog_failure(self):
        from status_tracker import StatusTracker
        tracker=StatusTracker()
        try:
            tracker.record_scraper('Empty fixture',0,0,details={'requests_ok':1})
            self.assertEqual(tracker.get_streaks()['Empty fixture']['zero'],0)
            self.assertEqual(tracker.get_status()['scrapers']['Empty fixture']['details']['requests_ok'],1)
        finally:
            tracker.close()

    def test_source_blocks_are_visible(self):
        scraper=BaseScraper()
        async def run():
            async with scraper.make_client(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                await client.get('https://example.com/search')
        asyncio.run(run())
        self.assertEqual(scraper.requests_ok,0)
        self.assertIn('HTTP 403',scraper.health_error())

    def test_skipped_sources_are_not_reported_as_searched(self):
        from status_tracker import StatusTracker
        tracker = StatusTracker()
        try:
            tracker.start_cycle()
            tracker.record_scraper("Checked fixture", 0, 0, details={"requests_ok": 1})
            tracker.record_scraper("Guard fixture", -1, 0)
            tracker.record_scraper("Partial fixture", 2, 0, error="HTTP 403")
            tracker.end_cycle(total_sent=0)
            status = tracker.get_status()
            self.assertEqual(status["last_cycle"]["scrapers_ran"], 2)
            self.assertEqual(status["scrapers"]["Guard fixture"]["coverage_state"], "skipped")
            self.assertEqual(status["scrapers"]["Partial fixture"]["coverage_state"], "partial")
        finally:
            tracker.close()

    def test_price_comparison_excludes_different_models_and_bids(self):
        pt=PriceTracker()
        try:
            pt.conn.execute('DELETE FROM price_history')
            pt.conn.commit()
            for index in range(5):
                pt.record_listing(self.listing(id=f'comp{index}',price='3000 USD',condition='Good'))
            other=pt.record_listing(self.listing(id='other',title='Zeta Strados SV24',price='1000 USD',condition='Good'))
            self.assertIsNone(other['avg_price'])
            auction=pt.record_listing(self.listing(id='bid',price='100 USD (current bid)',auction=True,condition='Good'))
            self.assertIsNone(auction['avg_price'])
            match=pt.record_listing(self.listing(id='match',price='2000 USD',condition='Good'))
            self.assertEqual(match['avg_price'],3000)
            self.assertEqual(match['total_seen'],5)
        finally:
            pt.close()


class SourceHealthTests(unittest.TestCase):
    """Regressions from the October 12 production status and public probes."""

    @staticmethod
    def yahoo_html(query, cards=""):
        return (f'<html><title>Yahoo!オークション - {query}の中古品一覧</title>'
                '<p>に一致する商品はありません。キーワードの一部を利用した結果を表示しています</p>'
                f'{cards}</html>')

    def test_yahoo_valid_empty_404_is_healthy_and_partial_instruments_are_kept(self):
        from scrapers.yahoo_auctions_jp import YahooAuctionsJPScraper
        scraper = YahooAuctionsJPScraper()
        card = ('<li class="Product"><a class="Product__titleLink" '
                'href="/jp/auction/b123456789">中古 Zeta Strados バイオリン</a></li>')

        def handler(request):
            body = self.yahoo_html(request.url.params['p'], card)
            # Force the response hook to read the streamed body before classifying.
            return httpx.Response(404, stream=httpx.ByteStream(body.encode()))

        original = scraper.make_client
        with patch.object(Config, 'EGRESS_COUNTRY', 'SG'), \
                patch('scrapers.yahoo_auctions_jp.market_queries', return_value=['Zeta Jazz Fusion']), \
                patch.object(scraper, 'make_client', side_effect=lambda **kw: original(
                    transport=httpx.MockTransport(handler), **kw)):
            results = asyncio.run(scraper.search())
        self.assertEqual(len(results), 1)
        self.assertEqual(classify(results[0]), '')
        self.assertEqual(scraper.fetched, 1)
        self.assertEqual(scraper.requests_ok, 1)
        self.assertEqual(scraper.health_error(), '')

    def test_yahoo_real_missing_pages_wrong_routes_and_blocks_stay_errors(self):
        from scrapers.yahoo_auctions_jp import YahooAuctionsJPScraper
        cases = [
            ('https://auctions.yahoo.co.jp/search/search?p=zeta', 404, '<title>Not Found</title>'),
            ('https://auctions.yahoo.co.jp/jp/auction/b123?p=zeta', 404, self.yahoo_html('zeta')),
            ('https://auctions.yahoo.co.jp/search/search?p=zeta', 404, self.yahoo_html('other')),
            ('https://example.com/search/search?p=zeta', 404, self.yahoo_html('zeta')),
            ('https://auctions.yahoo.co.jp/search/search?p=zeta', 403, self.yahoo_html('zeta')),
        ]
        for url, status, body in cases:
            with self.subTest(url=url, status=status, body=body):
                scraper = YahooAuctionsJPScraper()
                async def run():
                    async with scraper.make_client(transport=httpx.MockTransport(
                            lambda r: httpx.Response(status, text=body))) as client:
                        await client.get(url)
                asyncio.run(run())
                self.assertEqual(scraper.requests_ok, 0)
                self.assertIn(f'HTTP {status}', scraper.health_error())

    def test_yahoo_verified_zero_match_404_resets_false_error_streak(self):
        from scrapers.yahoo_auctions_jp import YahooAuctionsJPScraper
        scraper = YahooAuctionsJPScraper()
        body = ('<title>【2026年最新】 Yahoo!オークション -stradosの中古品一覧</title>'
                '<p>条件に一致する商品は見つかりませんでした。</p>')
        async def run():
            async with scraper.make_client(transport=httpx.MockTransport(
                    lambda r: httpx.Response(404, text=body))) as client:
                await client.get('https://auctions.yahoo.co.jp/search/search?p=strados')
        asyncio.run(run())
        self.assertEqual(scraper.requests_ok, 1)
        self.assertEqual(scraper.health_error(), '')
        from status_tracker import StatusTracker
        tracker = StatusTracker()
        try:
            tracker.record_scraper('Yahoo empty fixture', 0, 0, error='HTTP 404')
            tracker.record_scraper('Yahoo empty fixture', 0, 0, details={'requests_ok': 1})
            self.assertEqual(tracker.get_streaks()['Yahoo empty fixture'], {'zero': 0, 'error': 0})
        finally:
            tracker.close()

    def test_yahoo_loading_shell_is_reported_and_other_queries_continue(self):
        from scrapers.yahoo_auctions_jp import YahooAuctionsJPScraper
        scraper = YahooAuctionsJPScraper()
        def handler(request):
            if request.url.params['p'] == 'loading':
                return httpx.Response(200, text='<div class="gv-SkeletonItem">Loading</div>')
            return httpx.Response(404, text=self.yahoo_html('strados'))
        original = scraper.make_client
        with patch.object(Config, 'EGRESS_COUNTRY', 'SG'), \
                patch('scrapers.yahoo_auctions_jp.market_queries', return_value=['loading', 'strados']), \
                patch.object(scraper, 'make_client', side_effect=lambda **kw: original(
                    transport=httpx.MockTransport(handler), **kw)):
            asyncio.run(scraper.search())
        self.assertEqual(scraper.requests_attempted, 2)
        self.assertEqual(scraper.requests_ok, 2)
        self.assertIn('require JavaScript', scraper.health_error())
        self.assertNotIn('without response', scraper.health_error())

    def test_yahoo_empty_notice_split_over_elements_is_valid_search(self):
        from scrapers.yahoo_auctions_jp import YahooAuctionsJPScraper
        response = httpx.Response(404, text=(
            '<title>Yahoo!オークション - ＳＴＲＡＤＯＳの中古品一覧</title>'
            '<h2>条件に一致する商品は</h2><p>見つかりませんでした。</p>'),
            request=httpx.Request('GET', 'https://auctions.yahoo.co.jp/search/search/?p=strados'))
        self.assertTrue(YahooAuctionsJPScraper._is_empty_search(response))

    def test_shopify_spacing_preserves_every_store_collection_and_keyword(self):
        from scrapers.shopify_dealers import ShopifyDealersScraper, STORES, REQUEST_GAP_SECONDS
        scraper = ShopifyDealersScraper()
        requests = []
        terms = ['zeta', 'strados', 'JV44', 'JV45', 'SV24', 'SV25']
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'products': [], 'resources': {'results': {'products': []}}})
        original = scraper.make_client
        with patch('scrapers.shopify_dealers.market_queries', return_value=terms), \
                patch('scrapers.shopify_dealers.asyncio.sleep', AsyncMock()) as sleep, \
                patch.object(scraper, 'make_client', side_effect=lambda **kw: original(
                    transport=httpx.MockTransport(handler), **kw)):
            asyncio.run(scraper.search())
        self.assertEqual(len(requests), sum(len(s[3]) + len(terms) for s in STORES))
        for _, base, _, collections, _ in STORES:
            host = httpx.URL(base).host
            subset = [r for r in requests if r.url.host == host]
            self.assertEqual([r.url.params['q'] for r in subset if r.url.path == '/search/suggest.json'], terms)
            self.assertEqual(len(subset), len(collections) + len(terms))
        self.assertEqual(sleep.await_count, len(requests) - len(STORES))
        self.assertTrue(all(0 < c.args[0] <= REQUEST_GAP_SECONDS for c in sleep.await_args_list))
        self.assertEqual(scraper.health_error(), '')

    def test_shopify_rate_limit_does_not_discard_later_queries_or_other_stores(self):
        from scrapers.shopify_dealers import ShopifyDealersScraper
        scraper = ShopifyDealersScraper()
        requests = []
        def handler(request):
            requests.append((request.url.host, request.url.params['q']))
            if request.url.host == 'one.example' and request.url.params['q'] == 'zeta':
                return httpx.Response(429)
            product = {'id': 1, 'handle': 'used-zeta', 'title': 'Used Zeta Strados electric violin'}
            return httpx.Response(200, json={'resources': {'results': {'products': [product]}}})
        original = scraper.make_client
        stores = [('One', 'https://one.example', 'USA', [], 'Used'),
                  ('Two', 'https://two.example', 'USA', [], 'Used')]
        with patch('scrapers.shopify_dealers.STORES', stores), \
                patch('scrapers.shopify_dealers.market_queries', return_value=['zeta', 'strados']), \
                patch('scrapers.shopify_dealers.asyncio.sleep', AsyncMock()), \
                patch.object(scraper, 'make_client', side_effect=lambda **kw: original(
                    transport=httpx.MockTransport(handler), **kw)):
            results = asyncio.run(scraper.search())
        self.assertEqual(requests, [('one.example', 'zeta'), ('one.example', 'strados'),
                                    ('two.example', 'zeta'), ('two.example', 'strados')])
        self.assertEqual(len(results), 2)
        self.assertEqual(scraper.requests_ok, 3)
        self.assertIn('HTTP 429 (one.example)', scraper.health_error())

    def test_skipped_cycles_do_not_reset_error_streak(self):
        from status_tracker import StatusTracker
        tracker = StatusTracker()
        name = 'Skipped-error fixture'
        try:
            tracker.record_scraper(name, 0, 0, error='HTTP 403')
            tracker.record_scraper(name, -1, 0)
            tracker.record_scraper(name, 0, 0, error='HTTP 403')
            tracker.record_scraper(name, -1, 0)
            self.assertEqual(tracker.get_streaks()[name]['error'], 2)
            tracker.record_scraper(name, 4, 0)
            self.assertEqual(tracker.get_streaks()[name]['error'], 0)
        finally:
            tracker.close()

    def test_shopify_predictive_body_recovers_generic_used_violin_and_keeps_new_stock_rules(self):
        from scrapers.shopify_dealers import ShopifyDealersScraper
        scraper = ShopifyDealersScraper()
        products = [
            {'id': 1, 'handle': 'vintage-electric', 'title': 'Vintage electric violin',
             'type': 'Used Violins', 'body': '<p>' + ('Instrument details. ' * 40)
             + 'This is a Zeta Strados electric violin.</p>'},
            {'id': 2, 'handle': 'new-zeta', 'title': 'Zeta Strados electric violin',
             'type': 'Electric Violins', 'body': 'Can be used on stage or in a studio.'},
        ]
        original = scraper.make_client
        with patch('scrapers.shopify_dealers.STORES', [('Mixed store', 'https://store.example', 'USA', [], 'New')]), \
                patch('scrapers.shopify_dealers.market_queries', return_value=['zeta']), \
                patch.object(scraper, 'make_client', side_effect=lambda **kw: original(
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
                        'resources': {'results': {'products': products}}})), **kw)):
            results = asyncio.run(scraper.search())
        by_handle = {r['url'].rsplit('/', 1)[-1]: r for r in results}
        used = by_handle['vintage-electric']
        self.assertIn('Zeta Strados', used['description'][300:])
        self.assertEqual(used['condition'], 'Used')
        self.assertEqual(classify(used), '')
        self.assertEqual(classify(by_handle['new-zeta']), 'new_stock')

    def test_access_errors_are_never_reported_as_empty_markets(self):
        import main
        scraper = BaseScraper()
        scraper.name = 'Blocked fixture'
        notifier = SimpleNamespace(send_watchdog=AsyncMock())
        with patch.object(main.status_tracker, 'get_streaks', return_value={
                scraper.name: {'error': 3, 'zero': 30}}):
            asyncio.run(main._watchdog([scraper], notifier))
        notifier.send_watchdog.assert_not_awaited()
        with patch.object(main.status_tracker, 'get_streaks', return_value={
                scraper.name: {'error': 10, 'zero': 30}}):
            asyncio.run(main._watchdog([scraper], notifier))
        message = notifier.send_watchdog.await_args.args[0][0]
        self.assertIn('erori sau acoperire parțială', message)
        self.assertNotIn('0 rezultate', message)

    def test_long_error_streak_is_not_stuck_at_notification_milestone(self):
        from status_tracker import StatusTracker
        tracker = StatusTracker()
        try:
            for i in range(31):
                tracker.record_scraper('Long-error fixture', 0, 0, error='HTTP 403')
            self.assertEqual(tracker.get_streaks()['Long-error fixture'], {'zero': 31, 'error': 31})
            self.assertEqual(tracker.get_streaks(lookback=30)['Long-error fixture']['error'], 30)
        finally:
            tracker.close()

    def test_watchdog_skipped_source_does_not_resend_old_milestone(self):
        import main
        scraper = BaseScraper()
        scraper.name = 'Skipped watchdog fixture'
        scraper.skipped = True
        notifier = SimpleNamespace(send_watchdog=AsyncMock())
        with patch.object(main.status_tracker, 'get_streaks', return_value={
                scraper.name: {'error': 2, 'zero': 2}}):
            asyncio.run(main._watchdog([scraper], notifier))
        notifier.send_watchdog.assert_not_awaited()

    def test_deploy_after_morning_cycle_preserves_evening_search_budget(self):
        from datetime import timedelta
        import main
        search = AsyncMock()
        # Default production schedule is 10:00/22:00; a deploy 11 hours after
        # the morning search used to run early and starve the evening search.
        with patch.object(main.status_tracker, 'last_cycle_started', return_value=datetime.utcnow() - timedelta(hours=11)), \
                patch.object(Config, 'STARTUP_RUN_GUARD_HOURS', 12), \
                patch('main.run_search_cycle', search):
            asyncio.run(main._startup_cycle())
        search.assert_not_awaited()

    def test_watchdog_does_not_claim_partial_sources_return_nothing(self):
        notifier = TelegramNotifier('fake', 'fake')
        notifier.send = AsyncMock(return_value=True)
        asyncio.run(notifier.send_watchdog(['Yahoo: acoperire parțială']))
        message = notifier.send.await_args.args[0]
        self.assertNotIn('nu mai returnează nimic', message)
        self.assertIn('Celelalte căutări continuă', message)


if __name__ == '__main__':
    unittest.main()
