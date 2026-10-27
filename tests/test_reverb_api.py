"""Offline tests for Reverb PAT headers, state, identity and redirect safety."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import database
from config import Config
from liveness import is_live
from offer_verifier import verify_offer
from reverb_api import build_headers, get_listing, listing_state, remember_availability

URL='https://reverb.com/item/123-zeta-violin'


class ReverbApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'test.db'))
        self.dbpatch.start()

    async def asyncTearDown(self):
        self.dbpatch.stop()
        self.tmp.cleanup()

    def test_primary_sold_is_shared_across_sources_and_live_clears_only_same_id(self):
        from offer_verifier import saved_indexed_closed_reason, availability_conflict
        from provider_state import read
        with tempfile.TemporaryDirectory() as tmp, patch.object(database,'DB_PATH',str(Path(tmp)/'test.db')):
            item={'id':123,'state':{'slug':'sold'},'_links':{'web':{'href':URL}}}
            self.assertEqual(remember_availability(item),'sold')
            indexed={'url':'https://www.reverb.com/en-ro/item/123-another-slug','source':'search'}
            self.assertTrue(availability_conflict(indexed,'unknown'))
            saved=read('availability:indexed_closed:reverb:123')
            self.assertEqual(saved['source'],'primary_api')
            self.assertIn('state=sold',saved['reason'])
            self.assertFalse(saved_indexed_closed_reason({'url':'https://reverb.com/item/124-x'}))
            for invalid in ({**item,'state':{'slug':'mystery'}},
                            {**item,'id':124}, {**item,'id':None},
                            {**item,'_links':{'web':{'href':'https://reverb.com.evil.test/item/123-x'}}},
                            {**item,'_links':{'web':{'href':'https://reverb.com/item/124-x'}}},
                            {**item,'_links':{}}, {**item,'state':None}):
                self.assertIsNone(remember_availability(invalid))
                self.assertEqual(read('availability:indexed_closed:reverb:123'),saved)
            self.assertEqual(remember_availability({**item,'state':{'slug':'live'}}),'live')
            self.assertFalse(saved_indexed_closed_reason(indexed))
            self.assertFalse(availability_conflict(indexed,'unknown'))
    def test_pat_and_anonymous_headers(self):
        with patch.object(Config,'REVERB_API_TOKEN','fixture-secret'):
            headers=build_headers()
        self.assertEqual(headers['Authorization'],'Bearer fixture-secret')
        self.assertEqual(headers['Accept'],'application/hal+json')
        self.assertEqual(headers['Content-Type'],'application/hal+json')
        self.assertEqual(headers['Accept-Version'],'3.0')
        self.assertNotIn('X-Auth-Token',headers)
        with patch.object(Config,'REVERB_API_TOKEN',''):
            self.assertNotIn('Authorization',build_headers())

    async def test_live_sold_unknown_id_and_invalid_payload(self):
        cases=[({'id':123,'title':'Zeta violin','state':{'slug':'live'}},'live'),
               ({'id':'123','state':{'slug':'sold'}},'dead'),
               ({'id':124,'state':{'slug':'live'}},'unknown'),
               ({'id':124,'state':{'slug':'sold'}},'unknown'),
               ({'id':True,'state':{'slug':'live'}},'unknown'),
               ({'id':123,'state':{'slug':'mystery'}},'unknown'),
               ({'id':123,'state':None},'unknown'),
               ({'id':123,'state':{'slug':'live'},'description':['wrong']},'unknown'),
               ([], 'unknown')]
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('facebook_discovery.cached_offer',return_value=None), \
             patch.object(Config,'REVERB_API_TOKEN','fixture-secret'):
            for data,expected in cases:
                with self.subTest(data=data):
                    calls=[]
                    def handler(req):
                        calls.append(req)
                        return httpx.Response(200,json=data) if req.url.host=='api.reverb.com' else httpx.Response(403)
                    item={'url':URL,'title':'Zeta violin','source':'search'}
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                        state,_=await verify_offer(item,c)
                    self.assertEqual(state,expected)
                    self.assertEqual(len(calls),2 if expected=='unknown' else 1)
                    self.assertEqual(calls[0].headers['authorization'],'Bearer fixture-secret')
                    if expected=='unknown': self.assertNotIn('authorization',calls[1].headers)
                    if expected=='unknown': self.assertNotIn('_primary_content_checked',item)

    async def test_invalid_json_and_unknown_liveness_are_not_ended(self):
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('facebook_discovery.cached_offer',return_value=None):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='bad json') if r.url.host=='api.reverb.com' else httpx.Response(403))) as c:
                self.assertEqual((await verify_offer({'url':URL,'title':'Zeta violin','source':'search'},c))[0],'unknown')
                self.assertTrue((await is_live(URL,c))[0])

    async def test_incomplete_api_falls_back_to_same_item_live_or_sold_html(self):
        live='<main><h1>Zeta violin</h1><p>For sale $900</p></main>'
        sold='<main><h1>Zeta violin</h1><p>This gear has sold</p></main>'
        for api in ({'state':{'slug':'unknown'}}, {'id':124,'state':{'slug':'live'}},
                    {'id':123,'state':{'slug':'live'},'description':[]},None):
            for body,expected in ((live,'live'),(sold,'dead')):
                with self.subTest(api=api,expected=expected):
                    calls=[]
                    def handler(req):
                        calls.append(req)
                        if req.url.host=='api.reverb.com':
                            return httpx.Response(200,json=api) if api is not None else httpx.Response(200,text='invalid')
                        return httpx.Response(200,text=body)
                    with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                         patch('facebook_discovery.cached_offer',return_value=None), \
                         patch.object(Config,'REVERB_API_TOKEN','fixture-secret'):
                        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                            state,_=await verify_offer({'url':URL,'title':'Zeta violin','source':'search'},c)
                            alive,_=await is_live(URL,c)
                    self.assertEqual(state,expected)
                    self.assertEqual(alive,expected=='live')
                    self.assertEqual([x.url.host for x in calls],['api.reverb.com','reverb.com']*2)
                    self.assertNotIn('authorization',calls[1].headers)
                    self.assertNotIn('authorization',calls[3].headers)

    async def test_contradictory_web_link_cannot_prove_live_or_sold(self):
        urls=('https://reverb.com/item/124-other', 'https://reverb.com.evil.example/item/123-x',
              'https://user@reverb.com/item/123-x', 'https://reverb.com:444/item/123-x',
              'ftp://reverb.com/item/123-x', 'not a URL', None)
        for slug in ('live','sold'):
            for bad in urls:
                with self.subTest(slug=slug,bad=bad):
                    data={'id':123,'state':{'slug':slug},'_links':{'web':{'href':bad}}}
                    self.assertEqual(listing_state(data,'123'),'unknown')
                    calls=[]
                    def handler(req):
                        calls.append(req)
                        if req.url.host=='api.reverb.com': return httpx.Response(200,json=data)
                        return httpx.Response(200,text='<main><h1>Zeta violin</h1><p>For sale $900</p></main>')
                    with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                         patch('facebook_discovery.cached_offer',return_value=None), \
                         patch.object(Config,'REVERB_API_TOKEN','fixture-secret'):
                        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                            state,_=await verify_offer({'url':URL,'title':'Zeta violin','source':'search'},c)
                    self.assertEqual(state,'live')  # Primary HTML supplies independent evidence.
                    self.assertEqual([req.url.host for req in calls],['api.reverb.com','reverb.com'])
                    self.assertNotIn('authorization',calls[1].headers)
        for slug in ('live','sold'):
            self.assertEqual(listing_state({'id':123,'state':{'slug':slug}},'123'),slug)
            self.assertEqual(listing_state({'id':123,'state':{'slug':slug},'_links':{'web':{'href':URL}}},'123'),slug)

    async def test_present_malformed_web_field_never_proves_state(self):
        for slug in ('live','sold'):
            for web in ('https://reverb.com/item/124-other',[],{},None):
                with self.subTest(slug=slug,web=web):
                    data={'id':123,'state':{'slug':slug},'_links':{'web':web}}
                    self.assertEqual(listing_state(data,'123'),'unknown')
                    calls=[]
                    def handler(req):
                        calls.append(req)
                        return httpx.Response(200,json=data) if req.url.host=='api.reverb.com' else httpx.Response(403)
                    with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                         patch('facebook_discovery.cached_offer',return_value=None), \
                         patch.object(Config,'REVERB_API_TOKEN','fixture-secret'):
                        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                            state,_=await verify_offer({'url':URL,'title':'Zeta violin','source':'search'},c)
                    self.assertEqual(state,'unknown')
                    self.assertEqual([r.url.host for r in calls],['api.reverb.com','reverb.com'])
                    self.assertNotIn('authorization',calls[1].headers)

    async def test_live_status_only_needs_primary_identity_and_cannot_borrow_index_title(self):
        from main import _needs_primary_identity
        from filters import classify
        cases=[({'id':123,'state':{'slug':'live'}},403,'','unknown',True),
               ({'id':123,'state':{'slug':'live'}},200,
                '<main><h1>Zeta Jazz Fusion violin</h1><p>For sale $900</p></main>','live',False),
               ({'id':123,'state':{'slug':'live'},'title':'Yamaha guitar'},403,'','live',False),
               ({'id':123,'state':{'slug':'live'},'make':'Zeta','model':'JV5'},403,'','live',False),
               ({'id':123,'state':{'slug':'live'},'description':'<p>Used Zeta JV5 electric violin with original case.</p>'},403,'','live',False)]
        for api,status,body,expected,needs in cases:
            with self.subTest(api=api,status=status):
                calls=[]
                def handler(req):
                    calls.append(req)
                    return httpx.Response(200,json=api) if req.url.host=='api.reverb.com' else httpx.Response(status,text=body)
                item={'url':URL,'title':'Zeta Jazz Fusion violin','description':'index only','source':'search'}
                with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                     patch('facebook_discovery.cached_offer',return_value=None), \
                     patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value='')):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                        state,_=await verify_offer(item,c)
                self.assertEqual(state,expected)
                self.assertEqual(_needs_primary_identity(item,state),needs)
                if needs:
                    self.assertTrue(item['_query_identity_unverified'])
                    self.assertNotIn('_primary_content_checked',item)
                if api.get('title')=='Yamaha guitar':
                    self.assertEqual(item['title'],'Yamaha guitar');self.assertTrue(classify(item))
                if api.get('make')=='Zeta':
                    self.assertEqual(item['title'],'');self.assertEqual(classify(item),'')
                if api.get('description'):
                    self.assertEqual(item['title'],'')
                    self.assertIn('Used Zeta JV5',item['description'])
                    self.assertTrue(item['_primary_content_checked'])
                    self.assertEqual(classify(item),'')

    async def test_reverb_other_id_or_host_never_fetches_or_borrows_status(self):
        from offer_verifier import inspect_page
        for original in (URL,URL+'?utm_source=google'):
            for destination in ('https://reverb.com/item/124-other','https://evil.example/item/123-other',
                                'https://reverb.com@evil.example/item/123', 'ftp://reverb.com/item/123'):
                for status in (200,404,410):
                    item={'url':original,'title':'Zeta violin','description':'original','source':'search'}
                    self.assertEqual(inspect_page(item,'<main><h1>Other</h1><p>This gear has sold</p></main>',status,destination)[0],'unknown')
                    self.assertEqual((item['title'],item['description']),('Zeta violin','original'))
                calls=[]
                def handler(req):
                    calls.append(req)
                    return httpx.Response(403) if req.url.host=='api.reverb.com' else httpx.Response(302,headers={'location':destination})
                with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                     patch('facebook_discovery.cached_offer',return_value=None):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as c:
                        self.assertEqual((await verify_offer({'url':original,'title':'Zeta violin','source':'search'},c))[0],'unknown')
                        self.assertTrue((await is_live(original,c))[0])
                self.assertEqual([r.url.host for r in calls],['api.reverb.com','reverb.com']*2)

    async def test_reverb_same_id_locale_and_slug_redirect_can_use_primary(self):
        alias='https://www.reverb.com/en-ro/item/123-other-name'
        for body,expected in (('<main><h1>Zeta violin</h1><p>For sale $900</p></main>','live'),
                              ('<main><h1>Zeta violin</h1><p>This gear has sold</p></main>','dead')):
            def handler(req):
                if req.url.host=='api.reverb.com': return httpx.Response(200,json={'state':None})
                if str(req.url)==URL: return httpx.Response(302,headers={'location':alias})
                return httpx.Response(200,text=body)
            with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
                 patch('facebook_discovery.cached_offer',return_value=None):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                    self.assertEqual((await verify_offer({'url':URL,'title':'Zeta violin','source':'search'},c))[0],expected)
                    self.assertEqual((await is_live(URL,c))[0],expected=='live')

    async def test_api_redirect_does_not_leak_token_and_html_403_is_unknown(self):
        calls=[]
        def handler(req):
            calls.append(req)
            if req.url.host=='api.reverb.com':
                return httpx.Response(302,headers={'location':'https://evil.example/collect'})
            return httpx.Response(403)
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('facebook_discovery.cached_offer',return_value=None), \
             patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value='')), \
             patch.object(Config,'REVERB_API_TOKEN','fixture-secret'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as c:
                state,_=await verify_offer({'url':URL,'title':'Zeta violin','source':'search'},c)
        self.assertEqual(state,'unknown')
        self.assertEqual([r.url.host for r in calls],['api.reverb.com','reverb.com'])
        self.assertNotIn('authorization',calls[1].headers)

    async def test_get_listing_rejects_nonnumeric_id_before_http(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:self.fail('unexpected HTTP'))) as c:
            for bad in ('123/../../x','https://evil.example','12?x=1'):
                with self.assertRaises(ValueError): await get_listing(c,bad)


if __name__=='__main__': unittest.main()
