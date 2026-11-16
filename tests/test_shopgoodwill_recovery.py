"""Failed anonymous search POSTs, durable rotation and cancellation recovery."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import database
from discovery_queue import DiscoveryQueue
from scrapers.shopgoodwill import ShopGoodwillScraper, _pending_queries, PAGE_SIZE, MAX_PAGES

ITEM = {'itemId':42,'title':'Zeta Jazz Fusion JV5',
        'description':'Violon électrique à cinq cordes. Case only has minor scuffs.',
        'currentPrice':1000}


def response(items=None):
    return httpx.Response(200,json={'searchResults':{'items':items or []}})


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dbpatch = patch.object(database,'DB_PATH',str(Path(self.temp.name)/'fixture.db'))
        self.dbpatch.start();self.addCleanup(self.dbpatch.stop)

    async def run_search(self, queries, handler):
        scraper=ShopGoodwillScraper();original=scraper.make_client
        with patch('scrapers.shopgoodwill.market_queries',return_value=queries) as factory, \
             patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)), \
             patch('scrapers.shopgoodwill.asyncio.sleep',AsyncMock()):
            items=await scraper.search()
        factory.assert_called_once_with('en',broad=True,limit=8,extra=('electric violin',))
        return scraper,items

    async def test_timeout_recovers_exact_request_and_keeps_all_original_terms(self):
        terms=['zeta','strados','JV5','JLP','4-string','5-strings','Jean-Luc Ponty','sunburst']
        calls=[];failed=False
        def handle(request):
            nonlocal failed
            p=json.loads(request.content);calls.append(p)
            self.assertEqual(str(request.url),'https://buyerapi.shopgoodwill.com/api/Search/ItemListing')
            self.assertEqual(p['searchDescriptions'],'true')
            self.assertEqual(p['searchClosedAuctions'],'false')
            self.assertEqual(p['searchInternationalShippingOnly'],'false')
            self.assertEqual(p['pageSize'],40)
            if p['searchText']=='strados' and not failed:
                failed=True;raise httpx.ReadTimeout('controlled',request=request)
            return response([ITEM] if p['searchText']=='strados' else [])
        scraper,items=await self.run_search(terms,handle)
        self.assertEqual([p['searchText'] for p in calls],terms[:2]+terms[1:])
        self.assertEqual(calls[1],calls[2])
        self.assertEqual(len(items),1);self.assertEqual(items[0]['description'],ITEM['description'])
        self.assertEqual((scraper.requests_attempted,scraper.requests_ok,scraper.requests_recovered),(9,8,1))
        self.assertEqual(scraper.health_error(),'')
        self.assertEqual(_pending_queries([]),[])

    async def test_successful_retry_cannot_hide_another_unrecovered_failure(self):
        calls=[]
        def handle(request):
            q=json.loads(request.content)['searchText'];calls.append(q)
            if q=='blocked':return httpx.Response(403)
            if calls.count(q)==1:return httpx.Response(503)
            return response([ITEM])
        scraper,items=await self.run_search(['blocked','zeta'],handle)
        self.assertEqual(len(items),1)
        self.assertIn('HTTP 403',scraper.health_error())
        self.assertNotIn('HTTP 503',scraper.health_error())
        self.assertEqual([r['query'] for r in _pending_queries([])],['blocked'])

    async def test_rotated_failed_query_survives_restart_and_runs_after_original_plan(self):
        def unavailable(request):raise httpx.ReadTimeout('controlled',request=request)
        first,_=await self.run_search(['old JV5'],unavailable)
        self.assertEqual(first.discovery_queries_attempted,2)
        pending=_pending_queries([]);self.assertEqual(pending[0]['query'],'old JV5')
        calls=[]
        def recovered(request):
            q=json.loads(request.content)['searchText'];calls.append(q)
            return response([ITEM] if q=='old JV5' else [])
        with patch('scrapers.shopgoodwill.time.time',return_value=time.time()+3601):
            scraper,items=await self.run_search(['new JLP','new sunburst'],recovered)
        self.assertEqual(calls,['new JLP','new sunburst','old JV5'])
        self.assertEqual(len(items),1);self.assertEqual(_pending_queries([]),[])
        self.assertEqual(scraper.coverage_stats['resumed_queries'],['old JV5'])

    async def test_cancellation_preserves_unfinished_terms_and_already_collected_cards(self):
        def handle(request):
            if json.loads(request.content)['searchText']=='JV5':raise asyncio.CancelledError()
            return response([ITEM])
        with self.assertRaises(asyncio.CancelledError):
            await self.run_search(['zeta','JV5','JLP'],handle)
        self.assertEqual({r['query'] for r in _pending_queries([])},{'JV5','JLP'})
        with DiscoveryQueue('search:ShopGoodwill') as queue:
            cards=queue.claim(12)
        self.assertEqual(len(cards),1)
        self.assertEqual(cards[0]['url'],'https://shopgoodwill.com/item/42')
        self.assertEqual(cards[0]['description'],ITEM['description'])

    async def test_invalid_schema_is_retained_instead_of_healthy_zero(self):
        for n,payload in enumerate(({}, {'searchResults':None}, {'searchResults':{'items':None}},
                                    {'searchResults':{'items':{}}})):
            with self.subTest(payload=payload):
                scraper,items=await self.run_search([f'q{n}'],lambda r:httpx.Response(200,json=payload))
                self.assertEqual(items,[])
                self.assertIn('schema',scraper.health_error())
        self.assertEqual(len(_pending_queries([])),4)
        scraper,items=await self.run_search(['healthy empty'],lambda r:response())
        self.assertEqual(items,[]);self.assertEqual(scraper.health_error(),'')

    async def test_rate_limit_respects_retry_after_without_losing_later_queries(self):
        now=time.time();calls=[]
        def handle(request):
            q=json.loads(request.content)['searchText'];calls.append(q)
            return httpx.Response(429,headers={'Retry-After':'120'}) if q=='limited' else response([ITEM])
        with patch('scrapers.shopgoodwill.time.time',return_value=now):
            scraper,items=await self.run_search(['limited','zeta'],handle)
            self.assertEqual(calls,['limited','zeta']);self.assertEqual(len(items),1)
            self.assertEqual(_pending_queries([])[0]['due'],now+120)
            second,_=await self.run_search(['limited'],handle)
            self.assertEqual(second.discovery_queries_attempted,0)
            self.assertEqual(calls,['limited','zeta'])
        with patch('scrapers.shopgoodwill.time.time',return_value=now+121):
            await self.run_search(['limited'],lambda r:response())
        self.assertEqual(_pending_queries([]),[])

    async def test_page_failure_replays_from_page_one_and_preserves_three_page_limit(self):
        calls=[]
        def fail_page(request):
            p=json.loads(request.content);calls.append(p['page'])
            if p['page']==2:raise httpx.ReadTimeout('controlled',request=request)
            return response([ITEM]*PAGE_SIZE)
        scraper,items=await self.run_search(['old query'],fail_page)
        self.assertEqual(calls,[1,2,2]);self.assertEqual(len(items),1)
        self.assertEqual(_pending_queries([])[0]['failed_page'],2)
        calls=[]
        def recover(request):
            p=json.loads(request.content);calls.append((p['searchText'],p['page']))
            return response([ITEM]*PAGE_SIZE) if p['searchText']=='old query' else response()
        with patch('scrapers.shopgoodwill.time.time',return_value=time.time()+3601):
            await self.run_search(['new query'],recover)
        self.assertEqual(MAX_PAGES,3)
        self.assertEqual(calls,[('new query',1),('old query',1),('old query',2),('old query',3)])
        self.assertEqual(_pending_queries([]),[])

    async def test_old_recovery_budget_never_replaces_the_current_eight_queries(self):
        _pending_queries([f'old{i}' for i in range(5)])
        terms=[f'current{i}' for i in range(8)];calls=[]
        def handle(request):calls.append(json.loads(request.content)['searchText']);return response()
        scraper,_=await self.run_search(terms,handle)
        self.assertEqual(calls,terms+['old0','old1'])
        self.assertEqual(scraper.coverage_stats['queries_pending'],3)

    async def test_invalid_item_does_not_discard_the_valid_violin_after_it(self):
        scraper,items=await self.run_search(['zeta'],lambda r:response([None,ITEM]))
        self.assertEqual(len(items),1)
        self.assertEqual(_pending_queries([])[0]['error'],'invalid item record')
        self.assertIn('invalid item',scraper.health_error())

    async def test_repeatedly_failed_replays_do_not_starve_other_old_terms(self):
        now=time.time()
        with patch('scrapers.shopgoodwill.time.time',return_value=now):
            _pending_queries(['old0','old1','old2','old3'])
        def blocked(request):return httpx.Response(403)
        with patch('scrapers.shopgoodwill.time.time',return_value=now+3601):
            first,_=await self.run_search(['current'],blocked)
        self.assertEqual(first.coverage_stats['resumed_queries'],['old0','old1'])
        with patch('scrapers.shopgoodwill.time.time',return_value=now+7202):
            second,_=await self.run_search(['current'],blocked)
        self.assertEqual(second.coverage_stats['resumed_queries'],['old2','old3'])


if __name__=='__main__':unittest.main()
