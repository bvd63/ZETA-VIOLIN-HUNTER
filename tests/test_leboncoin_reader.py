"""Regional discovery fallback retains every term, stock uncertainty and budgets."""
import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import httpx
import database
from config import Config
from provider_state import store,used
from leboncoin_reader import search,API
from leboncoin_native import NativeClient,search_payload
from scrapers.leboncoin_native import LeboncoinNativeScraper
from tests.test_leboncoin_native import ROW
from tests.test_willhaben_reader_contract import run_js,MODULE
from willhaben_reader import ReaderUnavailable

BASE='http://reader.railway.internal:8080'


def dto(**changes):
    return {'version':'leboncoin-reader-v1','request':{'kind':'search','keyword':'zeta','offset':0},
            'requested':API,'url':API,'status':200,'body':json.dumps({'ads':[ROW],'total':1}),
            'checked':time.time(),'region':'us-east4-eqdc4a','location':None,**changes}


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(database,'DB_PATH',str(Path(self.temp.name)/'reader.db')),
                      patch.object(Config,'WILLHABEN_READER_URL',BASE)]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    async def test_fixed_request_bound_response_full_body_and_independent_ledger(self):
        async def handler(r):
            self.assertEqual(str(r.url),BASE+'/read-leboncoin')
            self.assertEqual(json.loads(r.content),{'kind':'search','keyword':'zeta','offset':0})
            self.assertEqual(r.headers['X-Leboncoin-Reader-Version'],'1')
            return httpx.Response(200,json=dto())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            response=await search('zeta',client=c)
        self.assertEqual(response.json()['ads'][0]['body'],ROW['body'])
        self.assertEqual(used('leboncoin_reader'),1)
        self.assertEqual(used('willhaben_reader'),0);self.assertEqual(used('gumtree_reader'),0)
        for kw,offset in (('',0),('x'*401,0),('zeta',True),('zeta',-1),('zeta',2147483648)):
            with self.assertRaises(ReaderUnavailable):await search(kw,offset)
        self.assertEqual(used('leboncoin_reader'),1)

    async def test_partial_challenge_redirect_wrong_query_id_time_and_region_never_accepted(self):
        bads=[dto(request={'kind':'search','keyword':'other','offset':0}),dto(request={'kind':'search','keyword':'zeta','offset':False}),
              dto(url=API+'/other'),dto(requested='https://evil.test'),dto(location='https://evil.test'),
              dto(status=403,body='{"url":"https://challenge.invalid"}'),dto(status=429),dto(body='not json'),
              dto(body='x'*2097153),dto(checked=time.time()-121),dto(region='other'),dto(extra=1)]
        for bad in bads:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=bad))) as c:
                with self.assertRaises(ReaderUnavailable):await search('zeta',client=c)
        self.assertEqual(used('leboncoin_reader'),len(bads))

    async def test_auxiliary_cannot_consume_scheduled_search_reserve(self):
        with store() as c:
            c.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                          [('leboncoin_reader',time.time(),160),('leboncoin_reader:aux',time.time(),160)])
            c.commit()
        with self.assertRaisesRegex(ReaderUnavailable,'allowance'):await search('zeta')
        from work_context import work_kind
        token=work_kind.set('core')
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=dto()))) as c:
                self.assertEqual((await search('zeta',client=c)).status_code,200)
        finally:work_kind.reset(token)
        self.assertEqual(used('leboncoin_reader'),161)

    async def test_original_native_success_does_not_call_regional_and_403_does(self):
        observer=LeboncoinNativeScraper();observer.reset_health()
        client=NativeClient(observer)
        success=httpx.Response(200,json={'ads':[ROW],'total':1})
        client.session=type('Session',(),{'request':AsyncMock(return_value=success)})()
        with patch('leboncoin_reader.search',AsyncMock(side_effect=AssertionError('native should win'))):
            self.assertEqual((await client.search('zeta'))[1],1)
        self.assertEqual(observer.requests_attempted,1)
        client.session.request=AsyncMock(return_value=httpx.Response(403))
        async def reserve(kw,offset,observer=None):
            observer.requests_attempted+=1;observer.requests_ok+=1
            return success
        with patch('leboncoin_reader.search',AsyncMock(side_effect=reserve)) as fallback:
            self.assertEqual((await client.search('zeta'))[1],1)
            self.assertEqual((await client.search('Zeta JV5',35))[1],1)
        self.assertEqual(fallback.await_count,2)
        self.assertEqual(client.session.request.await_count,1)
        self.assertEqual(observer.health_error(),'')
        self.assertEqual(observer.requests_recovered,1)

    async def test_primary_never_uses_search_or_invents_sold_on_reader_failure(self):
        client=NativeClient();client.regional_only=True
        client.session=type('Session',(),{'request':AsyncMock(return_value=httpx.Response(403))})()
        with patch('leboncoin_reader.search',AsyncMock(side_effect=AssertionError('no primary via search'))):
            with self.assertRaisesRegex(RuntimeError,'403'):await client.primary(ROW['url'])
        from leboncoin_api import closed_reason
        self.assertFalse(closed_reason(ROW['url']))

    async def test_bootstrap403_preserves_eight_queries_and_unknown_cards(self):
        observer=LeboncoinNativeScraper();observer.reset_health()
        class Session:
            def __init__(self,*a,**kw):pass
            async def request(self,*a,**kw):return httpx.Response(403)
            async def close(self):pass
        calls=[]
        async def reserve(keyword,offset=0,observer=None):
            calls.append((keyword,offset));observer.requests_attempted+=1;observer.requests_ok+=1
            return httpx.Response(200,json={'ads':[copy.deepcopy(ROW)],'total':1})
        with patch.dict('sys.modules',{'curl_cffi':SimpleNamespace(AsyncSession=Session)}),patch('leboncoin_reader.search',AsyncMock(side_effect=reserve)), \
             patch('scrapers.leboncoin_native.asyncio.sleep',AsyncMock()):
            items=await observer.search()
        from keywords import market_queries
        self.assertEqual([k for k,o in calls],market_queries('fr',broad=True,limit=8))
        self.assertEqual(len(items),1);self.assertEqual(items[0]['_leboncoin_observed_state'],'unknown')
        self.assertFalse(items[0]['_leboncoin_primary_checked'])
        self.assertTrue(items[0]['_query_identity_unverified']);self.assertEqual(observer.health_error(),'')
        self.assertEqual(observer.requests_attempted,9);self.assertEqual(observer.requests_recovered,1)

    async def test_429_does_not_switch_region_and_regional_failure_preserves_missing_term_error(self):
        client=NativeClient();client.session=type('Session',(),{'request':AsyncMock(return_value=httpx.Response(429))})()
        with patch('leboncoin_reader.search',AsyncMock(side_effect=AssertionError('no rate limit bypass'))):
            with self.assertRaisesRegex(RuntimeError,'429'):await client.search('zeta')
        observer=LeboncoinNativeScraper();observer.reset_health()
        class Session:
            def __init__(self,*a,**kw):pass
            async def request(self,*a,**kw):return httpx.Response(403)
            async def close(self):pass
        with patch.dict('sys.modules',{'curl_cffi':SimpleNamespace(AsyncSession=Session)}),patch('leboncoin_reader.search',AsyncMock(side_effect=ReaderUnavailable('allowance unavailable'))) as fallback:
            self.assertEqual(await observer.search(),[])
        self.assertEqual(fallback.await_count,1);self.assertIn('coverage incomplete',observer.health_error())


class ServiceTests(unittest.TestCase):
    def test_only_fixed_search_no_item_or_arbitrary_payload(self):
        run_js(f'''import {{leboncoinTarget}} from {MODULE!r};
assert.equal(leboncoinTarget({{kind:'search',keyword:'zeta',offset:35}}),'https://api.leboncoin.fr/finder/search');
for(const p of [{{kind:'item',id:'3269447854'}},{{kind:'search',keyword:'zeta',offset:true}},
 {{kind:'search',keyword:'zeta',offset:-1}},{{kind:'search',keyword:'zeta',offset:0,url:'https://evil.test'}},
 {{kind:'search',keyword:'',offset:0}}])assert.equal(leboncoinTarget(p),null);
''')

    def test_exact_native_body_no_location_age_title_filter_stable_session_and_gum_unchanged(self):
        run_js('''let calls=[];
const h=createHandler({region:'us-test',log:()=>{},upstreamFetch:async(url,options)=>{
 calls.push([url,options]);let r=new Response('{}');Object.defineProperty(r,'url',{value:url});return r;}});
const req=kw=>new Request('http://internal/read-leboncoin',{method:'POST',headers:{'X-Leboncoin-Reader-Version':'1'},body:JSON.stringify({kind:'search',keyword:kw,offset:35})});
const response=await h(req('Zeta JV5'));const data=await response.json();
assert.equal(data.version,'leboncoin-reader-v1');assert.deepEqual(data.request,{kind:'search',keyword:'Zeta JV5',offset:35});
await h(req('Jazz Fusion'));
assert.equal(calls[0][1].headers['User-Agent'],calls[1][1].headers['User-Agent']);
assert.equal(calls[0][1].method,'POST');assert.equal(calls[0][1].redirect,'manual');
const p=JSON.parse(calls[0][1].body);assert.deepEqual(p.filters,{category:{id:'30'},enums:{ad_type:['offer']},keywords:{text:'Zeta JV5'},location:{}});
assert.equal(p.limit,35);assert.equal(p.offset,35);assert.equal(p.sort_by,'time');assert.equal(p.sort_order,'desc');
assert.equal(Object.keys(calls[0][1].headers).some(x=>/cookie|authorization/i.test(x)),false);
for(let i=2;i<200;i++)assert.equal((await h(req('zeta'))).status,200);
assert.equal((await h(req('zeta'))).status,429);
const gum=new Request('http://internal/read-gumtree',{method:'POST',headers:{'X-Gumtree-Reader-Version':'1'},body:JSON.stringify({kind:'search',keyword:'zeta'})});
assert.equal((await h(gum)).status,200);
''')


if __name__=='__main__':unittest.main()
