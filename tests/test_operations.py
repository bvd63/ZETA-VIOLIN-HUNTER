"""Offline recovery, schedule, HTTP protection and final-offer regressions."""
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

TEMP = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = str(Path(TEMP.name) / 'operations.db')
from config import Config
import database
database.DB_PATH = os.environ['DB_PATH']
from database import Database
from operations import create_backup, verify_restore, readiness
from offer_verifier import inspect_page
from alert_receipts import AlertReceipts
import main


class RecoveryTests(unittest.TestCase):
    def test_online_backup_contains_uncheckpointed_wal_and_restores_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'live.db'
            conn = sqlite3.connect(path)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute('CREATE TABLE pending_alerts (url TEXT)')
            conn.execute("INSERT INTO pending_alerts VALUES ('saved-violin')")
            conn.commit()
            result = create_backup(path)
            archive = path.parent / 'backups' / result['file']
            self.assertEqual(verify_restore(archive), {'pending_alerts': 1})
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertEqual(conn.execute('SELECT url FROM pending_alerts').fetchone()[0], 'saved-violin')
            conn.close()
            broken = path.parent / 'broken.db.gz'
            broken.write_bytes(b'not a gzip database')
            with self.assertRaises(Exception):
                verify_restore(broken)
            self.assertTrue(path.exists())

    def test_readiness_handles_romania_dst_and_missed_schedule(self):
        with patch.object(Config, 'SEARCH_HOURS', '10,22'), patch.object(Config, 'SEARCH_TIMEZONE', 'Europe/Bucharest'):
            for day, hour in ((24, 7), (25, 8), (26, 8)):
                last = datetime(2026, 10, day, hour)
                self.assertEqual(readiness(last, datetime(2026, 10, day, hour+2, tzinfo=timezone.utc))['status'], 'ready')
                old = last.replace(hour=hour-12) if hour >= 12 else datetime(2026,10,day-1,hour+12)
                self.assertEqual(readiness(old, datetime(2026,10,day,hour+2,tzinfo=timezone.utc))['status'], 'search_stale')
                self.assertEqual(readiness(old, datetime(2026,10,day,hour,30,tzinfo=timezone.utc))['status'], 'ready')

    def test_search_and_status_fail_closed_without_token(self):
        request = SimpleNamespace(headers={}, query={})
        with patch.object(Config, 'SEARCH_TOKEN', ''):
            self.assertFalse(main._authorized(request))
        with patch.object(Config, 'SEARCH_TOKEN', 'fixture-secret'):
            self.assertFalse(main._authorized(SimpleNamespace(headers={},query={'token':'fixture-secret'})))
            self.assertTrue(main._authorized(SimpleNamespace(headers={'X-Search-Token':'fixture-secret'})))

    def test_challenge_and_247_never_replace_violin_identity(self):
        for code, html in ((247, '<title>Gumtree</title>'), (200, '<title>Just a moment...</title>')):
            item = {'title':'Zeta Jazz Fusion JV5', 'url':'https://gumtree.com/p/violin/x/123'}
            state, _ = inspect_page(item, html, code, item['url'])
            self.assertEqual(state, 'unknown')
            self.assertEqual(item['title'], 'Zeta Jazz Fusion JV5')

    def test_feedback_is_durable_chat_scoped_and_stops_only_its_alarm(self):
        receipts = AlertReceipts()
        try:
            item = {'url':'https://reverb.com/item/999'}
            rid = receipts.prepare('owner', item, 'fixture')
            other = receipts.prepare('owner', {**item,'url':'https://reverb.com/item/998'}, 'fixture')
            self.assertFalse(receipts.feedback(rid, 'stranger', 'not_violin'))
            self.assertTrue(receipts.is_pending(rid))
            self.assertTrue(receipts.feedback(rid, 'owner', 'not_violin'))
            self.assertFalse(receipts.is_pending(rid))
            self.assertTrue(receipts.is_pending(other))
            self.assertEqual(receipts.conn.execute('SELECT label FROM alert_feedback WHERE receipt_id=?',(rid,)).fetchone()[0], 'not_violin')
        finally:
            receipts.close()


class FinalDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_offers_checked_before_send_and_dead_never_marked_seen(self):
        db = Database()
        item = {'id':'final-check', 'url':'https://reverb.com/item/555-zeta',
                'title':'Zeta Jazz Fusion JV5 electric violin', 'price':'2000 USD', 'platform':'Reverb'}
        send = AsyncMock(return_value=[])
        prices = SimpleNamespace(record_listing=lambda _: {})
        try:
            with patch('main.verify_offer', AsyncMock(return_value=('dead','ended'))):
                self.assertEqual(await main._deliver(SimpleNamespace(send_listings=send),db,prices,[dict(item)]),0)
            send.assert_not_awaited()
            self.assertFalse(db.was_url_alerted(item['url']))
            self.assertEqual(db.pending(), [])
            async def verified(items):
                return items
            send = AsyncMock(side_effect=verified)
            with patch('main.verify_offer', AsyncMock(return_value=('unknown','access blocked'))) as check:
                self.assertEqual(await main._deliver(SimpleNamespace(send_listings=send),db,prices,[dict(item)]),1)
            check.assert_awaited_once()
            self.assertTrue(db.was_url_alerted(item['url']))
        finally:
            db.close()

    async def test_failed_verification_stays_durable_for_retry(self):
        db = Database()
        item = {'id':'retry-check', 'url':'https://reverb.com/item/556-zeta',
                'title':'Zeta Jazz Fusion JV5 electric violin', 'price':'2000 USD'}
        try:
            with patch('main.verify_offer', AsyncMock(side_effect=RuntimeError('fixture'))):
                self.assertEqual(await main._deliver(SimpleNamespace(send_listings=AsyncMock()),db,
                    SimpleNamespace(record_listing=lambda _: {}),[item]),0)
            self.assertIn(item['url'],[x['url'] for x in db.pending()])
            self.assertFalse(db.was_url_alerted(item['url']))
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
