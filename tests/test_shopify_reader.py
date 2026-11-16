"""Fixed collection recovery and unchanged native search/stock rules."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch

import httpx
import database
from config import Config
from provider_state import store,used
from work_context import spending_scope
from shopify_reader import fetch_collection,_reserve,COLLECTIONS,ReaderUnavailable
from scrapers.shopify_dealers import ShopifyDealersScraper,STORES
from tests.test_willhaben_reader_contract import run_js,MODULE

BASE='http://will-reader.railway.internal:8080'
STORE='https://electricviolinshop.com'
URL=COLLECTIONS[(STORE,'used')][1]
PRODUCT={'id':42,'title':'Used Zeta Jazz Fusion JV5','handle':'zeta-jv5',
         'body_html':'<p>Violon électrique ancien. Case only has minor scuffs.</p>',
         'tags':['used'],'variants':[{'price':'1900.00','available':True}]}


def dto(**changes):
    return {'version':'shopify-reader-v1','requested':URL,'url':URL,'status':200,
        'body':json.dumps({'products':[PRODUCT]}),'checked':time.time(),
        'region':'us-east4-eqdc4a','location':None,**changes}


class CollectionReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for p in [patch.object(database,'DB_PATH',str(Path(self.temp.name)/'fixture.db')),
                  patch.object(Config,'WILLHABEN_READER_URL',BASE),
                  patch('shopify_reader._LIMIT',asyncio.Semaphore(2))]:
            p.start();self.addCleanup(p.stop)

    async def test_fixed_collection_payload_and_exact_successful_dto(self):
        calls=[]
        def handle(r):
            calls.append((str(r.url),json.loads(r.content)))
            self.assertEqual(r.headers['X-Shopify-Reader-Version'],'1')
            return httpx.Response(200,json=dto())
        with spending_scope('core'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
                self.assertEqual(await fetch_collection(STORE,'used',c),[PRODUCT])
        self.assertEqual(calls,[(BASE+'/read-shopify',{'collection':'evs-used'})])
        self.assertEqual(used('shopify_reader:core'),1)
        self.assertEqual(used('willhaben_reader'),0);self.assertEqual(used('gumtree_reader'),0)

    async def test_invalid_dto_never_borrows_other_store_stock_or_empty_shell(self):
        for bad in (dto(requested=URL.replace('used','other')),dto(url=URL.replace('.com','.com.evil.test')),
                    dto(version='gumtree-reader-v1'),dto(region='wrong'),dto(checked=time.time()-121),
                    dto(checked=True),dto(status=True),dto(status=429),dto(location=URL),
                    dto(body='{}'),dto(body='<html>challenge</html>'),dto(body='x'*2097153),dto(extra=1)):
            with self.subTest(bad=list(bad)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=bad))) as c:
                    with self.assertRaises(ReaderUnavailable):await fetch_collection(STORE,'used',c)

    async def test_unsupported_target_missing_config_and_unattempted_guard(self):
        with patch.object(Config,'WILLHABEN_READER_URL',''):
            self.assertIsNone(await fetch_collection(STORE,'used'))
        for base,handle in [(STORE,'other'),('https://evil.test','used'),(STORE+'/extra','used')]:
            with self.assertRaises(ReaderUnavailable):await fetch_collection(base,handle)
        self.assertEqual(used('shopify_reader'),0)
        attempted=AsyncMock()
        with patch('shopify_reader._reserve',return_value=False):
            with self.assertRaises(ReaderUnavailable):await fetch_collection(STORE,'used',on_attempt=attempted)
        attempted.assert_not_called()

    async def test_reservation_atomicity_and_core_capacity_are_independent(self):
        with store() as c:
            c.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',[
                ('shopify_reader',time.time(),31),('shopify_reader:aux',time.time(),31),
                ('willhaben_reader',time.time(),200),('gumtree_reader',time.time(),200)])
            c.commit()
        results=await asyncio.gather(*(asyncio.to_thread(_reserve,True) for _ in range(5)))
        self.assertEqual(sum(results),1)
        for _ in range(8):self.assertTrue(_reserve(False))
        self.assertFalse(_reserve(False));self.assertFalse(_reserve(True))
        self.assertEqual(used('shopify_reader'),40)
        self.assertEqual(used('willhaben_reader'),200);self.assertEqual(used('gumtree_reader'),200)

    async def test_rolling_auxiliary_guard_and_failed_requests_are_counted(self):
        async def fail(r):raise httpx.ReadTimeout('controlled',request=r)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as c:
            with self.assertRaises(ReaderUnavailable):await fetch_collection(STORE,'used',c)
        self.assertEqual(used('shopify_reader'),1)
        with store() as c:
            c.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',[
                ('shopify_reader',time.time()-86400,992),('shopify_reader:aux',time.time()-86400,992)])
            c.commit()
        self.assertFalse(_reserve(True));self.assertTrue(_reserve(False))

    async def test_native_search_matrix_and_descriptions_survive_collection_recovery(self):
        scraper=ShopifyDealersScraper();original=scraper.make_client;calls=[]
        def handle(r):
            calls.append(r)
            if r.url.host.endswith('railway.internal'):
                return httpx.Response(200,json=dto(body=json.dumps({'products':[None,PRODUCT]})))
            if '/collections/' in r.url.path:return httpx.Response(429)
            return httpx.Response(200,json={'resources':{'results':{'products':[]}}})
        store=STORES[0];terms=['zeta','Jazz Fusion','JV5','JLP','4-strings','sunburst']
        with patch('scrapers.shopify_dealers.STORES',[store]), \
             patch('scrapers.shopify_dealers.market_queries',return_value=terms) as factory, \
             patch('scrapers.shopify_dealers.REQUEST_GAP_SECONDS',0), \
             patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            items=await scraper.search()
        factory.assert_called_once_with(broad=True,limit=6)
        self.assertEqual([r.url.params['q'] for r in calls if r.url.path=='/search/suggest.json'],terms)
        self.assertEqual(len(items),1);self.assertIn('Case only has minor scuffs',items[0]['description'])
        self.assertEqual(items[0]['condition'],'Used')
        self.assertEqual(scraper.coverage_stats['private_us_attempts'],1)
        self.assertEqual(scraper.requests_recovered,1)
        self.assertNotIn('HTTP 429',scraper.health_error())
        self.assertIn('invalid product record',scraper.health_error())
        self.assertEqual(scraper.discovery_queries_attempted,8)

    async def test_unreadable_recovery_is_partial_not_healthy_zero(self):
        scraper=ShopifyDealersScraper();original=scraper.make_client
        def handle(r):
            if r.url.host.endswith('railway.internal'):return httpx.Response(200,json=dto(body='{}'))
            if '/collections/' in r.url.path:return httpx.Response(429)
            return httpx.Response(200,json={'resources':{'results':{'products':[]}}})
        with patch('scrapers.shopify_dealers.STORES',[STORES[0]]), \
             patch('scrapers.shopify_dealers.market_queries',return_value=['zeta']), \
             patch('scrapers.shopify_dealers.REQUEST_GAP_SECONDS',0), \
             patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            self.assertEqual(await scraper.search(),[])
        self.assertIn('HTTP 429',scraper.health_error());self.assertIn('unverified',scraper.health_error())
        self.assertEqual(scraper.requests_recovered,0)

    async def test_recovered_collection_cannot_make_sold_variants_available(self):
        scraper=ShopifyDealersScraper();original=scraper.make_client
        product={**PRODUCT,'variants':[{'price':'1900','available':False}]}
        def handle(r):
            if r.url.host.endswith('railway.internal'):return httpx.Response(200,json=dto(body=json.dumps({'products':[product]})))
            if '/collections/' in r.url.path:return httpx.Response(429)
            return httpx.Response(200,json={'resources':{'results':{'products':[]}}})
        with patch('scrapers.shopify_dealers.STORES',[STORES[0]]), \
             patch('scrapers.shopify_dealers.market_queries',return_value=['zeta']), \
             patch('scrapers.shopify_dealers.REQUEST_GAP_SECONDS',0), \
             patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
            self.assertEqual(await scraper.search(),[])
        self.assertEqual(scraper.health_error(),'')


class ServerContractTests(unittest.TestCase):
    def test_only_two_collection_destinations_and_required_protocol(self):
        run_js(f'''
import {{shopifyTarget}} from {MODULE!r};
assert.equal(shopifyTarget({{collection:'evs-used'}}),{URL!r});
for(const p of [null,[],{{collection:'other'}},{{collection:'evs-used',url:'https://evil.test'}},{{collection:'__proto__'}}])assert.equal(shopifyTarget(p),null);
let calls=[];const h=createHandler({{region:'us-test',log:()=>{{}},upstreamFetch:async(u,o)=>{{calls.push([u,o]);const r=new Response('{{"products":[]}}');Object.defineProperty(r,'url',{{value:u}});return r;}}}});
const req=(v='1')=>new Request('http://internal/read-shopify',{{method:'POST',headers:{{'X-Shopify-Reader-Version':v}},body:JSON.stringify({{collection:'evs-used'}})}});
const r=await h(req()),d=await r.json();assert.equal(r.status,200);assert.equal(d.version,'shopify-reader-v1');assert.equal(d.requested,{URL!r});assert.equal(d.location,null);
assert.equal(calls[0][1].redirect,'manual');assert.equal(calls[0][1].headers.Accept,'application/json, */*;q=0.8');
assert.equal(Object.keys(calls[0][1].headers).some(k=>/cookie|authorization/i.test(k)),false);
assert.equal((await h(req('2'))).status,400);assert.equal((await h(new Request('http://internal/read-shopify'))).status,405);
assert.equal((await h(new Request('http://internal/health-shopify'))).status,200);
''')

    def test_shopify_cap_cannot_consume_existing_willhaben_or_gumtree_capacity(self):
        run_js('''
const h=createHandler({log:()=>{},upstreamFetch:async u=>{const r=new Response('ok');Object.defineProperty(r,'url',{value:u});return r;}});
const s=()=>new Request('http://internal/read-shopify',{method:'POST',headers:{'X-Shopify-Reader-Version':'1'},body:JSON.stringify({collection:'evs-used'})});
for(let i=0;i<40;i++)assert.equal((await h(s())).status,200);assert.equal((await h(s())).status,429);
assert.equal((await h(new Request('http://internal/read',{method:'POST',body:JSON.stringify({kind:'search',keyword:'zeta',category:'all'})}))).status,200);
assert.equal((await h(new Request('http://internal/read-gumtree',{method:'POST',headers:{'X-Gumtree-Reader-Version':'1'},body:JSON.stringify({kind:'search',keyword:'zeta'})}))).status,200);
assert.equal((await h(new Request('http://internal/read-leboncoin',{method:'POST'}))).status,404);
''')

    def test_concurrency_and_body_deadline_remain_shared_across_all_routes(self):
        run_js('''
const h=createHandler({timeoutMs:30,log:()=>{},upstreamFetch:(u,o)=>new Promise((resolve,reject)=>o.signal.addEventListener('abort',()=>reject(new Error('aborted'))))});
const req=new Request('http://internal/read-shopify',{method:'POST',headers:{'X-Shopify-Reader-Version':'1'},body:JSON.stringify({collection:'evs-used'})});
const a=h(req),b=h(new Request('http://internal/read-gumtree',{method:'POST',headers:{'X-Gumtree-Reader-Version':'1'},body:JSON.stringify({kind:'search',keyword:'zeta'})}));
await new Promise(r=>setTimeout(r,5));
assert.equal((await h(new Request('http://internal/read',{method:'POST',body:JSON.stringify({kind:'search',keyword:'zeta',category:'all'})}))).status,429);
assert.equal((await a).status,504);assert.equal((await b).status,504);
''')


if __name__=='__main__':unittest.main()
