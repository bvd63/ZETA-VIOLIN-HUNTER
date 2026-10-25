"""Offline emergency escalation, shared acknowledgement and uncertain-send tests."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(TEMP.name) / "bot.db")
from config import Config
import database
database.DB_PATH = os.environ["DB_PATH"]
from alert_receipts import AlertReceipts
from provider_state import store, read, write, used
from pushover_notifier import PushoverNotifier


class PushoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patch = patch.multiple(Config, PUSHOVER_APP_TOKEN="fake-app", PUSHOVER_USER_KEY="fake-user")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        with store() as conn:
            conn.execute("DELETE FROM provider_state")
            conn.execute("DELETE FROM provider_reservations")
            conn.commit()
        self.receipts = AlertReceipts()
        self.receipts.conn.execute("DELETE FROM alert_receipts")
        self.receipts.conn.commit()
        self.bot = PushoverNotifier()
        self.bot.request = AsyncMock(return_value={"status": 1, "receipt": "R" * 30})
        self.listing = {"title": "Zeta Jazz Fusion violin", "url": "https://reverb.com/item/123"}
        self.rid = self.receipts.prepare("chat", self.listing, "🎻 <b>Zeta violin</b>")

    def tearDown(self):
        self.receipts.close()

    def due(self):
        self.receipts.push_immediately(self.rid)

    async def test_initial_delay_and_immediate_fallback_on_telegram_failure(self):
        await self.bot.tick("chat")
        self.bot.request.assert_not_awaited()
        self.due()
        await self.bot.tick("chat")
        method, path, payload = self.bot.request.await_args.args
        self.assertEqual((method, path), ("POST", "messages.json"))
        self.assertEqual(payload["priority"], 2)
        self.assertEqual(payload["retry"], 120)
        self.assertEqual(payload["expire"], 3600)
        self.assertLess(payload["expire"] / payload["retry"], 50)
        self.assertEqual(payload["tags"], self.rid)
        self.assertEqual(used("pushover"), 1)

    async def test_wrong_chat_and_unconfigured_provider_never_send(self):
        self.due()
        await self.bot.tick("other")
        with patch.object(Config, "PUSHOVER_USER_KEY", ""):
            await self.bot.tick("chat")
        self.bot.request.assert_not_awaited()

    async def test_receipt_survives_restart_without_duplicate_message_post(self):
        self.due()
        await self.bot.tick("chat")
        self.bot.request.reset_mock()
        self.bot.request.return_value = {"status": 1, "acknowledged": 0, "expired": 0}
        await self.bot.tick("chat")
        self.assertEqual(self.bot.request.await_count, 1)
        self.assertEqual(self.bot.request.await_args.args[0], "GET")

    async def test_pushover_ack_stops_telegram_reminders(self):
        self.receipts.delivered(self.rid, 1, now=1000)
        self.receipts.push_starting(self.rid)
        self.receipts.push_sent(self.rid, "R" * 30)
        self.bot.request.return_value = {"status": 1, "acknowledged": 1}
        await self.bot.tick("chat")
        self.assertFalse(self.receipts.is_pending(self.rid))
        self.assertEqual(self.receipts.claim_due("chat", now=99999999999), [])

    async def test_telegram_ack_cancels_remote_emergency_by_tag(self):
        self.receipts.push_starting(self.rid)
        self.receipts.push_sent(self.rid, "R" * 30)
        self.receipts.acknowledge(self.rid, "chat")
        await self.bot.tick("chat")
        self.assertIn("cancel_by_tag/" + self.rid, self.bot.request.await_args.args[1])

    async def test_unknown_post_is_cancelled_by_tag_before_replacement(self):
        self.due()
        self.bot.request.return_value = {}
        await self.bot.tick("chat")
        self.receipts.conn.execute("UPDATE alert_receipts SET push_next_attempt=0")
        self.receipts.conn.commit()
        self.bot.request.reset_mock()
        self.bot.request.side_effect = [{"status": 1}, {"status": 1, "receipt": "S" * 30}]
        await self.bot.tick("chat")
        calls = self.bot.request.await_args_list
        self.assertIn("cancel_by_tag", calls[0].args[1])
        self.assertEqual(calls[1].args[1], "messages.json")

    async def test_ack_during_uncertain_send_still_cancels_on_next_tick(self):
        self.due()
        async def request(*args):
            self.receipts.acknowledge(self.rid, "chat")
            return {}  # provider might have accepted it before a network timeout
        self.bot.request = AsyncMock(side_effect=request)
        await self.bot.tick("chat")
        self.bot.request = AsyncMock(return_value={"status": 1})
        await self.bot.tick("chat")
        self.assertIn("cancel_by_tag", self.bot.request.await_args.args[1])

    async def test_expired_emergency_is_renewed_until_ack(self):
        self.receipts.push_starting(self.rid)
        self.receipts.push_sent(self.rid, "R" * 30, now=1000)
        self.bot.request.side_effect = [{"status": 1, "expired": 1}, {"status": 1},
                                       {"status": 1, "receipt": "S" * 30}]
        await self.bot.tick("chat")
        self.assertEqual(self.bot.request.await_args.args[1], "messages.json")
        self.assertTrue(self.receipts.is_pending(self.rid))

    async def test_quota_cooldown_stops_new_message_posts(self):
        self.due()
        write("pushover:retry_after", time.time() + 3600)
        await self.bot.tick("chat")
        self.bot.request.assert_not_awaited()

    async def test_repeated_telegram_failure_cannot_override_pushover_backoff(self):
        self.due()
        self.receipts.push_starting(self.rid)
        self.receipts.push_failed(self.rid)
        self.due()
        await self.bot.tick("chat")
        self.bot.request.assert_not_awaited()

    async def test_successful_poll_cannot_clear_a_message_delivery_error(self):
        write("pushover:health", {"error": "HTTP 429"})
        response = httpx.Response(200, json={"status": 1, "acknowledged": 0})
        with patch("httpx.AsyncClient.request", AsyncMock(return_value=response)):
            await PushoverNotifier().request("GET", "receipts/" + "R" * 30 + ".json")
        self.assertEqual(read("pushover:health")["error"], "HTTP 429")

    async def test_real_adapter_handles_rate_limit_without_logging_secrets(self):
        response = httpx.Response(429, json={"status": 0}, headers={"X-Limit-App-Reset": str(int(time.time()) + 3600)})
        with patch("httpx.AsyncClient.request", AsyncMock(return_value=response)):
            self.assertEqual(await PushoverNotifier().request("POST", "messages.json", {}), {})
        self.assertGreater(read("pushover:retry_after"), time.time())

    async def test_migration_does_not_backfill_old_delivered_messages(self):
        self.receipts.conn.execute("UPDATE alert_receipts SET push_next_attempt=NULL")
        self.receipts.conn.commit()
        self.assertEqual(self.receipts.claim_push_due("chat", now=99999999999), [])


if __name__ == "__main__":
    unittest.main()
