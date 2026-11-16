"""Owner FAKE feedback: exact identity, undo, durability and delivery isolation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from alert_receipts import AlertReceipts
from listing_feedback import offer_key, hold_reason
from notifier import TelegramNotifier
from telegram_commands import TelegramCommands


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch('database.DB_PATH',str(Path(self.tmp.name)/'feedback.db'))
        self.db_patch.start()
        self.receipts = AlertReceipts()
        self.item = {'url':'https://reverb.com/item/123-zeta-jazz-fusion-jv5'}
        self.rid = self.receipts.prepare('owner',self.item,'Zeta Jazz Fusion JV5 — used')
        self.receipts.delivered(self.rid,101,now=1000)

    def tearDown(self):
        self.receipts.close()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_fake_retains_example_and_survives_restart(self):
        self.assertTrue(self.receipts.feedback(self.rid,'owner','fake'))
        second = AlertReceipts()
        try:
            self.assertEqual(second.feedback_state(self.rid,'owner')['fake'],True)
            self.assertEqual(second.claim_due('owner',now=1e12),[])
            self.assertEqual(second.claim_push_due('owner',now=1e12),[])
            row = second.conn.execute('SELECT label,url,alert_text FROM alert_feedback_history').fetchone()
            self.assertEqual(row,('fake',self.item['url'],'Zeta Jazz Fusion JV5 — used'))
            self.assertTrue(second.conn.execute('SELECT 1 FROM alert_receipts WHERE id=?',(self.rid,)).fetchone())
        finally:
            second.close()

    def test_fake_applies_only_to_same_offer_not_model_price_or_merchant(self):
        self.receipts.feedback(self.rid,'owner','fake')
        self.assertTrue(hold_reason('https://www.reverb.com/fr/item/123-new-slug?utm_source=x'))
        for url in ('https://reverb.com/item/124-zeta-jazz-fusion-jv5',
                    'https://reverb.com/item/1234-zeta','https://other.example/item/123',
                    'https://reverb.com.evil.example/item/123', 'https://reverb.com:444/item/123'):
            self.assertEqual(hold_reason(url),'',url)

    def test_facebook_identity_aliases_and_foreign_hosts(self):
        urls = ('https://facebook.com/marketplace/item/456/',
                'https://www.facebook.com/marketplace/item/456?ref=search',
                'https://m.facebook.com/marketplace/item/456/',
                'https://web.facebook.com/marketplace/item/456/')
        self.assertEqual(len({offer_key(u) for u in urls}),1)
        self.assertNotEqual(offer_key(urls[0]),offer_key('https://facebook.com.evil.example/marketplace/item/456/'))
        self.assertNotEqual(offer_key(urls[0]),offer_key('https://facebook.com/marketplace/item/4567/'))
        for url in ('https://[bad', 'https://user:pass@facebook.com/marketplace/item/456', 'https://facebook.com:444/marketplace/item/456'):
            self.assertEqual(offer_key(url),'')

    def test_wrong_chat_or_unknown_receipt_cannot_train_or_clear(self):
        for label in ('fake','clear_fake','ended'):
            self.assertFalse(self.receipts.feedback(self.rid,'other',label))
        self.assertFalse(self.receipts.feedback('0'*24,'owner','fake'))
        self.assertFalse(self.receipts.feedback(self.rid,'owner','blacklist_model'))
        self.assertEqual(self.receipts.conn.execute('SELECT count(*) FROM alert_feedback_history').fetchone()[0],0)
        self.assertTrue(self.receipts.is_pending(self.rid))

    def test_retry_is_idempotent_and_undo_keeps_history_and_ack(self):
        self.receipts.feedback(self.rid,'owner','fake')
        stamp = self.receipts.conn.execute('SELECT acknowledged_at FROM alert_receipts WHERE id=?',(self.rid,)).fetchone()[0]
        self.receipts.feedback(self.rid,'owner','fake')
        self.assertEqual(self.receipts.conn.execute('SELECT count(*) FROM alert_feedback_history').fetchone()[0],1)
        self.assertTrue(self.receipts.feedback(self.rid,'owner','clear_fake'))
        self.assertFalse(hold_reason(self.item['url']))
        self.assertFalse(self.receipts.feedback_state(self.rid,'owner')['fake'])
        self.assertEqual(self.receipts.conn.execute('SELECT acknowledged_at,next_reminder,push_next_attempt FROM alert_receipts WHERE id=?',(self.rid,)).fetchone(),(stamp,None,None))
        self.assertEqual([x[0] for x in self.receipts.conn.execute('SELECT label FROM alert_feedback_history ORDER BY id')],['fake','clear_fake'])

    def test_undo_restores_previous_correction(self):
        self.receipts.feedback(self.rid,'owner','ended')
        self.receipts.feedback(self.rid,'owner','fake')
        self.receipts.feedback(self.rid,'owner','clear_fake')
        self.assertEqual(self.receipts.conn.execute('SELECT label FROM alert_feedback WHERE receipt_id=?',(self.rid,)).fetchone()[0],'ended')

    def test_ended_blocks_unknown_delivery_siblings_without_inventing_reading(self):
        from listing_feedback import ended_reason
        from offer_verifier import availability_conflict
        drop=self.receipts.prepare('owner',self.item,'drop','drop:900')
        self.receipts.delivered(drop,102,now=1000)
        self.receipts.push_starting(drop);self.receipts.push_sent(drop,'a'*30,now=1000)
        self.receipts.feedback(self.rid,'owner','ended')
        self.assertTrue(ended_reason(self.item['url']))
        self.assertTrue(availability_conflict(self.item,'unknown'))
        self.assertTrue(availability_conflict(self.item,'live'))  # generic price alone is not reactivation
        self.assertFalse(availability_conflict({'url':'https://reverb.com/item/124-zeta'},'unknown'))
        self.assertFalse(self.receipts.is_pending(drop))
        self.assertEqual(self.receipts.conn.execute('SELECT acknowledged_at,next_reminder,push_next_attempt FROM alert_receipts WHERE id=?',(drop,)).fetchone(),(None,None,None))
        self.assertTrue(self.receipts.push_active('owner')[0]['technically_closed'])
        self.assertEqual(self.receipts.claim_due('owner',now=1e12),[])

    def test_ended_outbox_does_not_starve_other_offers_or_stop_primary_rechecks(self):
        from database import Database
        from discovery_queue import DiscoveryQueue
        db=Database()
        try:
            db.enqueue({**self.item,'id':'held'})
            db.enqueue({'id':'other','url':'https://reverb.com/item/124-zeta'})
            self.receipts.feedback(self.rid,'owner','ended')
            self.assertEqual([x['id'] for x in db.pending(limit=1)],['other'])
            self.assertEqual(db.conn.execute('SELECT count(*) FROM pending_alerts').fetchone()[0],2)
            with DiscoveryQueue('search:fixture') as q:
                q.put([{**self.item,'id':'held'}]);self.assertEqual(len(q.claim(1)),1)
        finally:db.close()

    async def test_owner_ended_requires_newer_explicit_primary_stock(self):
        import httpx
        from tests.test_reverb_workflow import api_item
        from offer_verifier import verify_offer,availability_conflict
        from listing_feedback import ended_reason,clear_ended_with_primary
        from work_context import spending_scope
        self.receipts.feedback(self.rid,'owner','ended')
        for stamp in (0,float('inf'),float('nan'),1e20):clear_ended_with_primary(self.item['url'],stamp)
        self.assertTrue(ended_reason(self.item['url']))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=api_item()))) as client:
            with patch('offer_verifier.public_dns',AsyncMock(return_value=True)),spending_scope('queue'):
                item={**self.item,'title':'Zeta Jazz Fusion JV5'};state,_=await verify_offer(item,client)
        self.assertEqual(state,'live');self.assertFalse(availability_conflict(item,state))
        self.assertFalse(ended_reason(self.item['url']))
        self.assertEqual(self.receipts.conn.execute('SELECT label FROM alert_feedback WHERE receipt_id=?',(self.rid,)).fetchone()[0],'ended')
        self.assertEqual(self.receipts.conn.execute('SELECT next_reminder,push_next_attempt FROM alert_receipts WHERE id=?',(self.rid,)).fetchone(),(None,None))

    async def test_price_only_html_does_not_overrule_owner_ended(self):
        import httpx,json
        from offer_verifier import verify_offer,availability_conflict
        from work_context import spending_scope
        item={'url':'https://example.org/products/zeta-jv5','title':'Zeta Jazz Fusion JV5','source':'search'}
        rid=self.receipts.prepare('owner',item,'fixture');self.receipts.feedback(rid,'owner','ended')
        def handler(req):
            product={'@type':'Product','url':item['url'],'name':item['title'],'offers':{'price':1500,'priceCurrency':'USD'}}
            return httpx.Response(200,text='<main><h1>'+item['title']+'</h1><script type="application/ld+json">'+json.dumps(product)+'</script></main>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch('offer_verifier.public_dns',AsyncMock(return_value=True)),spending_scope('queue'):
                state,_=await verify_offer(item,client)
        self.assertEqual(state,'unknown');self.assertTrue(availability_conflict(item,state))

    def test_new_owner_ended_action_after_primary_clear_is_not_lost_as_duplicate(self):
        from listing_feedback import ended_reason,clear_ended_with_primary
        import time
        self.receipts.feedback(self.rid,'owner','ended')
        clear_ended_with_primary(self.item['url'],time.time())
        self.assertFalse(ended_reason(self.item['url']))
        self.receipts.feedback(self.rid,'owner','ended')
        self.assertTrue(ended_reason(self.item['url']))
        self.assertEqual(self.receipts.conn.execute("SELECT count(*) FROM alert_feedback_history WHERE label='ended'").fetchone()[0],2)

    def test_incident_marketplace_aliases_keep_same_ended_offer_only(self):
        pairs=[('https://m.bunjang.co.kr/products/353306417','https://bunjang.co.kr/products/353306417?ref=x'),
               ('https://www.kleinanzeigen.de/s-anzeige/violin-zeta-strados/3520626319-74-2138','https://kleinanzeigen.de/s-anzeige/changed-title/3520626319-74-9999')]
        for a,b in pairs:
            self.assertEqual(offer_key(a),offer_key(b))
        self.assertNotEqual(offer_key(pairs[0][0]),offer_key('https://bunjang.co.kr.evil/products/353306417'))

    async def test_old_facebook_detail_reimport_is_not_a_new_primary_observation(self):
        import time,httpx
        card={'itemUrl':'https://www.facebook.com/marketplace/item/1655832142144071/',
              'listingTitle':'Zeta Strados 5 string electric violin',
              'description':{'text':'Purchased in 2001. Comes with a case.'},
              'listingPrice':{'amount':'3000','currency':'USD'},
              'isLive':True,'isSold':False,'isPending':False}
        from scrapers.apify_facebook import ApifyFacebookScraper,store
        from offer_verifier import verify_offer
        from listing_feedback import ended_reason
        item={'url':card['itemUrl'],'title':card['listingTitle'],'source':'search'}
        rid=self.receipts.prepare('owner',item,'oldFB');self.receipts.feedback(rid,'owner','ended')
        with store() as conn:
            conn.execute("INSERT INTO apify_jobs(id,phase,cap,created) VALUES('old','details',20000,?)",(time.time()-3600,));conn.commit()
        s=ApifyFacebookScraper();s.reset_health();s._consume('old','details',[card])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(AssertionError('no network')))) as client:
            state,_=await verify_offer(item,client)
        self.assertEqual(state,'unknown');self.assertTrue(ended_reason(item['url']))

    async def test_ended_during_photo_failure_stops_text_fallback(self):
        bot=TelegramNotifier('fixture','owner');methods=[]
        async def request(method,payload):
            methods.append(method);self.receipts.feedback(self.rid,'owner','ended');return {}
        bot._request=AsyncMock(side_effect=request)
        with patch('config.Config.SEND_PHOTOS',True):
            sent=await bot._send_violin({**self.item,'image_url':'https://example.org/violin.jpg'},'fixture',event='drop:800')
        self.assertFalse(sent);self.assertEqual(methods,['sendPhoto'])

    async def test_ended_is_removed_from_active_and_digest_without_erasing_history(self):
        from database import Database
        db=Database();item={**self.item,'id':'fixture','platform':'Reverb','title':'Zeta Jazz Fusion JV5','price':'1500 USD'}
        db.touch_active(item);db.mark_seen('fixture',item)
        self.receipts.feedback(self.rid,'owner','ended')
        try:
            self.assertEqual(db.active_listings(),[])
            self.assertTrue(db.is_seen('fixture'))
            bot=TelegramNotifier('fixture','owner');bot.send=AsyncMock(return_value=True)
            await bot.send_digest([item],[],{})
            self.assertNotIn('Zeta Jazz Fusion JV5',bot.send.await_args.args[0])
        finally:db.close()

    def test_same_offer_drop_timers_stop_without_inventing_read_receipt(self):
        drop = self.receipts.prepare('owner',self.item,'price drop','drop:900')
        self.receipts.delivered(drop,102,now=1000)
        other = self.receipts.prepare('owner',{'url':'https://reverb.com/item/124-zeta-jv5'},'another violin')
        self.receipts.delivered(other,103,now=1000)
        self.receipts.feedback(self.rid,'owner','fake')
        self.assertEqual(self.receipts.conn.execute('SELECT acknowledged_at,next_reminder,push_next_attempt FROM alert_receipts WHERE id=?',(drop,)).fetchone(),(None,None,None))
        self.assertTrue(self.receipts.is_pending(other))
        self.assertFalse(self.receipts.is_pending(drop))
        self.receipts.feedback(self.rid,'owner','clear_fake')
        self.assertEqual(self.receipts.conn.execute('SELECT acknowledged_at,next_reminder,push_next_attempt FROM alert_receipts WHERE id=?',(drop,)).fetchone(),(None,None,None))

    def test_undo_all_fake_receipts_for_the_offer_only(self):
        drop = self.receipts.prepare('owner',self.item,'price drop','drop:900')
        self.receipts.feedback(self.rid,'owner','fake')
        self.receipts.feedback(drop,'owner','fake')
        self.receipts.feedback(self.rid,'owner','clear_fake')
        self.assertFalse(hold_reason(self.item['url']))
        self.assertEqual(self.receipts.conn.execute("SELECT count(*) FROM alert_feedback WHERE label='fake'").fetchone()[0],0)

    def test_source_quarantine_survives_fake_undo(self):
        from source_review import set_hold,hold_reason as source_hold
        set_hold('reverb.com','fixture source review')
        self.receipts.feedback(self.rid,'owner','fake')
        self.receipts.feedback(self.rid,'owner','clear_fake')
        self.assertEqual(source_hold(self.item['url']),'fixture source review')

    def test_fake_rows_do_not_starve_other_pending_deliveries(self):
        from database import Database
        db = Database()
        try:
            db.enqueue({**self.item,'id':'held'})
            db.enqueue({'id':'good','url':'https://reverb.com/item/124-zeta-jv5'})
            self.receipts.feedback(self.rid,'owner','fake')
            # pending() applies the shared review policy before its limit.
            self.assertEqual([x['id'] for x in db.pending(limit=1)],['good'])
            self.assertEqual(db.conn.execute('SELECT count(*) FROM pending_alerts').fetchone()[0],2)
        finally:
            db.close()

    async def test_seen_keeps_feedback_button_and_fake_can_be_undone(self):
        bot = TelegramNotifier('fixture','owner')
        bot._post = AsyncMock(return_value=True)
        commands = TelegramCommands('fixture','owner',None,lambda:False,None,None,bot)
        async def click(action):
            await commands._dispatch({'callback_query':{'id':'fixture','data':action+':'+self.rid,
                'message':{'message_id':101,'chat':{'id':'owner'}}}})
        with patch('pushover_notifier.PushoverNotifier.cancel',new_callable=AsyncMock) as cancel:
            await click('seen')
            markup = bot._post.await_args_list[-1].args[1]['reply_markup']['inline_keyboard']
            self.assertTrue(any(b.get('callback_data')=='fake:'+self.rid for row in markup for b in row))
            await click('fake')
            markup = bot._post.await_args_list[-1].args[1]['reply_markup']['inline_keyboard']
            self.assertTrue(any('FAKE — marcat de tine' in b['text'] for row in markup for b in row))
            self.assertTrue(any(b.get('callback_data')=='clear_fake:'+self.rid for row in markup for b in row))
            await click('clear_fake')
            self.assertFalse(hold_reason(self.item['url']))
            self.assertEqual(cancel.await_count,3)

    async def test_fake_delivery_is_retained_and_other_model_is_still_sent(self):
        bot = TelegramNotifier('fixture','owner')
        bot._request = AsyncMock(return_value={'ok':True,'result':{'message_id':200}})
        self.receipts.feedback(self.rid,'owner','fake')
        self.assertFalse(await bot._send_violin(self.item,'same offer',event='drop:800'))
        bot._request.assert_not_awaited()
        other = {'url':'https://reverb.com/item/124-zeta-jv5'}
        self.assertTrue(await bot._send_violin(other,'another JV5',photo=False))
        self.assertEqual(bot._request.await_count,1)
        self.receipts.feedback(self.rid,'owner','clear_fake')
        self.assertTrue(await bot._send_violin(self.item,'freshly verified offer',event='drop:800',photo=False))

    async def test_wrong_chat_callback_cannot_mark_fake_or_cancel_push(self):
        bot = TelegramNotifier('fixture','owner');bot._post = AsyncMock()
        commands = TelegramCommands('fixture','owner',None,lambda:False,None,None,bot)
        with patch('pushover_notifier.PushoverNotifier.cancel',new_callable=AsyncMock) as cancel:
            await commands._dispatch({'callback_query':{'id':'fixture','data':'fake:'+self.rid,
                'message':{'message_id':101,'chat':{'id':'other'}}}})
            cancel.assert_not_awaited()
        bot._post.assert_not_awaited()
        self.assertFalse(hold_reason(self.item['url']))


if __name__ == '__main__':
    unittest.main()
