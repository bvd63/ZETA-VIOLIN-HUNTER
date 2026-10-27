"""Cached Facebook candidate fallback, without Actor launches or external traffic."""
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
from scrapers.apify_facebook import ApifyFacebookScraper, normalize, store


def card(n=1001, title='Zeta violin acoustic electric capable – Keyes', **flags):
    raw={'listingTitle':title, 'itemUrl':f'https://www.facebook.com/marketplace/item/{n}/',
         'description':{'text':''}, **flags}
    return normalize(raw)


class FakeClient:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass


class FacebookUnknownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'isolated.db'))
        self.path.start()
        self.config = patch.multiple(Config, APIFY_TOKEN='fixture-token', APIFY_ENABLED=True,
            APIFY_DISCOVERY_TASK_ID='fixtureDiscovery', APIFY_DETAILS_TASK_ID='fixtureDetails',
            APIFY_DETAIL_TTL_HOURS=72)
        self.config.start()
        import main
        self.main=main
        self.db=database.Database()
        self.scraper=ApifyFacebookScraper()
        self.scraper.reset_health()

    async def asyncTearDown(self):
        self.db.close()
        self.config.stop()
        self.path.stop()
        self.tmp.cleanup()

    def put(self, candidate, details=None, checked=0):
        with store() as conn:
            conn.execute('INSERT OR REPLACE INTO apify_items(url,candidate,details,checked) VALUES (?,?,?,?)',
                         (candidate['url'],json.dumps(candidate),json.dumps(details) if details is not None else None,checked))
            conn.commit()

    async def cache_search(self):
        # The start path is invoked only as a guard refusal, with no reservation,
        # request, Actor or paid work. Persisted cache remains available.
        with patch.object(self.scraper,'make_client',return_value=FakeClient()), \
             patch.object(self.scraper,'_start',AsyncMock(return_value=(None,[]))), \
             patch.object(self.scraper,'_reserve',side_effect=AssertionError('no Actor reservation')), \
             patch.object(self.scraper,'read_run',AsyncMock(side_effect=AssertionError('no API'))):
            return await self.scraper.search()

    async def primary(self, items, status):
        transport=httpx.MockTransport(lambda request:httpx.Response(status,text='not available'))
        with patch('offer_verifier.public_dns',AsyncMock(return_value=True)), \
             patch('primary_page_reserve.fresh_primary_page',AsyncMock(return_value=None)):
            async with httpx.AsyncClient(transport=transport) as client:
                return await self.main._verify_search_hits(items,self.db,client,{},'search:Facebook Marketplace')

    async def test_strong_card_missing_details_reaches_unknown_outbox_without_seen(self):
        candidate=card();self.put(candidate)
        items=await self.cache_search()
        self.assertEqual([x['url'] for x in items],[candidate['url']])
        self.assertEqual(items[0]['source'],'search')
        self.assertTrue(items[0]['_direct_item_candidate'])
        self.assertNotIn('verification',items[0])
        for key in ('apify_is_live','apify_is_sold','apify_is_pending'):
            self.assertIsNone(items[0].get(key))
        ready=await self.primary(items,403)
        self.assertEqual(len(ready),1)
        self.assertEqual(ready[0]['verification'],'unknown')
        self.assertEqual([x['url'] for x in self.db.pending()],[candidate['url']])
        self.assertFalse(self.db.is_seen(candidate['id']))
        self.assertFalse(self.db.was_url_alerted(candidate['url']))
        from notifier import TelegramNotifier
        buttons=TelegramNotifier._alert_buttons('a'*24,candidate['url'])
        self.assertTrue(any('seen:' in button.get('callback_data','') for row in buttons['inline_keyboard'] for button in row))

    async def test_weak_accessory_and_known_closed_flags_never_fallback(self):
        for n,title,flags in [(1,'Zeta',{}),(2,'Zeta pickup',{}),
                              (3,'Zeta violin',{'isSold':True}),
                              (4,'Zeta violin',{'isPending':True}),
                              (5,'Zeta violin',{'isHidden':True}),
                              (6,'Zeta violin',{'isLive':False})]:
            self.put(card(n,title,**flags))
        self.assertEqual(await self.cache_search(),[])
        self.assertEqual(self.db.pending(),[])

    async def test_stale_or_partial_details_with_known_closed_status_do_not_fallback(self):
        for n,key in [(11,'apify_is_sold'),(12,'apify_is_pending'),(13,'apify_is_hidden')]:
            c=card(n);d=card(n);d[key]=True
            self.put(c,d,checked=1)  # even stale evidence prevents speculative fallback
        c=card(14);d=card(14);d['apify_is_live']=False;self.put(c,d,checked=1)
        self.assertEqual(await self.cache_search(),[])

    async def test_fresh_complete_other_product_never_borrows_discovery_identity(self):
        c=card(20);d=card(20,'Yamaha electric violin',isLive=True,isSold=False,isPending=False)
        self.put(c,d,checked=time.time())
        self.assertEqual(await self.cache_search(),[])
        self.assertEqual(self.db.pending(),[])

    async def test_primary_404_closes_instead_of_unknown(self):
        candidate=card(30);self.put(candidate)
        items=await self.cache_search()
        self.assertEqual(len(items),1)
        self.assertEqual(await self.primary(items,404),[])
        self.assertEqual(self.db.pending(),[])
        self.assertTrue(self.db.is_seen(candidate['id']))

    async def test_already_delivered_is_not_enqueued_again(self):
        candidate=card(40);self.put(candidate)
        self.db.record_alert(candidate,None)
        items=await self.cache_search()
        self.assertEqual(len(items),1)
        self.assertEqual(await self.primary(items,403),[])
        self.assertEqual(self.db.pending(),[])

    async def test_wrong_id_partial_detail_never_replaces_original_candidate(self):
        candidate=card(50);wrong=card(51,'Zeta Jazz Fusion JV5 violin',isLive=True)
        self.put(candidate,wrong,checked=0)
        items=await self.cache_search()
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['url'],candidate['url'])
        self.assertEqual(items[0]['title'],candidate['title'])
        self.assertNotIn('JV5',items[0]['title'])
        self.assertEqual(len(await self.primary(items,403)),1)
        self.assertEqual(self.db.pending()[0]['url'],candidate['url'])

    async def test_new_negative_discovery_replaces_cached_live_and_prevents_alert(self):
        for index, flags in enumerate(({'isSold':True}, {'isPending':True},
                                       {'isHidden':True}, {'isLive':False}),start=101):
            with self.subTest(flags=flags):
                c=card(index)
                live=card(index,isLive=True,isSold=False,isPending=False,isHidden=False)
                self.put(c,live,checked=time.time())
                raw={'listingTitle':'Zeta violin', 'itemUrl':c['url'], **flags}
                self.scraper._consume('fixture-discovery','discovery',[raw])
                with store() as conn:
                    candidate,details,checked=conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',(c['url'],)).fetchone()
                candidate,details=json.loads(candidate),json.loads(details)
                mapping={'isSold':'apify_is_sold','isPending':'apify_is_pending',
                         'isHidden':'apify_is_hidden','isLive':'apify_is_live'}
                for key,value in flags.items():
                    self.assertEqual(candidate[mapping[key]],value)
                    self.assertEqual(details[mapping[key]],value)
                self.assertGreater(checked,0)
                self.assertEqual(await self.cache_search(),[])
                c.update({'source':'search','_direct_item_candidate':True})
                self.assertEqual(await self.primary([c],403),[])
                self.assertEqual(self.db.pending(),[])

    async def test_fresh_cached_pending_is_hold_not_seen(self):
        c=card(120);pending=card(120,isLive=True,isSold=False,isPending=True)
        self.put(c,pending,checked=time.time())
        c.update({'source':'search','_direct_item_candidate':True})
        self.assertEqual(await self.primary([c],403),[])
        self.assertEqual(self.db.pending(),[])
        self.assertFalse(self.db.is_seen(c['id']))
        self.assertFalse(self.db.was_url_alerted(c['url']))

    async def test_negative_discovery_repairs_mismatched_cached_identity(self):
        c=card(119)
        other=card(9999,isLive=True,isSold=False,isPending=False,isHidden=False)
        self.put(c,other,checked=time.time())
        self.scraper._consume('negative-after-wrong-cache','discovery',[
            {'itemUrl':c['url'],'listingTitle':c['title'],'isSold':True}])
        from facebook_discovery import cached_offer
        self.assertEqual(cached_offer(c['url'])['listing']['url'],c['url'])
        self.assertTrue(cached_offer(c['url'])['listing']['apify_is_sold'])
        c.update({'source':'search','_direct_item_candidate':True})
        self.assertEqual(await self.primary([c],403),[])
        self.assertEqual(self.db.pending(),[])

    async def test_fresh_active_discovery_invalidates_old_sold_until_primary(self):
        c=card(130);old=card(130,isSold=True)
        self.put(c,old,checked=time.time())
        raw={'listingTitle':'Zeta violin acoustic electric capable – Keyes','itemUrl':c['url'],
             'isLive':True,'isSold':False,'isPending':False,'isHidden':False}
        self.scraper._consume('fixture-discovery','discovery',[raw])
        with store() as conn:
            candidate,details,checked=conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',(c['url'],)).fetchone()
        self.assertIsNone(details)
        self.assertEqual(checked,0)
        self.assertFalse(json.loads(candidate)['apify_is_sold'])
        items=await self.cache_search()
        self.assertEqual([x['url'] for x in items],[c['url']])
        self.assertNotIn('verification',items[0])
        self.assertEqual(len(await self.primary(items,403)),1)
        self.assertEqual(self.db.pending()[0]['verification'],'unknown')

    async def test_stale_pending_cache_still_blocks_already_queued_403(self):
        c=card(145);pending=card(145,isLive=True,isSold=False,isPending=True)
        self.put(c,pending,checked=time.time()-7*3600)
        c.update({'source':'search','_direct_item_candidate':True})
        self.assertEqual(await self.primary([c],403),[])
        self.assertEqual(self.db.pending(),[])
        self.assertFalse(self.db.is_seen(c['id']))
        self.assertFalse(self.db.was_url_alerted(c['url']))

    async def test_discovery_missing_hidden_preserves_old_hold_and_timestamp(self):
        c=card(146,isLive=True,isSold=False,isPending=False,isHidden=True)
        self.put(c,c,checked=1)
        raw={'listingTitle':c['title'],'itemUrl':c['url'],
             'isLive':True,'isSold':False,'isPending':False}
        self.scraper._consume('discovery-incomplete-hidden','discovery',[raw])
        with store() as conn:
            candidate,details,checked=conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',(c['url'],)).fetchone()
        self.assertTrue(json.loads(candidate)['apify_is_hidden'])
        self.assertTrue(json.loads(details)['apify_is_hidden'])
        self.assertEqual(checked,1)  # inherited evidence is not a new observation
        self.assertEqual(await self.cache_search(),[])
        self.scraper._consume('discovery-explicit-hidden-false','discovery',[{**raw,'isHidden':False}])
        with store() as conn:
            candidate,details,checked=conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',(c['url'],)).fetchone()
        self.assertFalse(json.loads(candidate)['apify_is_hidden'])
        self.assertIsNone(details)
        self.assertEqual(checked,0)
        self.assertEqual([x['url'] for x in await self.cache_search()],[c['url']])

    async def test_detail_missing_hidden_keeps_hold_until_explicit_false(self):
        c=card(147,isLive=True,isSold=False,isPending=False,isHidden=True)
        self.put(c,c,checked=1)
        raw={'listingTitle':c['title'],'itemUrl':c['url'],
             'isLive':True,'isSold':False,'isPending':False}
        with store() as conn:
            conn.execute('INSERT INTO apify_batch_targets VALUES (?,?)',('detail-hidden-unknown',c['url']))
            conn.execute('INSERT INTO apify_batch_targets VALUES (?,?)',('detail-hidden-clear',c['url']))
            conn.commit()
        self.scraper._consume('detail-hidden-unknown','details',[raw])
        with store() as conn:
            candidate,details=conn.execute('SELECT candidate,details FROM apify_items WHERE url=?',(c['url'],)).fetchone()
        self.assertTrue(json.loads(candidate)['apify_is_hidden'])
        self.assertTrue(json.loads(details)['apify_is_hidden'])
        self.assertEqual(await self.cache_search(),[])
        self.scraper._consume('detail-hidden-clear','details',[{**raw,'isHidden':False}])
        with store() as conn:
            candidate,details=conn.execute('SELECT candidate,details FROM apify_items WHERE url=?',(c['url'],)).fetchone()
        self.assertFalse(json.loads(candidate)['apify_is_hidden'])
        self.assertFalse(json.loads(details)['apify_is_hidden'])
        self.assertEqual([x['url'] for x in await self.cache_search()],[c['url']])

    async def test_expired_pending_is_rechecked_in_existing_detail_slots_without_rediscovery(self):
        stale=card(150,isLive=True,isSold=False,isPending=True,isHidden=False)
        recent=card(151,isLive=True,isSold=False,isPending=True,isHidden=False)
        self.put(stale,stale,checked=1)
        self.put(recent,recent,checked=time.time())
        calls=[]
        async def guard(_client,phase,targets,cap):
            calls.append((phase,targets))
            if phase=='discovery':
                return None, []  # ordinary guard: no discovery Actor
            self.assertEqual(phase,'details')
            self.assertLessEqual(len(targets),2)
            self.assertEqual([row['url'] for row in targets],[stale['url']])
            # A requested-ID response with explicit positive flags and complete identity.
            with store() as conn:
                conn.execute('INSERT INTO apify_batch_targets VALUES (?,?)',('fixture-retry',stale['url']))
                conn.commit()
            return 'fixture-retry',[{'listingTitle':'Zeta violin acoustic electric capable – Keyes',
                                     'itemUrl':stale['url'],'isLive':True,'isSold':False,
                                     'isPending':False,'isHidden':False}]
        with patch.object(self.scraper,'make_client',return_value=FakeClient()), \
             patch.object(self.scraper,'_start',side_effect=guard), \
             patch.object(self.scraper,'_reserve',side_effect=AssertionError('no reservation')), \
             patch.object(self.scraper,'read_run',AsyncMock(side_effect=AssertionError('no API'))):
            result=await self.scraper.search()
        self.assertEqual([entry[0] for entry in calls],['discovery','details'])
        self.assertEqual([x['url'] for x in result],[stale['url']])
        self.assertFalse(result[0]['apify_is_pending'])
        with store() as conn:
            candidate,detail,checked=conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',
                                                   (stale['url'],)).fetchone()
            recent_checked=conn.execute('SELECT checked FROM apify_items WHERE url=?',(recent['url'],)).fetchone()[0]
        self.assertFalse(json.loads(candidate)['apify_is_pending'])
        self.assertFalse(json.loads(detail)['apify_is_pending'])
        self.assertGreater(checked,time.time()-60)
        self.assertGreater(recent_checked,time.time()-60)
        self.assertEqual(self.db.pending(),[])

    async def test_wrong_id_fresh_cached_negative_cannot_close_original(self):
        c=card(135);wrong=card(136,'Zeta violin',isSold=True)
        self.put(c,wrong,checked=time.time())
        c.update({'source':'search','_direct_item_candidate':True})
        result=await self.primary([c],403)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['url'],c['url'])
        self.assertEqual(result[0]['verification'],'unknown')
        self.assertFalse(self.db.is_seen(c['id']))
        self.assertEqual([item['url'] for item in self.db.pending()],[c['url']])

    async def test_wrong_id_negative_detail_cannot_poison_original(self):
        c=card(140);self.put(c)
        with store() as conn:
            conn.execute('INSERT INTO apify_batch_targets VALUES (?,?)',('fixture-details',c['url']))
            conn.commit()
        wrong={'listingTitle':'Zeta violin','itemUrl':card(141)['url'],'isSold':True}
        self.scraper._consume('fixture-details','details',[wrong])
        with store() as conn:
            saved,details,checked=conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',(c['url'],)).fetchone()
            self.assertIsNone(conn.execute('SELECT url FROM apify_items WHERE url=?',(card(141)['url'],)).fetchone())
        self.assertEqual(json.loads(saved)['url'],c['url'])
        self.assertIsNone(details)
        self.assertEqual(checked,0)
        items=await self.cache_search()
        self.assertEqual([x['url'] for x in items],[c['url']])
        self.assertEqual(len(await self.primary(items,403)),1)


if __name__ == '__main__': unittest.main()
