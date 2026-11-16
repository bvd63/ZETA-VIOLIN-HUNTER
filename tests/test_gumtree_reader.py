"""Regression controls for fixed-host regional recovery; no external requests."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import database
from config import Config
from provider_state import used, store
from gumtree_reader import fetch, _target, item_id, _reserve, ReaderUnavailable
from scrapers.gumtree import GumtreeScraper
from offer_verifier import inspect_page, verify_offer
from tests.test_willhaben_reader_contract import run_js, MODULE

BASE='http://will-reader.railway.internal:8080'
SEARCH='https://www.gumtree.com/search?search_category=all&q=zeta'
ITEM='https://www.gumtree.com/p/violins/zeta-jv5/9000000002'
FIXTURE=(Path(__file__).parent/'fixtures/gumtree_cards_2026.html').read_text()


def dto(request_url=SEARCH, **changes):
    return {'version':'gumtree-reader-v1','requested':request_url,'url':request_url,'status':200,
            'body':'<h1>0 ads for zeta</h1>','checked':time.time(),'region':'us-east4-eqdc4a','location':None,**changes}


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'reader.db')),
                      patch.object(Config,'WILLHABEN_READER_URL',BASE)]
        for p in self.patches: p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    async def test_urls_and_exact_payloads(self):
        self.assertEqual(_target(SEARCH),{'kind':'search','keyword':'zeta'})
        self.assertEqual(item_id(ITEM+'?utm_source=index'), '9000000002')
        for bad in (SEARCH+'&q=other', SEARCH+'&extra=1', SEARCH.replace('zeta',''), SEARCH+'#fragment',
                    SEARCH.replace('all','violins'), ITEM+'?q=x', ITEM.replace('zeta','zeta%2fescape'),
                    ITEM.replace('zeta','zeta%252fescape'), ITEM.replace('zeta','zeta%5cfoo'),
                    ITEM.replace('zeta','zeta%00foo'), ITEM.replace('zeta','..'),
                    ITEM.replace('zeta','zeta%xx'), ITEM.replace('www.gumtree.com','user@www.gumtree.com'),
                    ITEM.replace('www.gumtree.com','www.gumtree.com.evil.test'), ITEM.replace('https:','http:')):
            self.assertIsNone(_target(bad),bad)
        calls=[]
        async def handle(r):
            calls.append((str(r.url),json.loads(r.content)))
            self.assertEqual(r.headers['X-Gumtree-Reader-Version'],'1')
            return httpx.Response(200,json=dto(SEARCH if len(calls)==1 else ITEM))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            await fetch(SEARCH,c,auxiliary=False);await fetch(ITEM,c,auxiliary=False)
        self.assertEqual(calls,[(BASE+'/read-gumtree',{'kind':'search','keyword':'zeta'}),
                               (BASE+'/read-gumtree',{'kind':'item','path':'/p/violins/zeta-jv5/9000000002'})])
        self.assertEqual(used('gumtree_reader:core'),1)
        self.assertEqual(used('gumtree_reader:aux'),1)  # Primary never spends core search reserve.
        self.assertEqual(used('willhaben_reader'),0)

    async def test_bound_search_item_and_current_dto(self):
        for bad in (dto(url=SEARCH.replace('zeta','violin')), dto(requested=SEARCH+'&extra=1'),
                    dto(version='willhaben-reader-v2'),dto(region='other'),dto(status=True),dto(status=600),
                    dto(body='x'*2097153),dto(checked=time.time()-121),dto(checked=True),dto(extra=1),
                    dto(ITEM,url=ITEM.replace('9000000002','9000000003'))):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=bad))) as c:
                with self.assertRaises(ReaderUnavailable):await fetch(ITEM if bad['requested']==ITEM else SEARCH,c)
        self.assertEqual(used('gumtree_reader'),11)

    async def test_redirect_same_id_only_query_exact_and_failures_charged(self):
        redirected=ITEM.replace('zeta-jv5','zeta-new-title')
        for location,valid in ((redirected,True),(ITEM.replace('9000000002','9000000003'),False),
                               ('https://evil.test'+ITEM.split('.com')[1],False),(SEARCH,False)):
            count=0
            async def handle(r):
                nonlocal count
                count+=1
                return httpx.Response(200,json=dto(ITEM if count==1 else redirected,
                    status=302 if count==1 else 200,location=location if count==1 else None))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
                if valid:self.assertEqual((await fetch(ITEM,c))[0],200)
                else:
                    with self.assertRaises(ReaderUnavailable):await fetch(ITEM,c)
            self.assertEqual(count,2 if valid else 1)
        self.assertEqual(used('gumtree_reader'),5)
        async def slow(r):await asyncio.sleep(1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
            with self.assertRaises(ReaderUnavailable):await fetch(SEARCH,c,timeout=.02)
        self.assertEqual(used('gumtree_reader'),6)

    async def test_atomic_protected_capacity_and_independent_willhaben_allowance(self):
        with store() as c:
            c.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                          [('gumtree_reader',time.time(),159),('gumtree_reader:aux',time.time(),159),
                           ('willhaben_reader',time.time(),200)])
            c.commit()
        results=await asyncio.gather(*(asyncio.to_thread(_reserve,True) for _ in range(5)))
        self.assertEqual(sum(results),1)
        for _ in range(40):self.assertTrue(_reserve(False))
        self.assertFalse(_reserve(False));self.assertFalse(_reserve(True))
        self.assertEqual(used('gumtree_reader'),200);self.assertEqual(used('willhaben_reader'),200)

    async def test_rolling_auxiliary_guard_and_missing_config(self):
        with patch.object(Config,'WILLHABEN_READER_URL',''):
            self.assertIsNone(await fetch(SEARCH))
        with store() as c:
            c.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                          [('gumtree_reader',time.time()-86400,4960),('gumtree_reader:aux',time.time()-86400,4960)])
            c.commit()
        self.assertFalse(_reserve(True));self.assertTrue(_reserve(False))

    def test_query_echo_challenges_and_primary_id(self):
        for heading in ('2 ads for zeta','2 ads for "zeta"','2 ads for ZETA in UK','2 ads for zeta in All Classifieds'):
            self.assertEqual(len(GumtreeScraper._extract('<h1>'+heading+'</h1>'+FIXTURE,expected_query='zeta')),2)
        self.assertEqual(GumtreeScraper._extract('<h1>0 ads for zeta</h1>',expected_query='zeta'),[])
        for body in ('<title>Access denied</title>', '<h1>2 ads for other</h1>'+FIXTURE,
                     '<title>Access denied</title><h1>0 ads for zeta</h1>',
                     '<h1>2 ads for zeta</h1><div>No recognized tiles</div>'):
            with self.assertRaises(ValueError):GumtreeScraper._extract(body,expected_query='zeta')
        listing={'url':ITEM,'title':'Zeta JV5'}
        for final in (ITEM.replace('9000000002','9000000003'), 'https://evil.test/p/violins/zeta/9000000002'):
            self.assertEqual(inspect_page(listing,'<h1>Zeta violin</h1><button>Buy now</button>',200,final)[0],'unknown')

    async def test_primary_live_closed_and_unreadable(self):
        primary='<main><h1>Zeta Jazz Fusion JV5</h1><p>Used electric violin, five strings, original case.</p><button>Buy now</button></main>'
        for code,body,wanted in ((200,primary,'live'),(200,primary+'<p>This ad has been removed</p>','dead'),(403,'blocked','unknown')):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(247))) as c:
                listing={'url':ITEM,'title':'generic index title','description':'old summary'}
                with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                     patch('public_node_fetch.fetch',AsyncMock(return_value=(247,'',ITEM))), \
                     patch('gumtree_reader.fetch',AsyncMock(return_value=(code,body,ITEM,None))), \
                     patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value='')):
                    state,reason=await verify_offer(listing,c)
            self.assertEqual(state,wanted,(state,reason))
            if wanted=='live':self.assertEqual(listing['title'],'Zeta Jazz Fusion JV5')

    async def test_native_recovery_preserves_queries_health_and_fallback(self):
        scraper=GumtreeScraper();scraper.reset_health()
        async def requested(r):scraper.requests_attempted+=1
        async def responded(r):scraper.failures.append('HTTP247')
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(247)),
                                event_hooks={'request':[requested],'response':[responded]})
        class Browser:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def fetch(self,*args):raise AssertionError('regional route recovers')
        terms=['Zeta violin','Zeta JV5']
        async def regional(url,c):
            from urllib.parse import parse_qs,urlsplit
            scraper.requests_attempted+=1; scraper.requests_ok+=1
            kw=parse_qs(urlsplit(url).query)['q'][0]
            return 200,'<h1>2 ads for '+kw+'</h1>'+FIXTURE,url,None
        with patch.object(scraper,'make_client',return_value=client),patch('scrapers.gumtree.PublicSearchBrowser',return_value=Browser()), \
             patch('scrapers.gumtree.private_read',AsyncMock(side_effect=regional)) as read, \
             patch('scrapers.gumtree.market_queries',return_value=terms), \
             patch('scrapers.gumtree.node_fetch',AsyncMock(side_effect=AssertionError('already recovered'))), \
             patch('asyncio.sleep',new_callable=AsyncMock):
            items=await scraper.search()
        self.assertEqual(read.await_count,2);self.assertEqual(len(items),2)
        self.assertEqual(scraper.health_error(),'');self.assertEqual(scraper.requests_recovered,4)
        self.assertEqual([x['keyword'] for x in scraper.coverage_stats['private_us_queries']],terms)
        self.assertEqual(scraper.discovery_queries_attempted,2)

    async def test_reader_failure_keeps_original_node_recovery(self):
        scraper=GumtreeScraper();scraper.reset_health()
        async def requested(r):scraper.requests_attempted+=1
        async def responded(r):scraper.failures.append('HTTP247')
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(247)),
                                event_hooks={'request':[requested],'response':[responded]})
        with patch.object(scraper,'make_client',return_value=client), \
             patch('scrapers.gumtree.private_read',AsyncMock(side_effect=ReaderUnavailable('allowance unavailable'))), \
             patch('scrapers.gumtree.market_queries',return_value=['zeta']), \
             patch('scrapers.gumtree.node_fetch',AsyncMock(return_value=(200,FIXTURE,SEARCH))) as node, \
             patch('asyncio.sleep',new_callable=AsyncMock):
            self.assertEqual(len(await scraper.search()),2)
        self.assertEqual(node.await_count,1);self.assertEqual(scraper.health_error(),'')
        self.assertEqual(used('willhaben_reader'),0)


class ServiceContractTests(unittest.TestCase):
    def test_fixed_destinations_and_no_arbitrary_inputs(self):
        run_js(f'''import {{gumtreeTarget}} from {MODULE!r};
const u=new URL(gumtreeTarget({{kind:'search',keyword:'Zeta & JV5'}}));
assert.equal(u.origin,'https://www.gumtree.com');assert.equal(u.searchParams.get('q'),'Zeta & JV5');
assert.equal(u.searchParams.get('search_category'),'all');
assert.equal(gumtreeTarget({{kind:'item',path:'/p/violins/zeta/123'}}),'https://www.gumtree.com/p/violins/zeta/123');
for(const path of ['/admin','/p/violins/zeta/123?url=evil','/p/violins/zeta%2fescape/123','/p/violins/zeta%252fescape/123','/p/violins/../123'])
 assert.equal(gumtreeTarget({{kind:'item',path}}),null);
assert.equal(gumtreeTarget({{kind:'search',keyword:'zeta',headers:{{}}}}),null);
''')

    def test_independent_allowances_and_dto(self):
        run_js('''let calls=[];
const h=createHandler({region:'us-test',log:()=>{},upstreamFetch:async(url,opts)=>{
 calls.push([url,opts]);let r=new Response('ok');Object.defineProperty(r,'url',{value:url});return r;}});
const gum=()=>new Request('http://internal/read-gumtree',{method:'POST',headers:{'X-Gumtree-Reader-Version':'1'},body:JSON.stringify({kind:'search',keyword:'zeta'})});
for(let i=0;i<200;i++)assert.equal((await h(gum())).status,200);
assert.equal((await h(gum())).status,429);
const will=new Request('http://internal/read',{method:'POST',headers:{'X-Willhaben-Reader-Version':'2'},body:JSON.stringify({kind:'search',keyword:'zeta',category:'all'})});
assert.equal((await h(will)).status,200);assert.equal(calls.length,201);
assert.equal(calls[0][1].redirect,'manual');assert.equal(calls[0][1].method,'GET');
assert.equal(Object.keys(calls[0][1].headers).some(x=>/authorization|cookie/i.test(x)),false);
const h2=createHandler({region:'us-test',log:()=>{},upstreamFetch:async url=>{let r=new Response('ok');Object.defineProperty(r,'url',{value:url});return r;}});
const data=await (await h2(gum())).json();assert.equal(data.version,'gumtree-reader-v1');assert.equal(data.location,null);
assert.equal((await h2(new Request('http://internal/read-gumtree',{method:'POST',body:JSON.stringify({kind:'search',keyword:'zeta'})}))).status,400);
''')


if __name__=='__main__':unittest.main()
