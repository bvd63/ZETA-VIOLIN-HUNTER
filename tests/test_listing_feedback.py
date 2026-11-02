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
