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

    def test_header_failure_does_not_abort_individual_delivery(self):
        notifier=TelegramNotifier('fake','fake')
        notifier.send=AsyncMock(side_effect=[RuntimeError('header failed'),True])
        with patch.object(Config,'SEND_PHOTOS',False):
            delivered=asyncio.run(notifier.send_listings([self.listing()]))
        self.assertEqual(len(delivered),1)

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


if __name__ == '__main__':
    unittest.main()
