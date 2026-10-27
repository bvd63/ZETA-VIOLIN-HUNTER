"""Incident regressions: protected discovery, inventories, missing FB details.

All HTTP and delivery are mocked; SQLite is isolated in a temporary directory.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(TEMP.name) / 'quality.db')
import database
database.DB_PATH = os.environ['DB_PATH']
from config import Config
from provider_state import store, reserve_work, used
from work_context import spending_scope, work_kind
from discovery_queue import DiscoveryQueue
from offer_verifier import inspect_page, is_item_url
from scrapers.apify_facebook import ApifyFacebookScraper, normalize, store as fb_store

LIST_URL = 'https://www.mercatinomusicale.com/mm/s_violini-viole_kw+violino+zeta+jazz+fusion-rp6-ct65.html'
ITEM_URL = 'https://www.mercatinomusicale.com/mm/a_zeta-jv5_id12345.html'
PAGE = '<title>VIOLINO ZETA JAZZ FUSION</title><main>Price €1500 <a href="/mm/a_zeta-jv5_id12345.html">Zeta JV5</a><a href="/mm/a_other-violin_id12346.html">Electric instrument</a><a href="https://evil.example/item/1">Zeta</a></main>'


def violin(n: int = 1) -> dict:
    return {'id': str(n), 'url': f'https://example.com/item/{n}', 'title': 'Zeta Jazz Fusion JV5',
            'description': 'Used 5-string electric violin', 'price': '1500 USD', 'platform': 'Fixture', 'source': 'search'}


class QualityTests(unittest.TestCase):
    def setUp(self):
        with store() as conn:
            conn.execute('DELETE FROM provider_reservations')
            conn.execute('DELETE FROM provider_state')
            conn.commit()
        with fb_store() as conn:
            for table in ('apify_jobs', 'apify_items', 'apify_item_attempts', 'apify_batch_targets'):
                conn.execute(f'DELETE FROM {table}')
            conn.commit()

    def test_manual_pages_cannot_consume_evening_search_or_page_slots(self):
        day = 1793059200 + 3600
        core = lambda units, at, counters=(): reserve_work('exa', units, 8000000, 250000, 7750000,
                                                          counters=counters, now=at)
        for _ in range(6): self.assertTrue(core(17000, day))
        for _ in range(10): self.assertTrue(core(1000, day, (('will',1,620,20),)))
        for _ in range(35):
            self.assertTrue(reserve_work('exa',1000,8000000,250000,7750000,auxiliary=True,
                                        counters=(('will',1,620,40),),now=day+100))
        for _ in range(6): self.assertTrue(core(17000, day+43200))
        for _ in range(10): self.assertTrue(core(1000, day+43200, (('will',1,620,20),)))
        self.assertEqual(used('will',now=day+43201),20)
        self.assertEqual(used('will:aux',now=day+43201),35)

    def test_31_day_core_plan_and_auxiliary_surplus_share_unchanged_ceiling(self):
        start = 1790812800 + 3600
        self.assertTrue(reserve_work('exa',250000,8000000,250000,7750000,auxiliary=True,now=start))
        for day in range(31):
            self.assertTrue(reserve_work('exa',250000,8000000,250000,7750000,now=start+day*86400))
        self.assertEqual(used('exa',now=start+30*86400+1),8000000)
        self.assertFalse(reserve_work('exa',1,8000000,250000,7750000,auxiliary=True,now=start+30*86400+1))

    def test_untagged_legacy_costs_are_retained_and_block_extra_spend_conservatively(self):
        now = time.time()
        with store() as conn:
            conn.execute('INSERT INTO provider_reservations VALUES (?,?,?)',('exa',now-86400,250000))
            conn.commit()
        self.assertFalse(reserve_work('exa',1000,8000000,250000,7750000,auxiliary=True,now=now))
        self.assertTrue(reserve_work('exa',17000,8000000,250000,7750000,now=now))
        self.assertEqual(used('exa'),267000)

    def test_failed_reservation_does_not_consume_page_slot(self):
        self.assertFalse(reserve_work('exa',1000,999,250000,7750000,counters=(('pages',1,620,20),)))
        self.assertEqual(used('pages'),0)
        self.assertEqual(used('exa'),0)

    def test_mercatino_list_expands_all_individual_routes_without_title_gate(self):
        item = {**violin(),'url':LIST_URL}
        state, _ = inspect_page(item,PAGE,200,LIST_URL)
        self.assertEqual(state,'collection')
        self.assertEqual(len(item['_collection_leads']),2)
        self.assertEqual(item['_collection_leads'][1]['title'],'Electric instrument')
        self.assertTrue(is_item_url(ITEM_URL))
        self.assertFalse(is_item_url(ITEM_URL.replace('mercatinomusicale.com','other.example')))

    def test_real_product_with_recommendation_list_remains_an_offer(self):
        item = {**violin(),'url':ITEM_URL}
        data = {'@type':'Product','name':'Zeta Jazz Fusion JV5','description':'Used electric violin',
                'offers':{'price':1500,'priceCurrency':'EUR','availability':'https://schema.org/InStock'},
                'isRelatedTo':{'@type':'ItemList','itemListElement':[{'url':LIST_URL}]}}
        page = '<main><h1>Zeta Jazz Fusion JV5</h1><script type="application/ld+json">'+json.dumps(data)+'</script></main>'
        self.assertEqual(inspect_page(item,page,200,ITEM_URL)[0],'live')
        self.assertNotIn('_collection_leads',item)

    def test_explicit_generic_collection_cannot_borrow_recommendation_price(self):
        item = {**violin(),'url':'https://example.com/search'}
        page = '<script type="application/ld+json">{"@type":"SearchResultsPage"}</script><main>Price $1800 <a href="/item/1">Zeta</a></main>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'collection')
        self.assertEqual(len(item['_collection_leads']),1)

    def test_alternative_actor_shape_preserves_description_photo_price_and_status(self):
        row = {'url':'https://www.facebook.com/marketplace/item/12345/', 'title':'Zeta JV5',
               'listingId':'12345','priceValue':1500,'priceCurrency':'EUR','imageUrl':'https://example.com/photo.jpg',
               'description':'Vioară Zeta cu cinci corzi','isLive':True,'isSold':False,'isPending':False}
        item = normalize(row)
        self.assertEqual(item['description'],row['description'])
        self.assertEqual(item['price'],'1500 EUR')
        self.assertEqual(item['image_url'],row['imageUrl'])
        self.assertIs(item['apify_is_sold'],False)
        self.assertEqual(normalize({**row,'url':'https://evil.example/item/12345/'}),{})

    def test_missing_fb_details_survive_with_backoff_and_do_not_take_next_slot(self):
        url = 'https://www.facebook.com/marketplace/item/12345/'
        item = normalize({'listingUrl':url,'marketplace_listing_title':'Zeta Jazz Fusion JV5'})
        with fb_store() as conn:
            conn.execute('INSERT INTO apify_items (url,candidate) VALUES (?,?)',(url,json.dumps(item)))
            conn.execute("INSERT INTO apify_jobs VALUES ('r','details','run','2026-10',20000,0,0)")
            conn.execute("INSERT INTO apify_batch_targets VALUES ('r',?)",(url,))
            conn.commit()
        scraper = ApifyFacebookScraper();scraper.reset_health()
        scraper._consume('r','details',[{'error':'no_items'}])
        with fb_store() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM apify_items').fetchone()[0],1)
            failures,due = conn.execute('SELECT failures,next_attempt FROM apify_item_attempts WHERE url=?',(url,)).fetchone()
            self.assertEqual(failures,1)
            self.assertGreater(due,time.time()+17*3600)
            self.assertEqual(conn.execute('SELECT cap,done FROM apify_jobs').fetchone(),(20000,1))
        self.assertIn('retained',scraper.health_error())

    def test_unexpected_fb_item_id_does_not_replace_requested_candidate(self):
        wanted = 'https://www.facebook.com/marketplace/item/12345/'
        with fb_store() as conn:
            conn.execute("INSERT INTO apify_batch_targets VALUES ('r',?)",(wanted,));conn.commit()
        scraper = ApifyFacebookScraper();scraper.reset_health()
        scraper._consume('r','details',[{'listingUrl':wanted.replace('12345','99999'),
                                       'marketplace_listing_title':'Zeta violin','is_live':True,'is_sold':False,'is_pending':False}])
        with fb_store() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM apify_items').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT url FROM apify_item_attempts').fetchone()[0],wanted)

    def test_queue_scope_never_reserves_paid_extraction(self):
        from primary_page_reserve import reserve_pages
        with spending_scope('queue'):
            self.assertFalse(reserve_pages('exa_contents'))
        self.assertEqual(used('exa'),0)

    def test_concurrent_reservations_do_not_overspend_or_lose_page_counts(self):
        from concurrent.futures import ThreadPoolExecutor
        def spend(_):
            return reserve_work('parallel',1,10,10,10,counters=(('parallel_pages',1,10,10),))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(spend,range(24)))
        self.assertEqual(sum(results),10)
        self.assertEqual(used('parallel'),10)
        self.assertEqual(used('parallel_pages'),10)

    def test_brave_extra_work_preserves_remaining_month_scheduled_capacity(self):
        from search_budget import reserve_request
        conn = database.connect()
        conn.execute('CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY,last_run TEXT)')
        conn.execute('DELETE FROM scraper_runs');conn.commit();conn.close()
        with patch('search_budget.datetime') as clock:
            clock.now.return_value = datetime(2026,10,1,12,tzinfo=timezone.utc)
            for _ in range(8): self.assertTrue(reserve_request('brave',1000,manual=True))
            self.assertFalse(reserve_request('brave',1000,manual=True))
            for _ in range(32): self.assertTrue(reserve_request('brave',1000))

    def test_retry_after_date_and_nonfinite_values_do_not_trigger_paid_post_retries(self):
        from http_limits import retry_after_seconds
        now = datetime(2026,10,27,12,tzinfo=timezone.utc)
        self.assertEqual(retry_after_seconds('Tue, 27 Oct 2026 12:10:00 GMT',now=now),600)
        self.assertEqual(retry_after_seconds('120',now=now),120)
        self.assertEqual(retry_after_seconds('nan',now=now),3600)
        self.assertEqual(retry_after_seconds('invalid',now=now),3600)

    def test_german_primary_classified_without_english_sale_words_is_eligible(self):
        u = 'https://www.thomann.de/de/classified_show.html?id=1472945'
        item = {**violin(),'url':u}
        page = '<main><h1>Zeta Jazz Fusion JV4 und Synthony II</h1><p>Biete · VB 1880 €</p><div id="description">Gebrauchte Zeta Geige, vier Saiten.</div></main>'
        self.assertTrue(is_item_url(u))
        self.assertFalse(is_item_url(u.replace('thomann.de','other.example')))
        self.assertEqual(inspect_page(item,page,200,u)[0],'live')
        self.assertTrue(item['_primary_content_checked'])

    def test_primary_microdata_is_used_without_borrowing_related_stock(self):
        item = violin()
        page = '''<main><h1>Zeta Jazz Fusion JV5</h1><div itemscope itemtype="https://schema.org/Product">
            <span itemprop="name">Zeta Jazz Fusion JV5</span><p itemprop="description">Used electric violin</p>
            <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <meta itemprop="price" content="1500"><meta itemprop="priceCurrency" content="USD">
            <link itemprop="availability" href="https://schema.org/InStock"></div>
            <aside><div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Other product</span>
            <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <link itemprop="availability" href="https://schema.org/SoldOut"></div></div></aside></div></main>'''
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'live')
        self.assertEqual(item['price'],'1500 USD')

    def test_unknown_product_route_with_recommendations_remains_live(self):
        item = {**violin(), 'url':'https://example.com/custom/zeta'}
        graph = [{'@type':'Product','url':item['url'],'name':item['title'],
                  'offers':{'price':1500,'availability':'https://schema.org/InStock'}},
                 {'@type':'ItemList','name':'Related items'}]
        page = '<h1>'+item['title']+'</h1><script type="application/ld+json">'+json.dumps(graph)+'</script>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'live')
        self.assertNotIn('_collection_leads',item)

    def test_product_links_in_inventory_are_retained(self):
        item = {**violin(), 'url':'https://example.com/search'}
        page = '<script type="application/ld+json">{"@type":"SearchResultsPage"}</script><a href="/products/zeta-jazz">Instrument</a>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'collection')
        self.assertEqual(item['_collection_leads'][0]['url'],'https://example.com/products/zeta-jazz')

    def test_recommended_product_same_path_other_query_id_cannot_close_primary(self):
        item = {**violin(), 'url':'https://example.com/classified.html?id=1'}
        data = {'@type':'Product','url':'https://example.com/classified.html?id=2',
                'name':item['title'],'offers':{'availability':'https://schema.org/SoldOut'}}
        page = '<main><h1>'+item['title']+'</h1><p>Used violin for sale $1500</p><script type="application/ld+json">'+json.dumps(data)+'</script></main>'
        self.assertNotEqual(inspect_page(item,page,200,item['url'])[0],'dead')

    def test_microdata_does_not_borrow_missing_fields_from_nested_product(self):
        from bs4 import BeautifulSoup
        from product_metadata import primary_microdata
        page = '''<h1>Zeta JV5</h1><div itemscope itemtype="https://schema.org/Product">
          <span itemprop="name">Zeta JV5</span><div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
          <meta itemprop="price" content="1500"></div><aside><div itemscope itemtype="https://schema.org/Product">
          <span itemprop="description">Only a case, no instrument</span></div></aside></div>'''
        self.assertEqual(primary_microdata(BeautifulSoup(page,'lxml'),violin()['url'])[0]['description'],'')

    def test_jv4_without_violin_noun_is_eligible_and_preferred(self):
        from filters import classify
        from keywords import preference_matches
        item = {**violin(), 'title':'Zeta JV4 + Synthony II', 'description':'Gebraucht mit Originalkoffer'}
        self.assertEqual(classify(item),'')
        self.assertTrue(preference_matches(item))

    def test_incomplete_facebook_detail_never_erases_discovery_identity(self):
        url = 'https://www.facebook.com/marketplace/item/12345/'
        item = normalize({'listingUrl':url,'marketplace_listing_title':'Zeta electric violin'})
        with fb_store() as conn:
            conn.execute('INSERT INTO apify_items (url,candidate) VALUES (?,?)',(url,json.dumps(item)))
            conn.execute("INSERT INTO apify_jobs VALUES ('r','details','run','2026-10',20000,0,0)")
            conn.execute('INSERT INTO apify_batch_targets VALUES (?,?)',('r',url));conn.commit()
        scraper=ApifyFacebookScraper();scraper.reset_health()
        scraper._consume('r','details',[{'listingUrl':url,'marketplace_listing_title':'Electric instrument'}])
        with fb_store() as conn:
            saved,checked=conn.execute('SELECT candidate,checked FROM apify_items WHERE url=?',(url,)).fetchone()
            self.assertEqual(json.loads(saved)['title'],'Zeta electric violin')
            self.assertEqual(checked,0)

    def test_apify_cached_account_headroom_is_not_reused_by_extra_jobs(self):
        from provider_state import write
        from datetime import timedelta
        moment=datetime(2026,10,27,tzinfo=timezone.utc)
        write('apify:account',{'usage_cycle':{'endAt':(moment+timedelta(days=1)).isoformat()},
                             'included_monthly_usd':5,'usage_usd':4.70,'checked':time.time(),'error':''})
        scraper=ApifyFacebookScraper();scraper.reset_health();scraper.auxiliary_work=True
        with patch.multiple(Config,APIFY_MONTHLY_LIMIT_USD=5,APIFY_DISCOVERY_CAP_USD=.065,APIFY_DETAILS_CAP_USD=.02,RUNS_PER_DAY=2), \
             patch('scrapers.apify_facebook.datetime') as clock:
            clock.now.return_value=moment;clock.fromisoformat.side_effect=datetime.fromisoformat
            for _ in range(6):
                reservation=scraper._reserve('details',.02)
                self.assertTrue(reservation)
                with fb_store() as conn:
                    conn.execute('UPDATE apify_jobs SET done=1 WHERE id=?',(reservation,));conn.commit()
            self.assertFalse(scraper._reserve('details',.02))

    def test_gardiner_calendar_distinguishes_unpublished_and_future_catalogs(self):
        from scrapers.gardiner import calendar_catalogs
        page='''<h1>Upcoming Auctions</h1><section><h3>Music</h3><p>Sale Date: 11th December 2026</p>
          <p>Preview available soon.</p></section><section><h3>Music 2</h3><p>Sale Date: 12th December 2026</p>
          <a href="/sale/80/music">View catalogue</a></section><footer><h3>Links</h3></footer>'''
        catalogs,waiting,errors=calendar_catalogs(page,datetime(2026,10,27).date())
        self.assertEqual(errors,[])
        self.assertEqual(waiting,1)
        self.assertEqual([x['url'] for x in catalogs],['https://www.musicalinstrument-auctions.co.uk/sale/80/music'])
        self.assertEqual(calendar_catalogs(page,datetime(2027,1,1).date()),([],0,[]))
        with self.assertRaises(ValueError): calendar_catalogs('<title>Access denied</title>')

    def test_gardiner_unparsed_calendar_card_is_retained_and_partial_is_reported(self):
        from scrapers.gardiner import calendar_catalogs
        page='''<h1>Upcoming Auctions</h1><div><h3>Music 1</h3><p>Sale Date: 11th December 2026</p>
          <p>Preview available soon.</p></div><div><h3>Music 2</h3><p>Sale date TBC</p>
          <a href="/sale/99/music">Catalogue</a></div>'''
        catalogs,waiting,errors=calendar_catalogs(page,datetime(2026,10,27).date())
        self.assertEqual(waiting,1)
        self.assertTrue(errors)
        self.assertEqual(catalogs[0]['url'],'https://www.musicalinstrument-auctions.co.uk/sale/99/music')

    def test_gardiner_catalog_preserves_generic_titles_and_all_page_links(self):
        from scrapers.gardiner import catalog_links
        page='''<h1>Catalogue</h1><a href="/sale/80/2400/black-instrument">Black instrument</a>
        <a href="/sale/80/2401/zeta-jv5">Zeta JV5</a><a href="?page=2">2</a><a href="?page=19">19</a>
        <a href="https://evil.example/sale/80/2402/zeta">Zeta</a>'''
        lots,pages=catalog_links(page,'https://www.musicalinstrument-auctions.co.uk/sale/80/music')
        self.assertEqual(len(lots),2)
        self.assertEqual(lots[0]['_discovery_priority'],2)
        self.assertEqual(lots[1]['_discovery_priority'],0)
        self.assertEqual(len(pages),2)
        with self.assertRaises(ValueError): catalog_links('<h1>Challenge</h1>','https://www.musicalinstrument-auctions.co.uk/sale/80/music')

    def test_gardiner_published_future_lot_is_live_and_past_unsold_is_closed(self):
        url='https://www.musicalinstrument-auctions.co.uk/sale/80/2401/zeta'
        item={**violin(),'url':url}
        page='<main><h1>Zeta Jazz Fusion JV5</h1><p>Used violin</p><p>Estimate: £2000-4000</p><p>Sale Date: 12th December 2026</p></main>'
        self.assertEqual(inspect_page(item,page,200,url)[0],'live')
        self.assertNotIn('auction_end',item)  # never invent a precise close time
        self.assertEqual(inspect_page({**violin(),'url':url},page.replace('2026','2024'),200,url)[0],'dead')

    def test_auxiliary_cycle_cannot_mask_missed_scheduled_readiness(self):
        from status_tracker import StatusTracker
        from operations import readiness
        tracker=StatusTracker()
        try:
            tracker.conn.execute('DELETE FROM cycle_summary');tracker.conn.commit()
            tracker.start_cycle('core');tracker._cycle_start=datetime(2026,10,26,20);tracker.end_cycle(0)
            tracker.start_cycle('auxiliary');tracker._cycle_start=datetime(2026,10,27,10);tracker.end_cycle(0)
            self.assertEqual(readiness(tracker.last_scheduled_cycle_started(),datetime(2026,10,27,11,tzinfo=timezone.utc))['status'],'search_stale')
            self.assertEqual(tracker.get_status()['last_cycle']['work_kind'],'auxiliary')
        finally:
            tracker.close()

    def test_legacy_readiness_migration_uses_only_actual_schedule_slots(self):
        from status_tracker import StatusTracker
        tracker=StatusTracker()
        try:
            tracker.conn.execute('DELETE FROM cycle_summary');tracker.conn.commit()
            tracker.start_cycle();tracker._cycle_start=datetime(2026,10,26,13);tracker.end_cycle(0)
            self.assertIsNone(tracker.last_scheduled_cycle_started())
            tracker.start_cycle();tracker._cycle_start=datetime(2026,10,26,20);tracker.end_cycle(0)
            self.assertEqual(tracker.last_scheduled_cycle_started(),datetime(2026,10,26,20))
        finally:
            tracker.close()


class QueueFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import main
        self.main = main
        with DiscoveryQueue('search:Fixture') as q:
            q.conn.execute('DELETE FROM discovery_queue');q.conn.commit()
        db = database.Database()
        db.conn.execute('DELETE FROM alerts');db.conn.execute('DELETE FROM pending_alerts');db.conn.commit();db.close()

    async def test_collection_children_are_persisted_and_parent_is_not_alerted(self):
        item = {**violin(),'url':LIST_URL}
        async def verify(candidate, http):
            return inspect_page(candidate,PAGE,200,LIST_URL)
        db = database.Database()
        try:
            with patch('main.verify_offer',side_effect=verify):
                result = await self.main._verify_search_hits([item],db,None,{},'search:Fixture')
            self.assertEqual(result,[])
            self.assertEqual(db.pending(),[])
            with DiscoveryQueue('search:Fixture') as queue:
                self.assertEqual(len(queue.claim(12)),2)
        finally:
            db.close()

    async def test_worker_queues_saved_violin_without_discovery_or_telegram_lock(self):
        with DiscoveryQueue('search:Fixture') as q:
            q.put([violin(98765)])
        modes = []
        async def verify(item,http):
            modes.append(work_kind.get());item['_primary_content_checked']=True
            return 'live','fixture primary page'
        send = AsyncMock(side_effect=lambda items: items)
        with patch.multiple(Config,DETAIL_WORKER_ENABLED=True,DETAIL_WORKER_BATCH=12), \
             patch('main.datetime') as clock, patch('main.verify_offer',side_effect=verify), \
             patch('main.TelegramNotifier',return_value=SimpleNamespace(send_listings=send)), \
             patch('scrapers.search_reserves.ExaScraper.search',AsyncMock(side_effect=AssertionError('paid discovery'))):
            clock.now.return_value=datetime(2026,10,27,14,30,tzinfo=timezone.utc)
            await self.main.detail_queue_tick()
        send.assert_not_awaited()
        self.assertEqual(modes,['queue'])
        db = database.Database()
        try:
            self.assertFalse(db.was_url_alerted(violin(98765)['url']))
            self.assertEqual([x['url'] for x in db.pending()],[violin(98765)['url']])
        finally:
            db.close()

    async def test_worker_yields_around_scheduled_search(self):
        with patch.multiple(Config,DETAIL_WORKER_ENABLED=True,SEARCH_HOURS='10,22'), \
             patch('main.datetime') as clock, patch('main._verify_search_hits',AsyncMock()) as check:
            clock.now.return_value=datetime(2026,10,27,9,56,tzinfo=timezone.utc)
            await self.main.detail_queue_tick()
            check.assert_not_awaited()

    async def test_empty_auction_api_is_unknown_not_proof_of_closed_sale(self):
        from offer_verifier import verify_offer
        item={**violin(), 'url':'https://www.govdeals.com/en/asset/370/12180'}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('scrapers.govdeals.GovDealsClient.initialize',AsyncMock()), \
             patch('scrapers.govdeals.GovDealsClient.asset',AsyncMock(return_value={})):
            self.assertEqual((await verify_offer(item,None))[0],'unknown')
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('scrapers.govdeals.GovDealsClient.initialize',AsyncMock()), \
             patch('scrapers.govdeals.GovDealsClient.asset',AsyncMock(return_value={'assetStatusCd':'SOLD'})):
            self.assertEqual((await verify_offer(item,None))[0],'dead')

    async def test_gardiner_transport_error_uses_public_browser_without_paid_calls(self):
        from scrapers.gardiner import GardinerHoulgateScraper
        scraper=GardinerHoulgateScraper();scraper.reset_health()
        original=scraper.make_client
        def fail(request): raise httpx.ConnectError('fixture',request=request)
        page='<h1>Upcoming Auctions</h1><section><h3>Music</h3><p>Sale Date: 11th December 2026</p><p>Preview available soon.</p></section>'
        browser=SimpleNamespace(fetch=AsyncMock(return_value=(200,page)))
        context=AsyncMock();context.__aenter__.return_value=browser
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(fail),**kw)), \
             patch('scrapers.gardiner.PublicSearchBrowser',return_value=context):
            self.assertEqual(await scraper.search(),[])
        browser.fetch.assert_awaited_once()
        self.assertEqual(scraper.coverage_stats['previews_unpublished'],1)
        self.assertEqual(scraper.requests_recovered,1)
        self.assertEqual(scraper.health_error(),'')

    async def test_gardiner_priority_batch_reaches_zeta_after_generic_lots(self):
        from scrapers.gardiner import GardinerHoulgateScraper
        scraper=GardinerHoulgateScraper();scraper.reset_health()
        original=scraper.make_client
        calendar='<h1>Upcoming Auctions</h1><div><h3>Music</h3><p>Sale Date: 11th December 2026</p><a href="/sale/80/music">Catalogue</a></div>'
        catalog=''.join(f'<a href="/sale/80/{i}/lot">Instrument {i}</a>' for i in range(20))+'<a href="/sale/80/21/zeta">Zeta JV5</a>'
        def reply(request):
            path=request.url.path
            if path=='/auctions': text=calendar
            elif path=='/sale/80/music': text=catalog
            else:
                name='Zeta JV5' if '/21/' in path else 'Instrument'
                text=f'<h1>{name}</h1><p>Used violin</p><p>Estimate: £1000</p><p>Sale Date: 11th December 2026</p>'
            return httpx.Response(200,text=text)
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(reply),**kw)):
            results=await scraper.search()
        self.assertTrue(any(x['title']=='Zeta JV5' for x in results))
        self.assertEqual(scraper.coverage_stats['detail_checked'],12)
        with DiscoveryQueue('search:Gardiner Houlgate') as queue:
            self.assertEqual(queue.conn.execute('SELECT count(*) FROM discovery_queue WHERE namespace=?',(queue.namespace,)).fetchone()[0],21)

    async def test_accepted_receipt_reconciles_interrupted_outbox_without_second_message(self):
        from notifier import TelegramNotifier
        bot=TelegramNotifier('fixture','quality-chat')
        item=violin(771100)
        with patch.object(bot,'_request',AsyncMock(return_value={'ok':True,'result':{'message_id':1}})) as send:
            self.assertTrue(await bot._send_violin(item,'violin'))
            # Same event still pending in delivered_urls after an interrupted batch.
            self.assertTrue(await bot._send_violin(item,'violin'))
            self.assertEqual(send.await_count,1)

    async def test_telegram_rate_limit_is_persisted_without_sleep_or_second_post(self):
        from notifier import TelegramNotifier
        from provider_state import write, read
        bot=TelegramNotifier('fixture','quality-chat')
        write('telegram:retry_after',0)
        response=httpx.Response(429,json={'ok':False,'parameters':{'retry_after':600}})
        try:
            with patch('httpx.AsyncClient.post',AsyncMock(return_value=response)) as post, \
                 patch('notifier.asyncio.sleep',AsyncMock(side_effect=AssertionError('must not hold search lock'))):
                self.assertFalse(await bot.send('fixture'))
                self.assertFalse(await bot.send('fixture'))
                self.assertEqual(post.await_count,1)
                self.assertGreater(read('telegram:retry_after'),time.time()+590)
        finally:
            write('telegram:retry_after',0)

    async def test_account_snapshot_frontier_precedes_usage_request(self):
        from resource_monitor import refresh_accounts
        from provider_state import read
        from unittest.mock import Mock
        clock=SimpleNamespace(time=Mock(return_value=100))
        async def get(client,url,**kwargs):
            clock.time.return_value=200  # a job can start/settle while these GETs run
            if url.endswith('/usage/monthly'):
                payload={'totalUsageCreditsUsdAfterVolumeDiscount':4,'usageCycle':{}}
            elif url.endswith('/limits'): payload={'limits':{}}
            else: payload={'plan':{'monthlyUsageCreditsUsd':5}}
            return httpx.Response(200,json={'data':payload},request=httpx.Request('GET',url))
        with patch('resource_monitor.time',clock), patch('resource_monitor.claim_window',return_value=True), \
             patch.multiple(Config,APIFY_TOKEN='fixture',PUSHOVER_APP_TOKEN=''), \
             patch('httpx.AsyncClient.get',get),patch('apify_costs.reconcile',AsyncMock(return_value=0)):
            await refresh_accounts()
        self.assertEqual(read('apify:account')['checked'],100)


if __name__ == '__main__':
    unittest.main()
