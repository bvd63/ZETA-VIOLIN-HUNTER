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

    def test_manual_brave_allowance_preserves_regular_day_and_month_ceiling(self):
        from search_budget import reserve_request
        conn = database.connect()
        conn.execute('CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY,last_run TEXT)')
        conn.execute("DELETE FROM scraper_runs WHERE scraper LIKE 'brave%' ")
        conn.commit(); conn.close()
        for _ in range(16):
            self.assertTrue(reserve_request('brave', 48, manual=True))
        self.assertFalse(reserve_request('brave', 48, manual=True))
        for _ in range(32):
            self.assertTrue(reserve_request('brave', 48))
        self.assertFalse(reserve_request('brave', 48))
        self.assertFalse(reserve_request('brave', 48, manual=True))

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
    async def test_fresh_endpoint_is_authenticated_and_explicit(self):
        from aiohttp.test_utils import make_mocked_request
        with patch.object(Config, 'SEARCH_TOKEN', 'fixture'), patch('main.run_search_cycle',AsyncMock(return_value=0)) as run:
            response = await main.handle_search(make_mocked_request('POST','/search?fresh=1'))
            self.assertEqual(response.status,401)
            run.assert_not_awaited()
            response = await main.handle_search(make_mocked_request('POST','/search?fresh=1',headers={'X-Search-Token':'fixture'}))
            self.assertEqual(response.status,200)
            run.assert_awaited_once_with(force_discovery=True)

    async def test_manual_search_preserves_scheduled_guard_and_reports_actual_calls(self):
        from provider_state import write, read, store
        from scrapers.search_reserves import TavilyScraper
        import time,httpx
        with store() as conn:
            conn.execute('DELETE FROM provider_reservations'); conn.execute('DELETE FROM provider_state'); conn.commit()
        previous=time.time(); write('tavily:last_run',previous)
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'results':[]})
        scraper=TavilyScraper(); scraper.manual_fresh=True
        original=scraper.make_client
        with patch.multiple(Config,TAVILY_API_KEY='fixture',TAVILY_QUERIES_PER_RUN=2,TAVILY_EXTRA_QUERIES_PER_RUN=0), \
             patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            await scraper.search()
        self.assertEqual(len(calls),2)
        self.assertEqual(scraper.discovery_queries_attempted,2)
        self.assertEqual(read('tavily:last_run'),previous)
        self.assertEqual(read('tavily:cursor'),2)

    async def test_google_permission_failure_is_not_presented_as_quota(self):
        from scrapers.google import GoogleScraper
        import httpx
        scraper=GoogleScraper('fixture-key','fixture-cx');scraper.manual_fresh=True
        original=scraper.make_client
        def handler(request):
            return httpx.Response(403,json={'error':{'message':'This project does not have the access to Custom Search JSON API.'}})
        with patch.object(scraper,'make_client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)), \
             patch('scrapers.google.reserve_request',return_value=True):
            await scraper.search()
        self.assertIn('API_PROJECT_NOT_ELIGIBLE',scraper.health_error())
        self.assertEqual(scraper.discovery_queries_attempted,1)
        self.assertIn('nu este epuizare',main.source_problem(scraper.name,scraper.health_error()))

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
            pending = {x['url']:x for x in db.pending()}
            self.assertIn(item['url'],pending)
            self.assertTrue(pending[item['url']]['_direct_item_candidate'])
            self.assertFalse(db.was_url_alerted(item['url']))
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
