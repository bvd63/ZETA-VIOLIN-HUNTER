"""Private reader contract and quotas, simulated HTTP and temporary SQLite only."""
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
from provider_state import used
from willhaben_reader import fetch, ReaderUnavailable, _reserve, _target, MAX_BODY, MAX_WIRE

URL='https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=Zeta%20JV5'
ITEM='https://www.willhaben.at/iad/kaufen-und-verkaufen/d/zeta-violin-12345'
BASE='http://will-reader.railway.internal:8080'


def dto(url=URL, **changes):
    return {'version':'willhaben-reader-v1','requested':url,'url':url,'status':200,
            'body':'<html>item</html>','checked':time.time(),'region':'us-east4-eqdc4a',**changes}


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'reader.db'));self.db.start()
        self.cfg=patch.object(Config,'WILLHABEN_READER_URL',BASE,create=True);self.cfg.start()

    async def asyncTearDown(self):
        self.cfg.stop();self.db.stop();self.tmp.cleanup()

    async def test_missing_config_and_invalid_urls_do_not_reserve(self):
        with patch.object(Config,'WILLHABEN_READER_URL',''):
            self.assertIsNone(await fetch(URL))
        for value in ('http://evil.example:80','http://user@a.railway.internal:80',
                      'http://a.railway.internal:80/path','https://a.railway.internal:80',
                      'http://a.railway.internal', 'http://a.railway.internal:80?x=1'):
            with self.subTest(value=value),patch.object(Config,'WILLHABEN_READER_URL',value):
                with self.assertRaises(ReaderUnavailable): await fetch(URL)
        for value in ('https://willhaben.at.evil.test/iad/kaufen-und-verkaufen/marktplatz?keyword=zeta',
                      URL+'&page=2', URL+'#x', 'http://willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=zeta',
                      ITEM+'?x=1','https://user@willhaben.at'+ITEM.split('.at')[1],
                      URL.replace('JV5','x'*401), ITEM.replace('zeta-violin','zeta%2fviolin'),
                      ITEM.replace('zeta-violin','zeta%5cviolin'), ITEM.replace('zeta-violin','zeta%252fviolin'),
                      ITEM.replace('zeta-violin','zeta%xxviolin')):
            with self.subTest(value=value):
                with self.assertRaises(ReaderUnavailable): await fetch(value)
        self.assertEqual(used('willhaben_reader'),0)

    async def test_https_standard_port_and_unicode_encoded_slugs(self):
        self.assertIsNotNone(_target(URL.replace('willhaben.at/','willhaben.at:443/')))
        for path in ('/iad/kaufen-und-verkaufen/d/geige-übung-12345/',
                     '/iad/kaufen-und-verkaufen/d/geige-%C3%BCbung-12345/'):
            target=_target('https://www.willhaben.at'+path)
            self.assertEqual(target,{'kind':'item','path':path})

    async def test_exact_search_and_item_payloads_and_no_redirect(self):
        calls=[]
        async def handler(req):
            calls.append((str(req.url),json.loads(req.content)))
            return httpx.Response(200,json=dto(URL if len(calls)==1 else ITEM))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            self.assertEqual((await fetch(URL,c,auxiliary=False))[0],200)
            self.assertEqual((await fetch(ITEM,c,auxiliary=False))[2],ITEM)
        self.assertEqual(calls,[(BASE+'/read',{'kind':'search','keyword':'Zeta JV5','category':'all'}),
                                (BASE+'/read',{'kind':'item','path':'/iad/kaufen-und-verkaufen/d/zeta-violin-12345'})])
        self.assertEqual(used('willhaben_reader'),2)

    async def test_invalid_dto_identity_age_size_and_status_are_withheld(self):
        cases=[dto(url=URL.replace('JV5','JV4')), dto(requested=URL+'&page=2'),
               dto(url='https://evil.test/iad/kaufen-und-verkaufen/marktplatz?keyword=Zeta%20JV5'),
               dto(checked=time.time()-121),dto(checked=time.time()+121),dto(checked=True),
               dto(status=True),dto(status=600),dto(body='x'*(MAX_BODY+1)),dto(region='other'),dto(extra=1)]
        for data in cases:
            with self.subTest(keys=list(data)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as c:
                    with self.assertRaises(ReaderUnavailable): await fetch(URL,c)
        item={**dto(ITEM),'url':ITEM.replace('12345','12346')}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=item))) as c:
            with self.assertRaises(ReaderUnavailable): await fetch(ITEM,c)
        self.assertEqual(used('willhaben_reader'),len(cases)+1)

    async def test_redirect_and_uncertain_timeout_remain_charged(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'Location':'https://evil.test'}))) as c:
            with self.assertRaises(ReaderUnavailable): await fetch(URL,c)
        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200,json=dto())
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
            with self.assertRaises(ReaderUnavailable): await fetch(URL,c,timeout=.02)
        self.assertEqual(used('willhaben_reader'),2)

    async def test_oversized_wire_and_cancellation_keep_reservation(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*(MAX_WIRE+1)))) as c:
            with self.assertRaises(ReaderUnavailable): await fetch(URL,c)
        started=asyncio.Event()
        async def stalled(request):
            started.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as c:
            task=asyncio.create_task(fetch(URL,c))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(used('willhaben_reader'),2)

    async def test_protected_core_daily_and_durable_concurrency(self):
        self.assertEqual(sum(await asyncio.gather(*(asyncio.to_thread(_reserve,True) for _ in range(180)))),160)
        self.assertEqual(sum(await asyncio.gather(*(asyncio.to_thread(_reserve,False) for _ in range(50)))),40)
        self.assertFalse(_reserve(False))
        self.assertEqual(used('willhaben_reader'),200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:self.fail('quota exceeded'))) as c:
            with self.assertRaises(ReaderUnavailable): await fetch(URL,c,auxiliary=False)

    async def test_primary_item_always_aux_even_when_caller_requests_core(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=dto(ITEM)))) as c:
            await fetch(ITEM,c,auxiliary=False)
        self.assertEqual(used('willhaben_reader:aux'),1)
        self.assertEqual(used('willhaben_reader:core'),0)
        self.assertEqual(sum(_reserve(True) for _ in range(160)),159)
        called=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:called.append(r) or httpx.Response(200,json=dto()))) as c:
            with self.assertRaises(ReaderUnavailable): await fetch(ITEM,c,auxiliary=False)
            self.assertEqual((await fetch(URL,c,auxiliary=False))[0],200)
        self.assertEqual(len(called),1)
        self.assertEqual(used('willhaben_reader:core'),1)

    async def test_at_most_two_concurrent_calls(self):
        active=peak=0
        async def handler(request):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01)
            active-=1
            return httpx.Response(200,json=dto())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            results=await asyncio.gather(*(fetch(URL,c) for _ in range(5)))
        self.assertEqual(len(results),5);self.assertEqual(peak,2)

    @staticmethod
    def native_page(query):
        ad={'id':'12345','attributes':{'attribute':[
            {'name':'HEADING','values':['Zeta Jazz JV5 electric violin']},
            {'name':'BODY_DYN','values':['Used Zeta violin']},
            {'name':'SEO_URL','values':['/iad/kaufen-und-verkaufen/d/zeta-violin-12345']}]}}
        data={'props':{'pageProps':{'searchResult':{'searchTitle':query,'rowsFound':1,
              'advertSummaryList':{'advertSummary':[ad]}}}}}
        return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'

    @staticmethod
    def tracked_client(scraper, handler):
        async def requested(request): scraper.requests_attempted+=1
        async def responded(response):
            if await scraper._response_is_healthy(response): scraper.requests_ok+=1
            else: scraper.failures.append(f'HTTP {response.status_code} ({response.request.url.host})')
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),event_hooks={'request':[requested],'response':[responded]})

    async def test_native_403_private_success_preserves_every_query_and_health(self):
        from scrapers.willhaben import WillhabenScraper, SEARCH_URL, CATEGORY_URL, search_plan
        scraper=WillhabenScraper();scraper.reset_health();requests=[]
        plan=search_plan()
        async def handler(request):
            requests.append(request)
            if request.url.host!='will-reader.railway.internal': return httpx.Response(403)
            payload=json.loads(request.content)
            url=str(httpx.URL(CATEGORY_URL if payload['category']=='music' else SEARCH_URL,
                               params={'keyword':payload['keyword']}))
            return httpx.Response(200,json=dto(url,body=self.native_page(payload['keyword'])))
        with patch.object(scraper,'make_client',return_value=self.tracked_client(scraper,handler)), \
             patch('scrapers.willhaben.fresh_willhaben_search',AsyncMock()) as exa:
            results=await scraper.search()
        self.assertEqual(scraper.discovery_queries_attempted,len(plan))
        self.assertEqual(len(requests),2*len(plan));self.assertEqual(scraper.fetched,len(plan))
        payloads=[json.loads(r.content) for r in requests if r.url.host=='will-reader.railway.internal']
        self.assertEqual([(p['keyword'],p['category']) for p in payloads],
                         [(kw,'music' if url==CATEGORY_URL else 'all') for kw,url in plan])
        self.assertEqual(len(results),1);self.assertEqual(scraper.health_error(),'')
        self.assertEqual(scraper.requests_recovered,len(plan));exa.assert_not_awaited()

    async def test_private_failures_retry_existing_exa_and_retain_partial_health(self):
        from scrapers.willhaben import WillhabenScraper, SEARCH_URL
        cases=('redirect','upstream302','403','invalid','wrong_echo','invalid_html')
        for case in cases:
            scraper=WillhabenScraper();scraper.reset_health()
            async def handler(request):
                if request.url.host!='will-reader.railway.internal': return httpx.Response(403)
                if case=='redirect': return httpx.Response(302,headers={'Location':'https://evil.test'})
                if case=='upstream302': return httpx.Response(200,json=dto(URL,status=302))
                if case=='403': return httpx.Response(200,json=dto(URL,status=403))
                if case=='invalid': return httpx.Response(200,json=dto(URL,region='invalid'))
                if case=='invalid_html': return httpx.Response(200,json=dto(URL,body='<html>shell</html>'))
                return httpx.Response(200,json=dto(URL,body=self.native_page('other')))
            with self.subTest(case=case), \
                 patch.object(scraper,'make_client',return_value=self.tracked_client(scraper,handler)), \
                 patch('scrapers.willhaben.search_plan',return_value=[('Zeta JV5',SEARCH_URL)]), \
                 patch('scrapers.willhaben.fresh_willhaben_search',AsyncMock(return_value={'text':'partial'})) as exa, \
                 patch.object(scraper,'_extract_exa',return_value=([],False)), \
                 patch('scrapers.willhaben.remember_market_search'):
                self.assertEqual(await scraper.search(),[])
                exa.assert_awaited_once()
                self.assertTrue(scraper.health_error())

    async def test_unconfigured_preserves_existing_exa_path(self):
        from scrapers.willhaben import WillhabenScraper, SEARCH_URL
        scraper=WillhabenScraper();scraper.reset_health()
        with patch.object(Config,'WILLHABEN_READER_URL',''), \
             patch.object(scraper,'make_client',return_value=self.tracked_client(scraper,lambda req:httpx.Response(403))), \
             patch('scrapers.willhaben.search_plan',return_value=[('zeta',SEARCH_URL)]), \
             patch('scrapers.willhaben.fresh_willhaben_search',AsyncMock(return_value={'text':'partial'})) as exa, \
             patch.object(scraper,'_extract_exa',return_value=([],False)), \
             patch('scrapers.willhaben.remember_market_search'):
            self.assertEqual(await scraper.search(),[])
            exa.assert_awaited_once();self.assertTrue(scraper.health_error())

    async def test_complete_exa_recovers_all_failed_routes_without_phantom_timeout(self):
        from scrapers.willhaben import WillhabenScraper, SEARCH_URL
        scraper=WillhabenScraper();scraper.reset_health()
        # Both direct and private transport actually answer403. A complete,
        # parser-validated independent result must recover both failed GET paths.
        with patch.object(scraper,'make_client',return_value=self.tracked_client(scraper,lambda req:httpx.Response(403))), \
             patch('scrapers.willhaben.search_plan',return_value=[('zeta',SEARCH_URL)]), \
             patch('scrapers.willhaben.fresh_willhaben_search',AsyncMock(return_value={'text':'0 Anzeigen'})):
            self.assertEqual(await scraper.search(),[])
        self.assertEqual(scraper.requests_attempted,2)
        self.assertEqual(scraper.requests_recovered,2)
        self.assertEqual(scraper.health_error(),'')

    async def test_private_primary_status_and_identity_keep_unknown_distinct(self):
        from offer_verifier import verify_offer
        from filters import classify
        violin='<main><h1>Zeta Jazz Fusion JV5 violin</h1><p id="description">Used Zeta electric violin</p><p>For sale price 1500 EUR</p></main>'
        nonviolin='<main><h1>Yamaha guitar</h1><p id="description">Used Yamaha guitar</p><p>For sale price 1500 EUR</p></main>'
        sold='<main><h1>Zeta Jazz Fusion JV5 violin</h1><p>This item has been sold</p></main>'
        for status,body,expected in ((200,violin,'live'),(200,nonviolin,'live'),(200,sold,'dead'),
                                     (404,'','dead'),(410,'','dead'),(403,'','unknown'),(302,'','unknown')):
            listing={'id':'x','url':ITEM,'title':'Zeta Jazz Fusion JV5 violin','description':'Used Zeta violin','source':'search'}
            with self.subTest(status=status,body=body[:25]), \
                 patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                 patch('willhaben_reader.fetch',AsyncMock(return_value=(status,body,ITEM))), \
                 patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value=None)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(403))) as client:
                    state,_=await verify_offer(listing,client)
                self.assertEqual(state,expected)
                if body==nonviolin:
                    self.assertTrue(classify(listing))

    def test_inspect_other_item_never_uses_status_or_mutates_identity(self):
        from offer_verifier import inspect_page
        other=ITEM.replace('12345','99999')
        for original in (ITEM,ITEM+'?utm_source=google'):
            for status,body in ((200,'<main><h1>Other violin</h1><p>This item has been sold</p></main>'),
                                (404,''),(410,'')):
                with self.subTest(original=original,status=status):
                    item={'url':original,'title':'Zeta violin','description':'original','source':'search'}
                    self.assertEqual(inspect_page(item,body,status,other)[0],'unknown')
                    self.assertEqual((item['title'],item['description']),('Zeta violin','original'))

    async def test_redirect_to_other_item_or_foreign_host_never_calls_reader(self):
        from offer_verifier import verify_offer
        for original in (ITEM,ITEM+'?utm_source=google'):
            for destination in (ITEM.replace('12345','99999'), 'https://evil.example/item/12345'):
                with self.subTest(original=original,destination=destination):
                    calls=[]
                    def handler(req):
                        calls.append(str(req.url))
                        return httpx.Response(302,headers={'location':destination})
                    item={'url':original,'title':'Zeta violin','description':'original','source':'search'}
                    with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                         patch('facebook_discovery.cached_offer',return_value=None), \
                         patch('willhaben_reader.fetch',AsyncMock()) as reader, \
                         patch('primary_page_reserve.fresh_primary_page',AsyncMock()) as exa:
                        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                            state,_=await verify_offer(item,client)
                    self.assertEqual(state,'unknown')
                    self.assertEqual(len(calls),1)
                    self.assertEqual(item['title'],'Zeta violin')
                    reader.assert_not_awaited();exa.assert_not_awaited()

    async def test_same_item_redirect_slug_alias_allows_primary_live_or_dead(self):
        from offer_verifier import verify_offer
        alias=ITEM.replace('zeta-violin','zeta-other-name')
        live='<main><h1>Zeta violin</h1><p>For sale price 1500 EUR</p></main>'
        sold='<main><h1>Zeta violin</h1><p>This item has been sold</p></main>'
        for body,expected in ((live,'live'),(sold,'dead')):
            with self.subTest(expected=expected):
                def handler(req):
                    return httpx.Response(302,headers={'location':alias}) if str(req.url)==ITEM else httpx.Response(403)
                item={'url':ITEM,'title':'Zeta violin','source':'search'}
                with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                     patch('facebook_discovery.cached_offer',return_value=None), \
                     patch('willhaben_reader.fetch',AsyncMock(return_value=(200,body,alias))) as reader, \
                     patch('primary_page_reserve.fresh_primary_page',AsyncMock()) as exa:
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        state,_=await verify_offer(item,client)
                self.assertEqual(state,expected)
                reader.assert_awaited_once();exa.assert_not_awaited()


if __name__=='__main__': unittest.main()
