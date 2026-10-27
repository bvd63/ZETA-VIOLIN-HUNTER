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


def sold_reverb(n=82035982):
    title = 'Zeta Electric Violin Jazz Classic Vintage (2000)'
    return {**violin(n), 'url': f'https://reverb.com/en-ro/item/{n}-zeta-electric-violin',
            'title': title, 'description': f'# {title}\n\nUsed – Excellent\n\nSOLD\n\nSee similar gear\n\n# {title}\n\nThis gear has sold\n\nListed: 2 years ago\n\nAbout this listing\nUsed violin.'}


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

    def test_reverb_primary_sold_precedes_stale_stock_and_recommendation_buy_buttons(self):
        item = sold_reverb()
        product = {'@type':'Product', 'url':item['url'], 'name':item['title'],
                   'offers':{'price':1500, 'availability':'https://schema.org/InStock'}}
        page = '<main><h1>'+item['title']+'</h1><p>This gear has sold</p><h2>About this listing</h2><p>Used violin</p><aside>Add to cart</aside><script type="application/ld+json">'+json.dumps(product)+'</script></main>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'dead')

    def test_offerup_sold_badge_wins_over_stock_metadata(self):
        url='https://offerup.com/item/detail/3b32aed8-b98b-3c09-a73b-2f220d215b75'
        item={**violin(), 'url':url, 'title':'Zeta Violin'}
        product={'@type':'Product','url':url,'name':'Zeta Violin',
                 'offers':{'price':900,'availability':'https://schema.org/InStock'}}
        page='<main><h1>Zeta Violin</h1><div>SOLD</div><h2>Description</h2><p>Used</p></main><script type="application/ld+json">'+json.dumps(product)+'</script>'
        self.assertEqual(inspect_page(item,page,200,url)[0],'dead')

    def test_offerup_sold_scope_does_not_use_seller_or_recommendations(self):
        url='https://offerup.com/item/detail/3b32aed8-b98b-3c09-a73b-2f220d215b75'
        for extra in ('<div id="description"><span>SOLD</span> AS IS</div>',
                      '<h2>Description</h2><p>SOLD separately</p>',
                      '<div class="suggested-items"><span>SOLD</span></div>',
                      '<aside><span>SOLD</span></aside>',
                      '<h2><span>SOLD</span> by Alice</h2>',
                      '<div><span>Sold</span><span> by Alice</span></div>',
                      '<section><h2>Other listings</h2><article><span>SOLD</span></article></section>',
                      '<article><a href="https://offerup.com/item/detail/12345">Other violin</a><span>SOLD</span></article>'):
            item={**violin(), 'url':url}
            page='<main><h1>Zeta Violin</h1>'+extra+'<p>Price $900</p></main>'
            with self.subTest(extra=extra):
                self.assertNotEqual(inspect_page(item,page,200,url)[0], 'dead')

    def test_offerup_redirect_other_item_is_unknown(self):
        url='https://offerup.com/item/detail/3b32aed8-b98b-3c09-a73b-2f220d215b75'
        other='https://offerup.com/item/detail/3b32aed8-b98b-3c09-a73b-2f220d215b76'
        page='<main><h1>Zeta Violin</h1><div>SOLD</div></main>'
        self.assertEqual(inspect_page({**violin(), 'url':url},page,200,other)[0], 'unknown')

    def test_offerup_indexed_markdown_and_card_are_scoped_to_item(self):
        from offer_verifier import indexed_closed_reason, index_card_negative, _indexed_state_key
        url='https://offerup.com/item/detail/3b32aed8-b98b-3c09-a73b-2f220d215b75'
        item={**violin(), 'url':url, 'title':'Zeta Violin for Sale in Hemet, CA - OfferUp',
              'description':'# Zeta Violin\n## Sold by Maria\n## Additional images\nSOLD\n## Description\nUsed'}
        self.assertTrue(indexed_closed_reason(item))
        self.assertEqual(_indexed_state_key(item),'availability:indexed_closed:offerup:3b32aed8-b98b-3c09-a73b-2f220d215b75')
        for description in ('# Other Violin\nSOLD', '# Zeta Violin\n## Description\nSOLD',
                            '# Zeta Violin\n## Related listings\nSOLD', '# Zeta Violin\nSold as is'):
            self.assertFalse(indexed_closed_reason({**item,'description':description}))
        self.assertTrue(index_card_negative(url,'SOLD'))
        self.assertTrue(index_card_negative(url,'This item has been sold.'))
        for phrase in ('SOLD as is', 'Previously sold separately', 'This gear has sold', 'Used violin'):
            self.assertFalse(index_card_negative(url,phrase))
        self.assertFalse(index_card_negative('https://offerup.com.evil.example/item/detail/123','SOLD'))
        self.assertTrue(index_card_negative('https://offerup.com/item/detail/123','SOLD'))
        self.assertFalse(index_card_negative('https://offerup.com/item/detail/abc','SOLD'))
        marker=index_card_negative(url,'SOLD')
        self.assertFalse(indexed_closed_reason({**item, **marker, 'url':url[:-1]+'6','description':''}))

    def test_reverb_status_scope_excludes_description_related_and_scripts(self):
        for other in ('<aside>This gear has sold</aside>', '<div class="related">This gear has sold</div>',
                      '<div class="suggested-items">This gear has sold</div>',
                      '<section aria-label="Similar listings">This gear has sold</section>',
                      '<script>"This gear has sold"</script>', '<h2>About this listing</h2><p>This gear has sold</p>'):
            item = sold_reverb()
            page = '<main><h1>'+item['title']+'</h1><button>Add to cart</button>'+other+'</main>'
            self.assertEqual(inspect_page(item,page,200,item['url'])[0],'live')
        item = {**sold_reverb(),'url':'https://reverb.com.evil.example/item/82035982'}
        page = '<main><h1>'+item['title']+'</h1><p>This gear has sold</p></main>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'unknown')

    def test_indexed_sold_requires_primary_heading_and_status_before_seller_prose(self):
        from offer_verifier import indexed_closed_reason
        item = sold_reverb()
        self.assertTrue(indexed_closed_reason(item))
        self.assertTrue(indexed_closed_reason({**item,'_discovery_description':'Used Zeta violin'}))
        self.assertTrue(indexed_closed_reason({**item,'description':'Used Zeta violin',
                                               '_discovery_description':item['description']}))
        for description in ('This gear has sold', '# Different violin\nSOLD',
                            '# '+item['title']+'\nAbout this listing\nSOLD',
                            '# '+item['title']+'\nPromoted similar listings\nSOLD',
                            '# '+item['title']+'\nUsed violin sold as is',
                            '# '+item['title']+'\nListed: 2 years ago\nUsed violin'):
            self.assertEqual(indexed_closed_reason({**item,'description':description}), '')
        for url in ('https://reverb.com.evil.example/item/82035982','https://other.example/item/82035982',
                    'https://reverb.com/news/article/item/82035982'):
            self.assertEqual(indexed_closed_reason({**item,'url':url}), '')

    def test_31_day_core_plan_and_auxiliary_surplus_share_unchanged_ceiling(self):
        start = 1790812800 + 3600
        self.assertTrue(reserve_work('exa',250000,8000000,250000,7750000,auxiliary=True,now=start))
        for day in range(31):
            self.assertTrue(reserve_work('exa',250000,8000000,250000,7750000,now=start+day*86400))
        self.assertEqual(used('exa',now=start+30*86400+1),8000000)
        self.assertFalse(reserve_work('exa',1,8000000,250000,7750000,auxiliary=True,now=start+30*86400+1))

    def test_negative_index_card_requires_same_reverb_item_url(self):
        from offer_verifier import indexed_closed_reason
        item = {**sold_reverb(), 'description':'Used Zeta violin'}
        bound = {'_indexed_availability_url':item['url'], '_indexed_availability_negative':'sold'}
        self.assertTrue(indexed_closed_reason({**item, **bound}))
        self.assertTrue(indexed_closed_reason({**item, **bound, '_indexed_availability_url':item['url']+'?foo=1'}))
        for change in ({'_indexed_availability_url':item['url'].replace('82035982','82035983')},
                       {'_indexed_availability_url':'https://reverb.com.evil.example/item/82035982'},
                       {'_indexed_availability_negative':'live'},
                       {'_indexed_availability_url':'https://[bad'},
                       {'url':'https://other.example/item/82035982'}):
            self.assertEqual(indexed_closed_reason({**item, **bound, **change}), '')

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

    def test_market_observations_are_exact_query_scoped_and_do_not_refresh_from_cache(self):
        from primary_page_reserve import remember_market_search, cached_market_search
        url='https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=zeta'
        result={'_live_crawled':True,'text':'1 Anzeige','extras':{'links':[]}}
        with patch('primary_page_reserve.time.time',return_value=10000):
            remember_market_search(url,result)
        with patch('primary_page_reserve.time.time',return_value=11000):
            cached=cached_market_search(url,'auxiliary')
            self.assertTrue(cached['_cached'])
            self.assertEqual(cached['_checked_at'],10000)
            remember_market_search(url,cached)
            self.assertEqual(cached_market_search(url,'again')['_checked_at'],10000)
            self.assertEqual(cached_market_search(url+'&PAGE=2','different page'),{})
            self.assertEqual(cached_market_search(url.replace('zeta','strados'),'different query'),{})
        with patch('primary_page_reserve.time.time',return_value=47000):
            self.assertEqual(cached_market_search(url,'expired'),{})

    def test_auxiliary_market_cache_makes_no_paid_reservation_or_http_request(self):
        from primary_page_reserve import remember_market_search, fresh_willhaben_search
        url='https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=zeta'
        remember_market_search(url,{'_live_crawled':True,'text':'0 Anzeigen','extras':{'links':[]}})
        client=SimpleNamespace(post=AsyncMock(side_effect=AssertionError('unexpected paid request')))
        with patch.object(Config,'EXA_API_KEY','fixture'),spending_scope('auxiliary'), \
             patch('primary_page_reserve.reserve_pages',side_effect=AssertionError('cache must not reserve')):
            result=asyncio.run(fresh_willhaben_search(url,client))
        self.assertTrue(result['_cached']);client.post.assert_not_awaited()

    def test_core_market_discovery_still_requests_fresh_content_and_replaces_cache(self):
        from primary_page_reserve import remember_market_search, fresh_willhaben_search
        url='https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=zeta'
        remember_market_search(url,{'_live_crawled':True,'text':'0 Anzeigen','extras':{'links':[]}})
        response=SimpleNamespace(status_code=200,json=lambda:{'statuses':[{'id':url,'status':'success','source':'crawled'}],
            'results':[{'url':url,'text':'1 Anzeige','extras':{'links':['/iad/kaufen-und-verkaufen/d/zeta-jv5-111']}}]})
        client=SimpleNamespace(post=AsyncMock(return_value=response))
        with patch.object(Config,'EXA_API_KEY','fixture'),spending_scope('core'), \
             patch('primary_page_reserve.reserve_pages',return_value=True):
            result=asyncio.run(fresh_willhaben_search(url,client))
        client.post.assert_awaited_once()
        self.assertTrue(result['_live_crawled']);self.assertNotIn('_cached',result)
        self.assertEqual(result['text'],'1 Anzeige')

    def test_unavailable_live_market_keeps_old_observation_labelled_without_spend(self):
        from primary_page_reserve import remember_market_search, fresh_gumtree_search
        url='https://www.gumtree.com/search?search_category=all&q=zeta'
        remember_market_search(url,{'_live_crawled':True,'text':'0 ads for zeta','extras':{'links':[]}})
        client=SimpleNamespace(post=AsyncMock(side_effect=AssertionError('budget denied')))
        with patch.object(Config,'EXA_API_KEY','fixture'),spending_scope('core'), \
             patch('primary_page_reserve.reserve_pages',return_value=False):
            result=asyncio.run(fresh_gumtree_search(url,client))
        self.assertTrue(result['_cached'])
        self.assertIn('reservation unavailable',result['_cache_reason'])
        client.post.assert_not_awaited()

    def test_optional_market_cache_failure_cannot_stop_current_result(self):
        from primary_page_reserve import remember_market_search, cached_market_search
        with patch('primary_page_reserve.write',side_effect=RuntimeError('fixture locked')):
            remember_market_search('https://www.gumtree.com/search?q=zeta',{'_live_crawled':True,'text':'0 ads for zeta'})
        with patch('primary_page_reserve.read',side_effect=RuntimeError('fixture locked')):
            self.assertEqual(cached_market_search('https://www.gumtree.com/search?q=zeta','aux'),{})
        with patch('primary_page_reserve.read',return_value={'checked':time.time(), 'result':{'url':'https://[bad','text':'0 ads'}}):
            self.assertEqual(cached_market_search('https://www.gumtree.com/search?q=zeta','aux'),{})

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

    def test_brave_cache_and_manual_rows_cannot_consume_core_daily_allowance(self):
        from search_budget import reserve_request
        from status_tracker import StatusTracker
        tracker=StatusTracker()
        conn=tracker.conn
        conn.execute('CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY,last_run TEXT)')
        conn.execute('DELETE FROM scraper_runs');conn.execute('DELETE FROM scraper_stats')
        conn.execute("INSERT INTO scraper_runs VALUES ('brave_quota:2026-10','704')")
        for attempts,manual,aux in ((16,True,False),(0,False,False),(0,False,False),(3,False,True)):
            conn.execute('INSERT INTO scraper_stats (scraper,raw_count,run_at,details) VALUES (?,?,?,?)',
                         ('Brave Search',0,'2026-10-27T04:00:00',json.dumps(
                             {'discovery_queries_attempted':attempts,'manual_fresh':manual,'auxiliary_work':aux})))
        conn.commit()
        try:
            with patch('search_budget.datetime') as clock:
                clock.now.return_value=datetime(2026,10,27,8,tzinfo=timezone.utc)
                for _ in range(32): self.assertTrue(reserve_request('brave',1000))
                self.assertFalse(reserve_request('brave',1000))
            self.assertEqual(conn.execute("SELECT last_run FROM scraper_runs WHERE scraper='brave_quota:2026-10'").fetchone()[0],'736')
        finally:
            conn.execute('DELETE FROM scraper_stats');conn.commit();conn.close()

    def test_brave_real_core_legacy_and_existing_ledgers_remain_conservative(self):
        from search_budget import reserve_request
        from status_tracker import StatusTracker
        conn=StatusTracker().conn
        conn.execute('CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY,last_run TEXT)')
        try:
            with patch('search_budget.datetime') as clock:
                clock.now.return_value=datetime(2026,10,27,20,tzinfo=timezone.utc)
                for details,expected in (({'discovery_queries_attempted':16},16),({},0),
                                         ({'discovery_queries_attempted':-1},0)):
                    conn.execute('DELETE FROM scraper_runs');conn.execute('DELETE FROM scraper_stats')
                    conn.execute('INSERT INTO scraper_stats (scraper,raw_count,run_at,details) VALUES (?,?,?,?)',
                                 ('Brave Search',0,'2026-10-27T08:00:00',json.dumps(details)));conn.commit()
                    for _ in range(expected): self.assertTrue(reserve_request('brave',1000))
                    self.assertFalse(reserve_request('brave',1000))
                conn.execute('DELETE FROM scraper_stats');conn.execute('DELETE FROM scraper_runs')
                conn.execute("INSERT INTO scraper_runs VALUES ('brave_daily:2026-10-27','32')");conn.commit()
                self.assertFalse(reserve_request('brave',1000))
                self.assertEqual(conn.execute("SELECT last_run FROM scraper_runs WHERE scraper='brave_daily:2026-10-27'").fetchone()[0],'32')
        finally:
            conn.execute('DELETE FROM scraper_stats');conn.commit();conn.close()

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

    def test_unbound_singleton_cannot_close_primary_or_supply_identity(self):
        from filters import classify
        cases = [
            ('Zeta Jazz Fusion JV5','Used Zeta JV5 violin for sale $1500','Other product','SoldOut','live',True),
            ('Black case','Only a hard case for sale $100','Zeta Jazz Fusion JV5','InStock','live',False),
            ('Zeta Jazz Fusion JV5','Used Zeta JV5 violin','Other product','InStock','unknown',True),
        ]
        for title,body,name,availability,state,eligible in cases:
            with self.subTest(title=title,availability=availability,body=body):
                item={**violin(),'title':title}
                product={'@type':'Product','name':name,'brand':'Zeta','model':'JV5',
                         'itemCondition':'https://schema.org/NewCondition',
                         'offers':{'price':9999,'availability':'https://schema.org/'+availability}}
                page='<main><h1>'+title+'</h1><p>'+body+'</p><script type="application/ld+json">'+json.dumps(product)+'</script></main>'
                self.assertEqual(inspect_page(item,page,200,item['url'])[0],state)
                self.assertEqual(item['title'],title)
                self.assertNotEqual(item.get('price'),'9999')
                self.assertNotEqual(item.get('condition'),'Brand New')
                self.assertEqual(classify(item)=='',eligible)

    def test_primary_url_alone_binds_generic_heading_among_recommendations(self):
        item=violin()
        primary={'@type':'Product','url':item['url'],'name':'Zeta JV5',
                 'brand':'Zeta','model':'JV5','description':'Used five-string electric violin',
                 'offers':{'price':1500,'availability':'https://schema.org/InStock'}}
        related={'@type':'Product','name':'Rare black instrument','url':'https://example.com/item/other',
                 'offers':{'availability':'https://schema.org/SoldOut'}}
        page='<main><h1>Rare black instrument</h1><script type="application/ld+json">'+json.dumps([related,primary])+'</script></main>'
        self.assertEqual(inspect_page(item,page,200,item['url'])[0],'live')
        self.assertEqual(item['model'],'JV5')
        self.assertEqual(item['price'],'1500')

    def test_duplicate_primary_stock_representations_do_not_close_available_offer(self):
        for order in (('SoldOut','InStock'),('InStock','SoldOut')):
            item=violin()
            products=[{'@type':'Product','url':item['url'],'name':item['title'],
                       'offers':{'availability':'https://schema.org/'+state,'price':1500}}
                      for state in order]
            page='<h1>'+item['title']+'</h1><script type="application/ld+json">'+json.dumps(products)+'</script>'
            self.assertEqual(inspect_page(item,page,200,item['url'])[0],'live')
        item=violin()
        for product in products:
            product['offers']['availability']='https://schema.org/SoldOut'
        self.assertEqual(inspect_page(item,'<h1>'+item['title']+'</h1><script type="application/ld+json">'+json.dumps(products)+'</script>',200,item['url'])[0],'dead')

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

    def test_apify_forecast_counts_actual_upcoming_slots_in_account_cycle(self):
        from resource_monitor import apify_forecast
        account={'usage_cycle':{'endAt':'2026-11-05T00:00:00Z'},'included_monthly_usd':5,'usage_usd':4.12}
        with patch.multiple(Config,SEARCH_HOURS='10,22',SEARCH_TIMEZONE='Europe/Bucharest',
                            APIFY_DISCOVERY_CAP_USD=.065,APIFY_DETAILS_CAP_USD=.02):
            result=apify_forecast(account,datetime(2026,10,27,tzinfo=timezone.utc))
        self.assertEqual(result,{'scheduled_cycles':18,'scheduled_cap_usd':1.53,
                                 'included_remaining_usd':.88,'cap_above_included_usd':.65})
        self.assertEqual(apify_forecast({},datetime(2026,10,27,tzinfo=timezone.utc)),{})

    def test_forecast_rejects_invalid_cycle_and_matches_scheduler_duplicate_hours(self):
        from resource_monitor import apify_forecast
        account={'usage_cycle':{},'included_monthly_usd':5,'usage_usd':4.12}
        now=datetime(2026,10,27,tzinfo=timezone.utc)
        for invalid in (None,12,[],{},'bad'):
            with self.subTest(invalid=invalid):
                self.assertEqual(apify_forecast({**account,'usage_cycle':{'endAt':invalid}},now),{})
        account['usage_cycle']['endAt']='2026-10-28T00:00:00Z'
        with patch.multiple(Config,SEARCH_HOURS='10,10,22',SEARCH_TIMEZONE='Europe/Bucharest'):
            self.assertEqual(apify_forecast(account,now)['scheduled_cycles'],2)

    def test_google_matrix_keeps_old_active_inventory_without_changing_query_slots(self):
        from scrapers.google import GoogleScraper, GLOBAL_QUERIES
        from scrapers.brave import BraveScraper
        with patch.multiple(Config,GOOGLE_QUERIES_PER_RUN=48,BRAVE_QUERIES_PER_RUN=48):
            google,_=GoogleScraper('fixture','fixture').plan_queries(0)
            brave,_=BraveScraper().plan_queries(0)
        self.assertEqual([q for q,_ in google],[q for q,_ in brave])
        self.assertTrue(all('dateRestrict' not in p for _,p in google[len(GLOBAL_QUERIES):]))

    def test_easy_live_primary_closed_cannot_be_overridden_by_price_or_watch_buttons(self):
        url='https://www.easyliveauction.com/catalogue/lot/abc/def/music-lot-3457/'
        page='''<h1>Zeta electric violin</h1><h4>Hammer</h4><p>£3400</p><p>Watch Lot · Price Guide</p>
          <div class="auction-section"><div class="lot-status"><div><div class="text-center status">
          <span class="h4 text-warning"><strong>Closed</strong></span></div></div> Auction Date:12th Jun26</div></div>'''
        state,reason=inspect_page({**violin(),'url':url},page,200,url)
        self.assertEqual(state,'dead');self.assertIn('Easy Live Auction',reason)

    def test_easy_live_related_closed_does_not_close_active_primary(self):
        url='https://www.easyliveauction.com/catalogue/lot/abc/def/music-lot-3457/'
        page='''<main><h1>Zeta electric violin</h1><p>Current bid £1500</p><button>Place bid</button>
        <div class="auction-section"><div class="lot-status"><div class="status">Open</div></div></div>
        <aside><div class="auction-section"><div class="lot-status"><div class="status">Closed</div></div></div></aside></main>'''
        self.assertEqual(inspect_page({**violin(),'url':url},page,200,url)[0],'live')

    def test_replacement_push_cannot_keep_an_old_closed_receipt_after_uncertain_post(self):
        from alert_receipts import AlertReceipts
        receipts=AlertReceipts()
        try:
            rid=receipts.prepare('replacement-owner',violin(117799),'fixture')
            receipts.push_starting(rid);receipts.push_sent(rid,'A'*30,now=1000);receipts.push_finished(rid)
            receipts.push_starting(rid)  # new POST response may never be received
            self.assertEqual(receipts.conn.execute('SELECT push_receipt,push_expires,push_closed FROM alert_receipts WHERE id=?',(rid,)).fetchone(),(None,None,None))
            receipts.close_technically(rid,'replacement-owner','closed item')
            receipts.push_finished(rid)  # cancellation can race a lost POST response
            receipts.push_failed(rid)
            self.assertTrue(receipts.push_active('replacement-owner')[0]['technically_closed'])
            self.assertIsNone(receipts.conn.execute('SELECT acknowledged_at FROM alert_receipts WHERE id=?',(rid,)).fetchone()[0])
        finally:
            receipts.close()

    def test_tavily_usage_preserves_unlimited_key_and_only_whitelisted_totals(self):
        from provider_metering import tavily_usage
        data={'key':{'usage':61,'limit':None,'api_key':'do-not-store'},
              'account':{'plan_usage':61,'plan_limit':1500,'paygo_usage':0,'paygo_limit':0,
                         'current_plan':'Researcher','email':'do-not-store'},'token':'do-not-store'}
        saved=tavily_usage(data,100)
        self.assertIsNone(saved['key']['limit'])
        self.assertEqual(saved['account']['plan_limit'],1500)
        self.assertNotIn('do-not-store',json.dumps(saved))

    def test_tavily_malformed_usage_does_not_become_zero_or_unlimited(self):
        from provider_metering import tavily_usage
        for invalid in ({}, {'key':{'usage':None,'limit':None},'account':{'plan_usage':1,'plan_limit':1000}},
                        {'key':{'usage':1,'limit':True},'account':{'plan_usage':1,'plan_limit':1000}},
                        {'key':{'usage':float('nan'),'limit':1000},'account':{'plan_usage':1,'plan_limit':1000}}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                tavily_usage(invalid,100)

    def test_tavily_zero_key_cap_is_different_from_no_key_cap(self):
        from resource_monitor import warning_events
        account={'checked':time.time(),'key':{'usage':0,'limit':None},'account':{'plan_usage':1,'plan_limit':1000}}
        self.assertFalse(any(key.startswith('tavily:account:') for key,_ in warning_events({'tavily_account':account})))
        account['key']['limit']=0
        self.assertTrue(any(key=='tavily:account:3' for key,_ in warning_events({'tavily_account':account})))

    def test_tavily_resource_display_dates_and_labels_stale_totals(self):
        from resource_monitor import text
        data={'pushover':{'configured':False},'tavily_account':{
            'checked':time.time()-13*3600,'key':{'usage':61,'limit':None},
            'account':{'plan_usage':61,'plan_limit':1500}}}
        with patch('resource_monitor.snapshot',return_value=data):
            result=text()
        self.assertIn('verificat',result)
        self.assertIn('Tavily pot fi vechi',result)

    def test_queue_age_distinguishes_due_unclaimed_work_from_saved_rechecks(self):
        from resource_monitor import snapshot
        now=time.time();namespace='search:MetricsFixture'
        with DiscoveryQueue(namespace) as q:
            q.conn.execute('DELETE FROM discovery_queue WHERE namespace=?',(namespace,));q.conn.commit()
            q.put([violin(701),violin(702),violin(703)])
            for number,age,next_attempt,attempts in ((701,100,now-1,4),(702,12,now+3600,0),(703,4,now-1,0)):
                q.conn.execute('UPDATE discovery_queue SET created=?,next_attempt=?,attempts=? WHERE namespace=? AND key=?',
                    (now-age*3600,next_attempt,attempts,namespace,violin(number)['url']))
            q.conn.commit()
            before=q.conn.execute('SELECT key,created,next_attempt,attempts FROM discovery_queue WHERE namespace=? ORDER BY key',(namespace,)).fetchall()
            with patch('resource_monitor.time.time',return_value=now):
                metrics=snapshot()['deferred_details'][namespace]
            self.assertEqual(metrics,{'saved':3,'due':2,'never_claimed':2,'oldest_saved_hours':100.0,
                                      'due_never_claimed':1,'oldest_due_never_claimed_hours':4.0})
            self.assertEqual(before,q.conn.execute('SELECT key,created,next_attempt,attempts FROM discovery_queue WHERE namespace=? ORDER BY key',(namespace,)).fetchall())
            q.finish(violin(703),3600)
            metrics=snapshot()['deferred_details'][namespace]
            self.assertEqual(metrics['due'],1)
            self.assertEqual(metrics['due_never_claimed'],0)
            self.assertIsNone(metrics['oldest_due_never_claimed_hours'])

    def condition_page(self, product_condition=None, offer_condition=None, *, offers=None,
                       native='Used', name='Zeta Jazz Fusion JV5', product_url=ITEM_URL, related=''):
        product={'@type':'Product','name':name,'url':product_url,'brand':'Zeta','model':'JV5',
                 'description':'Used electric violin. New strings fitted.',
                 'offers':offers if offers is not None else {'price':'1500','priceCurrency':'USD',
                    'availability':'https://schema.org/InStock'}}
        if product_condition is not None:product['itemCondition']=product_condition
        if offer_condition is not None:product['offers']['itemCondition']=offer_condition
        item={**violin(),'url':ITEM_URL,'condition':native}
        page='<main><h1>Zeta Jazz Fusion JV5</h1><script type="application/ld+json">'+json.dumps(product)+'</script>'+related+'<p>Price €1500</p></main>'
        state,why=inspect_page(item,page,200,ITEM_URL)
        return item,state,why

    def test_primary_item_condition_overrides_index_and_offer_wins_product(self):
        from filters import classify
        for product,offer,expected in [('NewCondition',None,'Brand New'),
                                       (None,'https://schema.org/NewCondition','Brand New'),
                                       ('NewCondition','UsedCondition','Used'),
                                       ('UsedCondition',{'@id':'https://schema.org/NewCondition'},'Brand New')]:
            with self.subTest(product=product,offer=offer):
                item,state,_=self.condition_page(product,offer)
                self.assertEqual(state,'live');self.assertEqual(item['condition'],expected)
                self.assertEqual(classify(item),'new_stock' if expected=='Brand New' else '')
        item,_,_=self.condition_page('NewCondition',offers={
            'price':1500,'url':'https://checkout.example/used-offer','itemCondition':'UsedCondition'})
        self.assertEqual(item['condition'],'Used');self.assertEqual(classify(item),'')

    def test_mixed_or_partly_unknown_offer_conditions_remain_uncertain(self):
        from filters import classify
        for conditions in (('NewCondition','UsedCondition'),('NewCondition',None)):
            offers=[{'price':1000,'availability':'https://schema.org/InStock',
                     **({'itemCondition':condition} if condition else {})} for condition in conditions]
            item,state,why=self.condition_page(offers=offers)
            self.assertEqual(state,'unknown');self.assertIn('nou/folosit',why)
            self.assertTrue(item['_primary_content_checked'])
            self.assertEqual(classify(item),'')

    def test_recommended_or_unmatched_product_cannot_supply_new_condition(self):
        from filters import classify
        related='<aside><script type="application/ld+json">'+json.dumps({'@type':'Product','name':'Other product',
             'itemCondition':'NewCondition','offers':{'price':3000}})+'</script></aside>'
        item,_,_=self.condition_page(related=related,native='Like New')
        self.assertEqual(item['condition'],'Like New');self.assertEqual(classify(item),'')
        for url in ('','   ','#recommendation','https://example.com/item/other'):
            item,_,_=self.condition_page('NewCondition',name='Different instrument name',product_url=url,native='Like New')
            self.assertEqual(item['condition'],'Like New');self.assertEqual(classify(item),'')

    def test_microdata_condition_respects_product_and_offer_scope(self):
        from filters import classify
        for product,offer,expected in [('NewCondition','UsedCondition','Used'),('UsedCondition','NewCondition','Brand New')]:
            page=f'''<main><h1>Zeta Jazz Fusion JV5</h1><div itemscope itemtype="https://schema.org/Product">
             <meta itemprop="name" content="Zeta Jazz Fusion JV5"><link itemprop="url" href="{ITEM_URL}">
             <link itemprop="itemCondition" href="https://schema.org/{product}">
             <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
              <meta itemprop="price" content="1500"><meta itemprop="priceCurrency" content="EUR">
              <meta itemprop="itemCondition" content="https://schema.org/{offer}"></div></div></main>'''
            item={**violin(),'url':ITEM_URL,'condition':'Used'}
            self.assertEqual(inspect_page(item,page,200,ITEM_URL)[0],'live')
            self.assertEqual(item['condition'],expected)
            self.assertEqual(classify(item),'new_stock' if expected=='Brand New' else '')

    def test_unknown_condition_new_strings_refurbished_and_damaged_are_not_new(self):
        from filters import classify
        for condition in ('UnknownCondition','RefurbishedCondition','DamagedCondition',
                          {'@id':'https://schema.org/UsedCondition'},'https://evil.example/NewCondition',
                          'https://[invalid','https://schema.org/not-schema/NewCondition',
                          'https://schema.org/NewCondition/',True):
            with self.subTest(condition=condition):
                item,_,_=self.condition_page(condition,native='Like New')
                self.assertEqual(classify(item),'')
        item,_,_=self.condition_page('NewCondition','UnknownCondition',native='Like New')
        self.assertEqual(item['condition'],'Like New');self.assertEqual(classify(item),'')

    def test_microdata_without_actual_url_or_h1_does_not_invent_condition_identity(self):
        from filters import classify
        page='''<main><div itemscope itemtype="https://schema.org/Product">
          <meta itemprop="name" content="Other instrument"><meta itemprop="brand" content="Zeta">
          <meta itemprop="model" content="JV5"><link itemprop="itemCondition" href="https://schema.org/NewCondition">
          <div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><meta itemprop="price" content="1500"></div>
        </div></main>'''
        item={**violin(),'condition':'Used'}
        inspect_page(item,page,200,item['url'])
        self.assertEqual(item['condition'],'Used');self.assertEqual(classify(item),'')

    def test_duplicate_primary_representations_with_conflicting_conditions_are_order_independent(self):
        from filters import classify
        for values in (('NewCondition','UsedCondition'),('UsedCondition','NewCondition'),('NewCondition','UnknownCondition')):
            products=[{'@type':'Product','name':'Zeta Jazz Fusion JV5','url':ITEM_URL,'itemCondition':value,
                       'offers':{'price':1500,'availability':'https://schema.org/InStock'}} for value in values]
            item={**violin(),'url':ITEM_URL,'condition':'Used'}
            page='<main><h1>Zeta Jazz Fusion JV5</h1><script type="application/ld+json">'+json.dumps(products)+'</script></main>'
            self.assertEqual(inspect_page(item,page,200,ITEM_URL)[0],'unknown')
            self.assertEqual(classify(item),'')

    def test_malformed_metadata_does_not_hide_later_products_or_primary_body(self):
        from filters import classify
        for offers in ('not-an-offer',[None,'invalid',{'price':1500,'itemCondition':'UsedCondition'}]):
            products=[{'@type':None}, {'@type':17}, {'@type':'Product','name':'Zeta Jazz Fusion JV5',
                      'url':ITEM_URL,'itemCondition':'UsedCondition','offers':offers}]
            item={**violin(),'url':ITEM_URL}
            page='<main><h1>Zeta Jazz Fusion JV5</h1><script type="application/ld+json">'+json.dumps(products)+'</script><div id="description">Used Zeta violin for sale. New strings fitted.</div><p>Price €1500</p></main>'
            self.assertEqual(inspect_page(item,page,200,ITEM_URL)[0],'live')
            self.assertEqual(item['condition'],'Used');self.assertEqual(classify(item),'')

    def test_closed_variant_does_not_hide_available_primary_offer(self):
        from filters import classify
        closed={'price':1500,'availability':'https://schema.org/SoldOut','itemCondition':'UsedCondition'}
        active={**closed,'availability':'https://schema.org/InStock'}
        for offers in ([closed,active],[active,closed]):
            item,state,_=self.condition_page(offers=offers)
            self.assertEqual(state,'live');self.assertEqual(classify(item),'')
        self.assertEqual(self.condition_page(offers=[closed,closed])[1],'dead')

    def test_nested_offer_conditions_are_uncertain_instead_of_assumed_new(self):
        from filters import classify
        item,state,_=self.condition_page('NewCondition',offers={'@type':'AggregateOffer',
            'offers':[{'price':1500,'itemCondition':'UsedCondition'}]})
        self.assertEqual(state,'unknown');self.assertEqual(classify(item),'')
        product={'@type':'Product','name':'Zeta Jazz Fusion JV5','url':ITEM_URL,'brand':'Zeta','model':'JV5',
                 'itemCondition':'NewCondition','offers':{'@type':'AggregateOffer','availability':'https://schema.org/SoldOut',
                 'offers':[{'itemCondition':'UsedCondition','availability':'https://schema.org/InStock'}]}}
        item={**violin(),'title':'Black instrument','url':ITEM_URL}
        page='<main><h1>Black instrument</h1><script type="application/ld+json">'+json.dumps(product)+'</script><p>Price €1500</p></main>'
        self.assertEqual(inspect_page(item,page,200,ITEM_URL)[0],'unknown')
        self.assertEqual(classify(item),'')


class QueueFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import main
        self.main = main
        with store() as conn:
            conn.execute("DELETE FROM provider_state WHERE key LIKE 'availability:indexed_closed:%'")
            conn.commit()
        with DiscoveryQueue('search:Fixture') as q:
            q.conn.execute('DELETE FROM discovery_queue');q.conn.commit()
        db = database.Database()
        db.conn.execute('DELETE FROM alerts');db.conn.execute('DELETE FROM pending_alerts');db.conn.commit();db.close()

    async def test_indexed_reverb_sold_is_retained_without_alert_when_primary_is_blocked(self):
        item = sold_reverb()
        with DiscoveryQueue('search:Fixture') as queue:
            queue.put([item])
            queue.claim(1)
        db = database.Database()
        dropped = {}
        try:
            with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                    result = await self.main._verify_search_hits([item],db,client,dropped,'search:Fixture')
            self.assertEqual(result,[])
            self.assertEqual(db.pending(),[])
            self.assertFalse(db.was_url_alerted(item['url']))
            self.assertEqual(dropped,{'availability_conflict':1})
            self.assertTrue(item['_availability_conflict'])
            self.assertEqual(self.main.verification_counts(dropped)['rejected'],{})
            with DiscoveryQueue('search:Fixture') as queue:
                self.assertEqual(queue.conn.execute('SELECT count(*) FROM discovery_queue').fetchone()[0],1)
                self.assertEqual(len(queue.claim(1,now=time.time()+3601)),1)
        finally:
            db.close()

    async def test_current_primary_live_api_supersedes_old_index_sold(self):
        from offer_verifier import verify_offer, availability_conflict
        item = sold_reverb()
        item['_availability_conflict'] = 'previous blocked check'
        data = {'state':{'slug':'live'},'title':item['title'],'description':'Used Zeta Jazz Classic electric violin',
                'condition':{'display_name':'Excellent'},'price':{'amount':'1500','currency':'USD'}}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
                state,_ = await verify_offer(item,client)
        self.assertEqual(state,'live')
        self.assertFalse(availability_conflict(item,state))
        self.assertNotIn('_availability_conflict',item)

    async def test_negative_search_card_is_held_when_blocked_but_current_live_wins(self):
        from offer_verifier import verify_offer, availability_conflict
        item = {**sold_reverb(), 'description':'This gear has sold',
                '_indexed_availability_url':sold_reverb()['url'], '_indexed_availability_negative':'sold'}
        data = {'state':{'slug':'live'},'title':item['title'],'description':'Used Zeta Jazz Classic electric violin',
                'condition':{'display_name':'Excellent'},'price':{'amount':'1500','currency':'USD'}}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                state, _ = await verify_offer(item, client)
            self.assertEqual(state,'unknown')
            self.assertTrue(availability_conflict(item,state))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
                state, _ = await verify_offer(item, client)
            self.assertEqual(state,'live')
            self.assertFalse(availability_conflict(item,state))

    async def test_gumtree_primary_node_recovery_precedes_paid_reserve(self):
        from offer_verifier import verify_offer
        item = {**violin(), 'url':'https://www.gumtree.com/p/violins/zeta-jv5/123456789'}
        primary = '<main><h1>Zeta Jazz Fusion JV5</h1><p>Used Zeta 5-string electric violin. Price £1500. For sale.</p></main>'
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('public_node_fetch.fetch',AsyncMock(return_value=(200,primary,item['url']))) as node, \
             patch('primary_page_reserve.fresh_primary_page',new_callable=AsyncMock) as reserve:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(247))) as client:
                state,reason = await verify_offer(item,client)
        self.assertEqual(state,'live')
        self.assertTrue(reason.startswith('Public Node:'))
        self.assertTrue(item['_primary_content_checked'])
        node.assert_awaited_once_with(item['url'])
        reserve.assert_not_awaited()

    async def test_gumtree_node_failure_preserves_existing_reserve(self):
        from offer_verifier import verify_offer
        item = {**violin(), 'url':'https://www.gumtree.com/p/violins/zeta-jv5/123456789'}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('public_node_fetch.fetch',AsyncMock(side_effect=RuntimeError('unavailable'))), \
             patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value=None)) as reserve:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                state,_ = await verify_offer(item,client)
        self.assertEqual(state,'unknown')
        reserve.assert_awaited_once_with(item['url'],client)

    async def test_negative_index_evidence_survives_rediscovery_and_other_provider_queue(self):
        from offer_verifier import remember_indexed_closed, verify_offer, availability_conflict, saved_indexed_closed_reason
        old = sold_reverb()
        remember_indexed_closed({**old, 'url':old['url']+'?foo=1'})
        plain = {**old, 'description':'Used Zeta violin', 'id':'different-provider', 'source':'search'}
        with DiscoveryQueue('search:Fixture') as queue:
            queue.put([old])
            queue.put([plain])
            current = queue.claim(1)[0]
        self.assertNotIn('SOLD',current['description'])
        other = {**plain, 'url':'https://reverb.com/item/82035983-other'}
        self.assertEqual(saved_indexed_closed_reason(other),'')
        data = {'state':{'slug':'live'},'title':plain['title'],'description':'Used Zeta electric violin',
                'condition':{'display_name':'Excellent'},'price':{'amount':'1500','currency':'USD'}}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                state,_ = await verify_offer(current, client)
            self.assertEqual(state,'unknown')
            self.assertTrue(availability_conflict(current,state))
            self.assertTrue(availability_conflict(plain,'unknown'))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
                state,_ = await verify_offer(plain, client)
            self.assertEqual(state,'live')
            self.assertEqual(saved_indexed_closed_reason(plain),'')
            self.assertFalse(availability_conflict(plain,'unknown'))

    async def test_offerup_indexed_sold_persists_on_403_and_live_primary_clears(self):
        from offer_verifier import remember_indexed_closed, verify_offer, availability_conflict, saved_indexed_closed_reason
        url='https://offerup.com/item/detail/3b32aed8-b98b-3c09-a73b-2f220d215b75'
        old={**violin(), 'url':url+'?foo=1', 'title':'Zeta Violin for Sale in Hemet, CA - OfferUp',
             'description':'# Zeta Violin\n## Additional images\nSOLD\n## Description\nUsed'}
        remember_indexed_closed(old)
        plain={**violin(), 'url':url, 'title':'Zeta Violin'}
        self.assertTrue(saved_indexed_closed_reason(plain))
        other={**plain, 'url':url[:-1]+'6'}
        self.assertFalse(saved_indexed_closed_reason(other))
        page='<main><h1>Zeta Violin</h1><p id="description">Used Zeta violin</p><p>For sale $900</p></main>'
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value=None)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                state,_ = await verify_offer(plain,client)
            self.assertEqual(state,'unknown')
            self.assertTrue(availability_conflict(plain,state))
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=page))) as client:
                state,_ = await verify_offer(plain,client)
            self.assertEqual(state,'live')
            self.assertFalse(saved_indexed_closed_reason(plain))

    async def test_cached_unknown_sold_outbox_cannot_bypass_the_hold(self):
        from price_tracker import PriceTracker
        item = {**sold_reverb(),'verification':'unknown'}
        db = database.Database();prices=PriceTracker()
        notifier=SimpleNamespace(send_listings=AsyncMock(side_effect=lambda xs:xs))
        try:
            self.assertEqual(await self.main._deliver(notifier,db,prices,[item]),0)
            notifier.send_listings.assert_not_awaited()
            self.assertEqual([x['url'] for x in db.pending()],[item['url']])
            self.assertFalse(db.was_url_alerted(item['url']))
        finally:
            db.close();prices.close()

    async def test_sold_conflict_blocks_both_cycle_price_drop_routes_and_minute_retry(self):
        from notifier import TelegramNotifier
        item=sold_reverb();info={'old_price':1600,'new_price':1500}
        for recovered in (True,False):
            db=database.Database()
            db.conn.execute("DELETE FROM pending_alerts WHERE url LIKE 'drop:%'");db.conn.commit()
            if recovered:
                db.enqueue_price_drop(item,info)
            db.close()
            result={'name':'Fixture','new':[],'drops':[] if recovered else [(item,info)],
                    'status':0,'raw':0,'duration':0,'details':{}}
            send=AsyncMock()
            with patch.object(TelegramNotifier,'send_price_drops',send), \
                 patch.object(TelegramNotifier,'send_listings',AsyncMock()), \
                 patch.object(TelegramNotifier,'send_no_changes',AsyncMock()), \
                 patch.object(TelegramNotifier,'send_reminders',AsyncMock()), \
                 patch('main.verify_offer',AsyncMock(return_value=('unknown','HTTP 403'))), \
                 patch('main.build_scrapers',return_value=[] if recovered else [SimpleNamespace()]), \
                 patch('main._run_scraper_with_resilience',AsyncMock(return_value=result)), \
                 patch('main.refresh_rates',AsyncMock()),patch('main.detect_egress',AsyncMock()), \
                 patch('main._watchdog',AsyncMock()),patch('main.resource_tick',AsyncMock()), \
                 patch('main._maybe_weekly_digest',AsyncMock()):
                await self.main.run_search_cycle()
                await self.main.notification_tick()
            send.assert_not_awaited()
            db=database.Database()
            try:
                self.assertEqual(db.pending_price_drops(),[(item,info)])
            finally:
                db.close()

    async def test_unknown_without_closed_evidence_keeps_existing_alert_route(self):
        item = {**sold_reverb(),'description':'Used Zeta electric violin; case included'}
        db = database.Database()
        try:
            with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as client:
                    result = await self.main._verify_search_hits([item],db,client,{})
            self.assertEqual([x['url'] for x in result],[item['url']])
            self.assertEqual(result[0]['verification'],'unknown')
        finally:
            db.close()

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

    async def test_auction_verifier_sends_browser_headers_to_both_init_and_asset(self):
        from offer_verifier import verify_offer
        calls=[]
        def reply(request):
            calls.append(request)
            if request.url.path=='/': return httpx.Response(200,text='<script src="/main.fixture.js"></script>')
            if request.url.path=='/main.fixture.js': return httpx.Response(200,text='maestroApiKey:"anonymous-public-fixture"')
            if 'Chrome/' not in request.headers.get('User-Agent',''): return httpx.Response(403)
            return httpx.Response(200,json={'assetStatusCd':'SOLD'})
        item={**violin(), 'url':'https://www.govdeals.com/en/asset/370/12180'}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                self.assertEqual((await verify_offer(item,client))[0],'dead')
        self.assertEqual(len(calls),3)
        self.assertTrue(all('Chrome/' in r.headers['User-Agent'] for r in calls))

    async def test_empty_204_auction_detail_is_explicit_unknown_and_retained(self):
        from offer_verifier import verify_offer
        def reply(request):
            if request.url.path=='/': return httpx.Response(200,text='<script src="/main.fixture.js"></script>')
            if request.url.path=='/main.fixture.js': return httpx.Response(200,text='maestroApiKey:"anonymous-public-fixture"')
            return httpx.Response(204)
        item={**violin(),'url':'https://www.govdeals.com/en/asset/42/32464'}
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                state,reason=await verify_offer(item,client)
        self.assertEqual(state,'unknown')
        self.assertIn('fără detaliile',reason)

    async def test_outbox_uses_price_from_refreshed_primary_page(self):
        from price_tracker import PriceTracker
        item={**violin(889900),'price':'1000 USD'}
        async def verify(candidate,http):
            candidate['price']='1800 USD';candidate['_primary_content_checked']=True
            return 'live','primary price'
        notifier=SimpleNamespace(send_listings=AsyncMock(side_effect=lambda xs:xs))
        db=database.Database();prices=PriceTracker()
        try:
            with patch('main.verify_offer',side_effect=verify):
                self.assertEqual(await self.main._deliver(notifier,db,prices,[item]),1)
            self.assertEqual(db.conn.execute('SELECT price_usd FROM alerts WHERE id=?',(item['id'],)).fetchone()[0],1800)
            self.assertEqual(item['price_context']['price_usd'],1800)
        finally:
            db.close();prices.close()

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

    async def test_google_zero_attempt_allowance_hold_is_skipped_not_searched(self):
        from scrapers.google import GoogleScraper
        scraper=GoogleScraper('fixture','fixture');scraper.manual_fresh=True
        with patch('scrapers.google.reserve_request',return_value=False), \
             patch.object(scraper,'_quota_used_today',return_value=0), \
             patch.object(scraper,'_mark_run') as mark,patch.object(scraper,'_set_cursor') as cursor, \
             patch('httpx.AsyncClient.get',AsyncMock(side_effect=AssertionError('no HTTP after denied reservation'))):
            self.assertEqual(await scraper.search(),[])
        self.assertTrue(scraper.skipped)
        mark.assert_not_called();cursor.assert_not_called()

    async def test_technical_closure_survives_late_sends_without_false_owner_read(self):
        from alert_receipts import AlertReceipts
        receipts=AlertReceipts()
        try:
            rid=receipts.prepare('technical-owner',violin(445566),'fixture')
            receipts.delivered(rid,1,now=1000);receipts.push_starting(rid)
            self.assertFalse(receipts.close_technically(rid,'wrong-chat','closed lot'))
            self.assertTrue(receipts.close_technically(rid,'technical-owner','primary Closed; not owner read'))
            receipts.delivered(rid,1,now=2000)
            receipts.reminder_result(rid,True,now=2000)
            receipts.push_immediately(rid)
            receipts.push_sent(rid,'A'*30,now=2000)  # response raced the technical stop
            receipts.push_failed(rid)
            self.assertFalse(receipts.is_pending(rid))
            self.assertEqual(receipts.pending_count('technical-owner'),0)
            self.assertEqual(receipts.claim_due('technical-owner',now=10**10),[])
            self.assertEqual(receipts.claim_push_due('technical-owner',now=10**10),[])
            row=receipts.conn.execute('SELECT acknowledged_at,next_reminder,push_next_attempt FROM alert_receipts WHERE id=?',(rid,)).fetchone()
            self.assertEqual(row,(None,None,None))
            self.assertTrue(receipts.push_active('technical-owner')[0]['technically_closed'])
            from pushover_notifier import PushoverNotifier
            push=PushoverNotifier()
            with patch.object(push,'is_configured',return_value=True),patch.object(push,'cancel',AsyncMock(return_value=True)) as cancel, \
                 patch.object(push,'request',AsyncMock(side_effect=AssertionError('no new emergency'))):
                await push.tick('technical-owner')
                cancel.assert_awaited_once_with(rid)
            self.assertEqual(receipts.push_active('technical-owner'),[])
        finally:
            receipts.close()

    async def test_tavily_account_refresh_is_read_only_and_does_not_store_secret_fields(self):
        from resource_monitor import refresh_accounts
        from provider_state import read
        data={'key':{'usage':10,'limit':None,'api_key':'not-a-real-secret'},
              'account':{'plan_usage':10,'plan_limit':1500,'paygo_usage':0,'paygo_limit':0}}
        async def get(client,url,**kwargs):
            self.assertEqual(url,'https://api.tavily.com/usage')
            self.assertEqual(kwargs['headers']['Authorization'],'Bearer fixture')
            return httpx.Response(200,json=data,request=httpx.Request('GET',url))
        with patch('resource_monitor.claim_window',return_value=True), \
             patch.multiple(Config,APIFY_TOKEN='',PUSHOVER_APP_TOKEN='',TAVILY_API_KEY='fixture'), \
             patch('httpx.AsyncClient.get',get), \
             patch('httpx.AsyncClient.post',AsyncMock(side_effect=AssertionError('no paid POST'))):
            await refresh_accounts()
        saved=read('tavily:account')
        self.assertEqual(saved['account']['plan_usage'],10)
        self.assertNotIn('not-a-real-secret',json.dumps(saved))


if __name__ == '__main__':
    unittest.main()
