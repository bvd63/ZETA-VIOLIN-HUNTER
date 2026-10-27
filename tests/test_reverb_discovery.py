"""Offline Reverb discovery: API contracts, honest state, and protected coverage."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import database
from config import Config
from keywords import market_queries
from scrapers.reverb import ReverbScraper


def listing(n='123', state='live', **changes):
    item={'id':n,'title':'Zeta Jazz Fusion JV5 electric violin',
          '_links':{'web':{'href':f'https://reverb.com/item/{n}-zeta-violin'}},
          'make':'Zeta','model':'JV5','description':'Used Zeta violin with original case',
          'price':{'amount':'1500','currency':'USD'},'condition':{'display_name':'Excellent'},
          'state':{'slug':state} if state is not None else None}
    item.update(changes)
    return item


class ReverbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'test.db'));self.db.start()
        self.auth=patch.object(Config,'REVERB_API_TOKEN','fixture-token');self.auth.start()

    async def asyncTearDown(self):
        self.auth.stop();self.db.stop();self.tmp.cleanup()

    @staticmethod
    def client(scraper,handler):
        async def requested(req): scraper.requests_attempted+=1
        async def responded(resp):
            if await scraper._response_is_healthy(resp): scraper.requests_ok+=1
            else: scraper.failures.append(f'HTTP {resp.status_code}')
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=False,
            event_hooks={'request':[requested],'response':[responded]})

    async def test_full_original_matrix_pages_headers_and_shipping_unknown(self):
        scraper=ReverbScraper();scraper.reset_health();requests=[]
        async def handler(req):
            requests.append(req)
            return httpx.Response(200,json={'listings':[listing()]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)) as factory:
            result=await scraper.search()
        factory.assert_called_once_with(timeout=15,follow_redirects=False)
        expected=[{'query':kw} for kw in market_queries(broad=True,limit=10)]+[{'make':'Zeta'}]
        self.assertEqual(len(requests),2*len(expected))
        for i,params in enumerate(expected):
            for page in (1,2):
                req=requests[i*2+page-1]
                self.assertEqual(dict(req.url.params),{**params,'per_page':'50','page':str(page)})
                self.assertEqual(req.headers['authorization'],'Bearer fixture-token')
                self.assertEqual(req.headers['content-type'],'application/hal+json')
                self.assertEqual(req.headers['accept-version'],'3.0')
                self.assertNotIn('x-auth-token',req.headers)
        self.assertEqual(scraper.discovery_queries_attempted,len(requests))
        self.assertEqual(len(result),1);self.assertIsNone(result[0]['ships_to_ro'])
        self.assertEqual(result[0]['description'],'Used Zeta violin with original case')
        self.assertEqual(result[0]['model'],'JV5');self.assertEqual(scraper.health_error(),'')

    async def test_missing_state_is_retained_unknown_and_sold_withheld(self):
        scraper=ReverbScraper();scraper.reset_health()
        async def handler(req):
            return httpx.Response(200,json={'listings':[listing('1',None),listing('2','sold'),listing('3','live')]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']):
            result=await scraper.search()
        self.assertEqual({row['url'].split('/item/')[1].split('-')[0] for row in result},{'1','3'})
        unknown=next(row for row in result if '/item/1-' in row['url'])
        self.assertEqual(unknown['source'],'search');self.assertIs(unknown['_direct_item_candidate'],True)
        self.assertNotIn('source',next(row for row in result if '/item/3-' in row['url']))

    async def test_invalid_rows_do_not_hide_next_valid_listing(self):
        scraper=ReverbScraper();scraper.reset_health()
        broken=listing('9');broken['_links']='invalid'
        async def handler(req):
            return httpx.Response(200,json={'listings':[None,broken,listing('22')]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']):
            result=await scraper.search()
        self.assertEqual(len(result),1);self.assertIn('/item/22-',result[0]['url'])
        self.assertIn('invalid listing row',scraper.health_error())

    async def test_missing_or_invalid_list_is_partial_and_continues_other_query(self):
        for payload in ({}, {'listings':None}, {'listings':{}}, []):
            scraper=ReverbScraper();scraper.reset_health();calls=[]
            async def handler(req):
                calls.append(req)
                return httpx.Response(200,json=payload if len(calls)==1 else {'listings':[]})
            with self.subTest(payload=payload), \
                 patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
                 patch('scrapers.reverb.market_queries',return_value=['zeta']):
                self.assertEqual(await scraper.search(),[])
            self.assertEqual(len(calls),2)
            self.assertIn('unresolved',scraper.health_error())

    async def test_redirect_is_not_followed_or_healthy_empty(self):
        scraper=ReverbScraper();scraper.reset_health();requests=[]
        async def handler(req):
            requests.append(req)
            return httpx.Response(302,headers={'location':'https://evil.test/token-leak'})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']):
            self.assertEqual(await scraper.search(),[])
        self.assertEqual(len(requests),2)
        self.assertTrue(all(req.url.host=='api.reverb.com' for req in requests))
        self.assertIn('HTTP 302',scraper.health_error())

    async def test_5xx_retry_is_counted_and_does_not_replace_other_queries(self):
        scraper=ReverbScraper();scraper.reset_health();requests=[]
        async def handler(req):
            requests.append(req)
            return httpx.Response(503) if len(requests)==1 else httpx.Response(200,json={'listings':[]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']), \
             patch('scrapers.reverb.asyncio.sleep',new_callable=AsyncMock):
            self.assertEqual(await scraper.search(),[])
        self.assertEqual(len(requests),3)
        self.assertEqual(scraper.discovery_queries_attempted,3)
        self.assertEqual(requests[0].url,requests[1].url)
        self.assertEqual(requests[2].url.params.get('make'),'Zeta')

    async def test_late_confirmed_sold_withholds_prior_live_and_missing_cannot_reopen(self):
        from offer_verifier import saved_indexed_closed_reason
        scraper=ReverbScraper();scraper.reset_health();calls=[]
        sequence=[listing('123','live'),listing('123','sold'),listing('123',None),listing('123',None)]
        async def handler(req):
            calls.append(req)
            return httpx.Response(200,json={'listings':[sequence[len(calls)-1]]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']):
            self.assertEqual(await scraper.search(),[])
        self.assertEqual(len(calls),4)
        self.assertTrue(saved_indexed_closed_reason({'url':'https://reverb.com/item/123-zeta'}))

    async def test_confirmed_later_live_reopens_but_bad_identity_stays_unknown(self):
        from offer_verifier import saved_indexed_closed_reason
        scraper=ReverbScraper();scraper.reset_health();calls=[]
        sequence=[listing('123','sold'),listing('123',None),listing('123','live'),listing('123','live')]
        async def handler(req):
            calls.append(req)
            return httpx.Response(200,json={'listings':[sequence[len(calls)-1]]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,handler)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']):
            result=await scraper.search()
        self.assertEqual(len(result),1);self.assertTrue(result[0]['_reverb_api_live'])
        self.assertFalse(saved_indexed_closed_reason(result[0]))
        scraper=ReverbScraper();scraper.reset_health()
        bad=listing('456','live');bad['_links']['web']['href']='https://reverb.com/item/457-other'
        async def wrong(req): return httpx.Response(200,json={'listings':[bad]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:self.client(scraper,wrong)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']):
            result=await scraper.search()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['source'],'search');self.assertNotIn('_reverb_api_live',result[0])
        self.assertTrue(result[0]['_query_identity_unverified'])


if __name__=='__main__': unittest.main()
