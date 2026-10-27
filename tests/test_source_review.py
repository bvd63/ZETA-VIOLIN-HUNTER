"""Offline source quarantine contracts, including durable queues and notifications."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import database
from alert_receipts import AlertReceipts
from discovery_queue import DiscoveryQueue
from notifier import TelegramNotifier
from price_tracker import PriceTracker
from pushover_notifier import PushoverNotifier
from source_review import set_hold, clear_hold, hold_reason, active_holds

HOST = 'classymusic.com'

def listing(host=HOST, n=1):
    return {'id':f'{host}-{n}', 'url':f'https://{host}/products/zeta-{n}',
            'title':'Zeta Jazz Fusion JV5 electric violin', 'description':'Used Zeta violin',
            'price':'1500 USD', 'platform':'Fixture', 'source':'search'}


class SourceReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(database,'DB_PATH',str(Path(self.tmp.name)/'test.db'))
        self.dbpatch.start()

    async def asyncTearDown(self):
        self.dbpatch.stop()
        self.tmp.cleanup()

    def test_domain_alias_persistence_and_no_lookalikes(self):
        set_hold('www.'+HOST,'owner review')
        self.assertEqual(hold_reason('https://'+HOST+'/products/1'),'owner review')
        self.assertEqual(hold_reason('https://WWW.'+HOST+'/products/1'),'owner review')
        for name in ('sub.'+HOST,HOST+'.evil.test','other.test'):
            self.assertFalse(hold_reason('https://'+name+'/products/1'))
        self.assertEqual(active_holds(),{HOST:'owner review'})
        clear_hold(HOST)
        self.assertFalse(hold_reason('https://www.'+HOST+'/products/1'))

    def test_held_queue_does_not_consume_capacity_or_lose_work(self):
        set_hold(HOST,'review')
        with DiscoveryQueue('search:fixture') as q:
            q.put([listing(n=i) for i in range(100)] + [listing('other.test',i) for i in range(4)])
            rows=q.claim(4)
            self.assertEqual(len(rows),4)
            self.assertTrue(all('other.test' in x['url'] for x in rows))
            count, attempts=q.conn.execute("SELECT count(*),sum(attempts) FROM discovery_queue WHERE key LIKE '%classymusic.com%'").fetchone()
            self.assertEqual((count,attempts),(100,0))
            self.assertEqual(q.conn.execute('SELECT count(*) FROM discovery_queue').fetchone()[0],104)

    async def test_deliver_holds_outbox_and_release_requires_verify(self):
        import main
        item=listing()
        set_hold(HOST,'review')
        db=database.Database(); prices=PriceTracker()
        notifier=TelegramNotifier('test','1')
        try:
            with patch.object(notifier,'send_listings',AsyncMock(side_effect=lambda rows:rows)) as send, \
                 patch('main._verify_delivery_offer',AsyncMock(return_value=('live','fixture'))) as verify:
                self.assertEqual(await main._deliver(notifier,db,prices,[item]),0)
                send.assert_not_awaited(); verify.assert_not_awaited()
                self.assertEqual(db.conn.execute('SELECT count(*) FROM pending_alerts').fetchone()[0],1)
                self.assertFalse(db.is_seen(item['id']))
                clear_hold(HOST)
                self.assertEqual(await main._deliver(notifier,db,prices,db.pending()),1)
                verify.assert_awaited_once(); send.assert_awaited_once()
                self.assertTrue(db.is_seen(item['id']))
        finally:
            db.close(); prices.close()

    async def test_notifier_blocks_new_and_drop_and_digest_hides_held(self):
        set_hold(HOST,'review')
        notifier=TelegramNotifier('test','1')
        held,other=listing(),listing('other.test')
        with patch.object(notifier,'_request',AsyncMock(return_value={'result':{'message_id':1}})) as post, \
             patch('notifier.asyncio.sleep',new_callable=AsyncMock):
            self.assertEqual(await notifier.send_listings([held,other]),[other])
            self.assertEqual(await notifier.send_price_drops([(held,{'old_price':2000,'new_price':1500})]),[])
            self.assertEqual(post.await_count,1)
        with patch.object(notifier,'send',AsyncMock(return_value=True)) as send:
            await notifier.send_digest([held,other],[held],{})
            text=send.await_args.args[0]
            self.assertNotIn(HOST,text)
            self.assertIn('other.test',text)
            self.assertIn('1 anunț(uri) active, 0 dispărute',text)

    async def test_release_rechecks_previously_verified_candidate(self):
        import main
        for verification in ('live','unknown'):
            with self.subTest(verification=verification):
                item={**listing(n=100 if verification=='live' else 101),
                      'verification':verification,'liveness':'old check'}
                set_hold(HOST,'review')
                db=database.Database();prices=PriceTracker()
                notifier=TelegramNotifier('test','1')
                try:
                    with patch.object(notifier,'send_listings',AsyncMock()) as send, \
                         patch('main._verify_delivery_offer',AsyncMock(return_value=('dead','fixture ended'))) as verify:
                        self.assertEqual(await main._deliver(notifier,db,prices,[item]),0)
                        clear_hold(HOST)
                        saved=db.pending()
                        self.assertEqual(len(saved),1)
                        self.assertNotIn('verification',saved[0]);self.assertNotIn('liveness',saved[0])
                        self.assertEqual(await main._deliver(notifier,db,prices,saved),0)
                        verify.assert_awaited_once();send.assert_not_awaited()
                        self.assertFalse(db.was_url_alerted(item['url']))
                finally:
                    db.close();prices.close()

    async def test_hold_between_failed_photo_and_fallback_blocks_message(self):
        from config import Config
        item={**listing(),'image_url':'https://example.com/photo.jpg'}
        notifier=TelegramNotifier('test','1')
        async def request(method,payload):
            self.assertEqual(method,'sendPhoto')
            set_hold(HOST,'review')
            return {}
        with patch.object(Config,'SEND_PHOTOS',True), \
             patch.object(notifier,'_request',AsyncMock(side_effect=request)) as post:
            self.assertFalse(await notifier._send_violin(item,'Zeta violin'))
        self.assertEqual(post.await_count,1)

    def test_receipt_timers_null_without_ack_and_other_alerts_progress(self):
        receipts=AlertReceipts()
        try:
            held=receipts.prepare('1',listing(),'held')
            other=receipts.prepare('1',listing('other.test'),'other')
            receipts.delivered(held,1,now=1); receipts.delivered(other,2,now=1)
            set_hold(HOST,'review')
            row=receipts.conn.execute('SELECT next_reminder,push_next_attempt,acknowledged_at FROM alert_receipts WHERE id=?',(held,)).fetchone()
            self.assertEqual(row,(None,None,None))
            self.assertFalse(receipts.is_pending(held)); self.assertTrue(receipts.is_pending(other))
            self.assertEqual(receipts.pending_count('1'),1)
            self.assertEqual([r['id'] for r in receipts.claim_due('1',now=time.time()+9999)],[other])
            self.assertEqual([r['id'] for r in receipts.claim_push_due('1',now=time.time()+9999)],[other])
            receipts.reminder_result(held,True); receipts.push_failed(held); receipts.delivered(held,1)
            clear_hold(HOST)
            row=receipts.conn.execute('SELECT next_reminder,push_next_attempt,acknowledged_at FROM alert_receipts WHERE id=?',(held,)).fetchone()
            self.assertEqual(row,(None,None,None))
        finally:
            receipts.close()

    async def test_active_pushover_held_is_cancelled_without_message_or_ack(self):
        receipts=AlertReceipts()
        receipt=receipts.prepare('1',listing(),'held')
        receipts.delivered(receipt,1); receipts.push_starting(receipt); receipts.push_sent(receipt,'x'*30)
        set_hold(HOST,'review')
        notifier=PushoverNotifier()
        try:
            with patch.object(notifier,'is_configured',return_value=True), \
                 patch.object(notifier,'cancel',AsyncMock(return_value=True)) as cancel, \
                 patch.object(notifier,'request',AsyncMock()) as request:
                await notifier.tick('1')
            cancel.assert_awaited_once_with(receipt);request.assert_not_awaited()
            ack,closed=receipts.conn.execute('SELECT acknowledged_at,push_closed FROM alert_receipts WHERE id=?',(receipt,)).fetchone()
            self.assertIsNone(ack); self.assertIsNotNone(closed)
        finally:
            receipts.close()

    async def test_hold_racing_pushover_post_cancels_without_ack(self):
        receipts=AlertReceipts(); rid=receipts.prepare('1',listing(),'held')
        receipts.delivered(rid,1); receipts.push_immediately(rid)
        notifier=PushoverNotifier()
        async def response(method,path,payload=None):
            self.assertEqual(path,'messages.json')
            set_hold(HOST,'review')
            return {'receipt':'x'*30}
        try:
            with patch.object(notifier,'is_configured',return_value=True), \
                 patch.object(notifier,'request',AsyncMock(side_effect=response)), \
                 patch.object(notifier,'cancel',AsyncMock(return_value=True)) as cancel:
                await notifier.tick('1')
            cancel.assert_awaited_once_with(rid)
            ack,closed,next_attempt=receipts.conn.execute('SELECT acknowledged_at,push_closed,push_next_attempt FROM alert_receipts WHERE id=?',(rid,)).fetchone()
            self.assertIsNone(ack);self.assertIsNotNone(closed);self.assertIsNone(next_attempt)
        finally:
            receipts.close()

    async def test_held_pending_and_price_drop_skip_network(self):
        import main
        item=listing(); set_hold(HOST,'review')
        db=database.Database()
        db.enqueue(item);db.enqueue_price_drop(item,{'old_price':2000,'new_price':1500})
        db.close()
        notifier=TelegramNotifier('test','1')
        with patch('main.TelegramNotifier',return_value=notifier), \
             patch.object(notifier,'send_reminders',AsyncMock()), \
             patch.object(notifier,'send_price_drops',AsyncMock()) as drops, \
             patch('main._verify_delivery_offer',AsyncMock()) as verify:
            await main._notification_tick()
        verify.assert_not_awaited();drops.assert_not_awaited()
        db=database.Database()
        try:
            self.assertEqual(db.conn.execute('SELECT count(*) FROM pending_alerts').fetchone()[0],2)
            self.assertEqual(db.pending(),[]);self.assertEqual(db.pending_price_drops(),[])
        finally:
            db.close()

    async def test_held_outbox_cannot_starve_unheld_new_or_price_drop(self):
        import main
        set_hold(HOST,'review')
        db=database.Database()
        info={'old_price':2000,'new_price':1500}
        for n in range(35):
            db.enqueue(listing(n=n))
            db.enqueue_price_drop(listing(n=n),info)
        other=listing('other.test')
        db.enqueue(other);db.enqueue_price_drop(other,info);db.close()
        notifier=TelegramNotifier('test','1')
        with patch('main.TelegramNotifier',return_value=notifier), \
             patch.object(notifier,'send_reminders',AsyncMock()), \
             patch('main._verify_delivery_offer',AsyncMock(return_value=('live','fixture'))) as verify, \
             patch('main._deliver',AsyncMock(return_value=1)) as deliver, \
             patch.object(notifier,'send_price_drops',AsyncMock(side_effect=lambda rows:rows)) as drops:
            await main._notification_tick()
        self.assertEqual(verify.await_count,2)
        self.assertEqual(deliver.await_count,1)
        self.assertEqual(drops.await_count,1)
        db=database.Database()
        try:
            self.assertEqual(db.conn.execute("SELECT count(*) FROM pending_alerts WHERE url LIKE '%classymusic.com%'").fetchone()[0],70)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
