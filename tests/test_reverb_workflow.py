"""Reverb reactivation workflow with simulated HTTP and an isolated database."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import database
from config import Config
from price_tracker import PriceTracker
from scrapers.reverb import ReverbScraper

URL='https://reverb.com/item/123-zeta-jv5'


def api_item(state='live', url=URL):
    return {'id':123, 'title':'Zeta Jazz Fusion JV5 electric violin',
            'state':{'slug':state} if state is not None else None,
            '_links':{'web':{'href':url}}, 'description':'Used Zeta violin',
            'make':'Zeta','model':'JV5','price':{'amount':'1500','currency':'USD'},
            'condition':{'display_name':'Excellent'}}


class ReverbWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dbpatch=patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'test.db'));self.dbpatch.start()
        self.config=patch.multiple(Config,REVERB_API_TOKEN='fixture',SCRAPER_RETRIES=0);self.config.start()
        self.db=database.Database();self.prices=PriceTracker()
        import main
        self.main=main

    async def asyncTearDown(self):
        self.prices.close();self.db.close();self.config.stop();self.dbpatch.stop();self.tmp.cleanup()

    async def run_fixture(self, state='live', url=URL, source_name='Reverb', delivered=False):
        scraper=ReverbScraper();scraper.name=source_name
        item_id=scraper._make_id('reverb','123')
        prior={'id':item_id,'platform':'Reverb','url':URL,'title':'Zeta violin','price':'1500 USD'}
        self.db.mark_seen(item_id,prior)
        self.db.mark_dead(item_id,'old ended listing')
        before=self.db.conn.execute('SELECT data,first_seen FROM seen_listings WHERE id=?',(item_id,)).fetchone()
        if delivered:
            self.db.record_alert(prior,1500)
        calls=[]
        def handler(req):
            calls.append(req)
            return httpx.Response(200,json={'listings':[api_item(state,url)]})
        original=scraper.make_client
        async def unknown(item,_client): return ('unknown','fixture 403')
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']), \
             patch('main.verify_offer',AsyncMock(side_effect=unknown)) as verify:
            result=await self.main._run_scraper_with_resilience(scraper,self.db,self.prices,asyncio.Semaphore(1))
        after=self.db.conn.execute('SELECT data,first_seen FROM seen_listings WHERE id=?',(item_id,)).fetchone()
        self.assertEqual(before,after)
        self.assertTrue(self.db.is_seen(item_id))
        self.assertEqual(len(calls),4)
        self.assertTrue(all(req.url.host=='api.reverb.com' for req in calls))
        return result,verify,item_id

    async def test_current_primary_live_reopens_unalerted_without_erasing_seen(self):
        result,verify,item_id=await self.run_fixture()
        self.assertEqual(len(result['new']),1)
        self.assertEqual(result['new'][0]['url'],URL)
        self.assertTrue(result['new'][0]['_reverb_api_live'])
        self.assertFalse(self.db.was_url_alerted(URL))
        verify.assert_not_awaited()

    async def test_already_delivered_url_is_never_reopened(self):
        result,verify,_=await self.run_fixture(delivered=True)
        self.assertEqual(result['new'],[])
        self.assertTrue(self.db.was_url_alerted(URL))
        verify.assert_not_awaited()

    async def test_unknown_state_cannot_override_seen_or_dead(self):
        result,verify,_=await self.run_fixture(state=None)
        self.assertEqual(result['new'],[])
        self.assertEqual(self.db.pending(),[])
        verify.assert_not_awaited()

    async def test_other_provider_flag_does_not_override_seen(self):
        result,verify,_=await self.run_fixture(source_name='Other marketplace')
        self.assertEqual(result['new'],[])
        self.assertEqual(self.db.pending(),[])
        verify.assert_not_awaited()

    async def test_mismatched_api_item_id_cannot_claim_live_or_override_history(self):
        result,verify,_=await self.run_fixture(url='https://reverb.com/item/124-other')
        self.assertEqual(result['new'],[])
        self.assertEqual(self.db.pending(),[])
        verify.assert_not_awaited()

    async def test_mismatched_id_without_dead_cache_requires_primary_identity(self):
        scraper=ReverbScraper();item_id=scraper._make_id('reverb','123')
        self.db.mark_seen(item_id,{'id':item_id,'platform':'Reverb','url':URL,'title':'Zeta violin'})
        original=scraper.make_client
        async def unknown(item,_client): return ('unknown','fixture 403')
        def handler(req):
            return httpx.Response(200,json={'listings':[api_item('live','https://reverb.com/item/124-other')]})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)), \
             patch('scrapers.reverb.market_queries',return_value=['zeta']), \
             patch('main.verify_offer',AsyncMock(side_effect=unknown)) as verify:
            result=await self.main._run_scraper_with_resilience(scraper,self.db,self.prices,asyncio.Semaphore(1))
        self.assertEqual(result['new'],[])
        self.assertEqual(self.db.pending(),[])
        verify.assert_awaited_once()
        self.assertTrue(verify.await_args.args[0]['_query_identity_unverified'])


if __name__=='__main__': unittest.main()
