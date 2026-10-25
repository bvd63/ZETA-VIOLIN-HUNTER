"""Offline delivery/acknowledgement/restart regressions. No real Telegram calls."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

TEMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(TEMP.name) / "bot.db")
os.environ["PUSHOVER_APP_TOKEN"] = ""
os.environ["PUSHOVER_USER_KEY"] = ""
from config import Config
import database
database.DB_PATH = os.environ["DB_PATH"]
from alert_receipts import AlertReceipts
from notifier import TelegramNotifier
from telegram_commands import TelegramCommands


class NotificationsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db = database.Database()
        for table in ("seen_listings", "alerts", "delivered_urls", "pending_alerts", "listing_activity"):
            db.conn.execute(f"DELETE FROM {table}")
        db.conn.commit()
        db.close()
        self.receipts = AlertReceipts()
        self.receipts.conn.execute("DELETE FROM alert_receipts")
        self.receipts.conn.commit()
        self.bot = TelegramNotifier("fake", "-123")
        self.listing = {"id": "violin", "title": "Zeta Jazz Fusion JV44 violin",
                        "price": "2000 USD", "url": "https://reverb.com/item/123-zeta",
                        "platform": "Reverb", "condition": "Used"}

    def tearDown(self):
        self.receipts.close()

    def make_receipt(self):
        rid = self.receipts.prepare("-123", self.listing, "🎻 Zeta violin")
        self.receipts.delivered(rid, 44, now=1000)
        return rid

    async def test_violin_is_audible_with_button_and_durable_receipt(self):
        self.bot._request = AsyncMock(return_value={"ok": True, "result": {"message_id": 45}})
        delivered = await self.bot.send_listings([self.listing])
        self.assertEqual(delivered, [self.listing])
        method, payload = self.bot._request.await_args.args
        self.assertEqual(method, "sendMessage")
        self.assertFalse(payload["disable_notification"])
        self.assertIn("VIOARĂ ZETA GĂSITĂ", payload["text"])
        self.assertNotIn("/10", payload["text"])
        self.assertIn("seen:", payload["reply_markup"]["inline_keyboard"][-1][0]["callback_data"])
        self.assertEqual(self.receipts.pending_count("-123"), 1)

    async def test_owner_priority_uses_metadata_without_claiming_signature_authenticity(self):
        self.bot._request = AsyncMock(return_value={"ok": True, "result": {"message_id": 46}})
        item = {**self.listing, "model": "JLP5", "finish": "JLP Burst"}
        self.assertEqual(await self.bot.send_listings([item]), [item])
        text = self.bot._request.await_args.args[1]["text"]
        self.assertIn("PRIORITATEA TA", text)
        self.assertIn("Jean-Luc Ponty / JLP", text)
        self.assertIn("burst", text)
        self.assertNotIn("Jean-Luc Ponty signature", text)

    async def test_strados_sunburst_is_delivered_without_owner_priority(self):
        self.bot._request = AsyncMock(return_value={"ok":True,"result":{"message_id":47}})
        item = {**self.listing,"title":"Zeta Strados violin","finish":"Sunburst"}
        self.assertEqual(await self.bot.send_listings([item]),[item])
        payload=self.bot._request.await_args.args[1]
        self.assertNotIn("PRIORITATEA TA",payload["text"])
        self.assertIn("interes redus",payload["text"])
        self.assertFalse(payload["disable_notification"])

    async def test_jazz_jv5_priority_is_audible_with_any_year_or_no_year(self):
        self.bot._request=AsyncMock(return_value={'ok':True,'result':{'message_id':48}})
        for year in ('1993','2001',''):
            item={**self.listing,'title':f'Zeta jazz fusion 5-string violon (JV5) {year} - NOIRE',
                  'model':'jazz fusion 5-string violon (JV5)','finish':'NOIRE','year':year,
                  'url':'https://reverb.com/item/103084523-zeta-jazz-fusion-5-string-violon-jv5-1993-noire'}
            self.assertEqual(await self.bot.send_listings([item]),[item])
            payload=self.bot._request.await_args.args[1]
            self.assertIn('PRIORITATEA TA',payload['text'])
            self.assertIn('Jazz / JV',payload['text'])
            self.assertNotRegex(payload['text'],r'\b\d{1,2}/10\b')
            self.assertFalse(payload['disable_notification'])

    async def test_priority_keeps_all_owner_aliases_without_year_keywords(self):
        from keywords import preference_matches, WEB_KEYWORDS, ADDITIONAL_WEB_KEYWORDS
        for alias in ('Jazz Fusion','JV5','JV45','JV44','JLP','JLP5','Jean Luc Ponty','Jean-Luc Ponty'):
            with self.subTest(alias=alias):
                self.assertTrue(preference_matches({'title':'Zeta '+alias}))
        self.assertFalse(preference_matches({'title':'Zeta violin 1993'}))
        self.assertFalse(any('1993' in q for q in WEB_KEYWORDS+ADDITIONAL_WEB_KEYWORDS))

    async def test_photo_failure_falls_back_to_text_with_same_confirmation(self):
        self.bot._request = AsyncMock(side_effect=[{}, {"ok": True, "result": {"message_id": 45}}])
        listing = {**self.listing, "image_url": "https://example.com/violin.jpg"}
        with patch.object(Config, "SEND_PHOTOS", True):
            self.assertTrue(await self.bot._send_violin(listing, "🎻 Violin"))
        calls = self.bot._request.await_args_list
        self.assertEqual([call.args[0] for call in calls], ["sendPhoto", "sendMessage"])
        self.assertEqual(calls[0].args[1]["reply_markup"], calls[1].args[1]["reply_markup"])

    async def test_failed_initial_delivery_never_starts_reminders(self):
        self.bot._request = AsyncMock(return_value={})
        self.assertEqual(await self.bot.send_listings([self.listing]), [])
        self.assertEqual(self.receipts.pending_count("-123"), 0)
        self.assertEqual(self.receipts.claim_due("-123", now=99999999999), [])

    async def test_api_ok_false_is_failure_even_with_http_200(self):
        async def post(*args, **kwargs):
            return httpx.Response(200, json={"ok": False, "error_code": 400})
        with patch("httpx.AsyncClient.post", new=post):
            self.assertFalse(await self.bot.send("fixture"))

    async def test_http_timeout_is_not_logged_with_token(self):
        error = httpx.ReadTimeout("secret-url-botfake")
        with patch("httpx.AsyncClient.post", AsyncMock(side_effect=error)), \
                self.assertLogs("notifier", level="WARNING") as logs:
            self.assertFalse(await self.bot.send("fixture"))
        self.assertNotIn("secret-url", str(logs.output))

    async def test_routine_report_is_silent_and_names_broken_sources(self):
        self.bot._post = AsyncMock(return_value=True)
        with patch.object(Config, "SEND_NO_CHANGES", True):
            await self.bot.send_no_changes(16, 3, 1, error_details=["Google: API 403"], pending=2)
        payload = self.bot._post.await_args.args[1]
        self.assertTrue(payload["disable_notification"])
        self.assertIn("Google: API 403", payload["text"])
        self.assertIn("așteaptă livrarea", payload["text"])
        self.assertNotIn("Nu au fost găsite", payload["text"])

    async def test_reminder_survives_restart_and_repeats_until_ack(self):
        rid = self.make_receipt()
        second = AlertReceipts()
        try:
            self.assertEqual(second.claim_due("-123", now=1599), [])
            self.assertEqual(second.claim_due("-123", now=1600)[0]["id"], rid)
            self.assertEqual(self.receipts.claim_due("-123", now=1600), [])
            second.reminder_result(rid, True, now=1600)
            self.assertEqual(second.claim_due("-123", now=3399), [])
            self.assertEqual(second.claim_due("-123", now=3400)[0]["id"], rid)
            self.assertTrue(second.acknowledge(rid, "-123"))
            second.reminder_result(rid, True, now=3400)
            self.assertEqual(second.claim_due("-123", now=99999999), [])
        finally:
            second.close()

    async def test_failed_reminder_retries_and_does_not_ack(self):
        rid = self.make_receipt()
        self.receipts.claim_due("-123", now=1600)
        self.receipts.reminder_result(rid, False, now=1600)
        self.assertEqual(self.receipts.claim_due("-123", now=1719), [])
        self.assertEqual(self.receipts.claim_due("-123", now=1720)[0]["reminder_count"], 0)

    async def test_reminder_message_has_sound_and_same_confirmation(self):
        rid = self.make_receipt()
        self.bot._post = AsyncMock(return_value=True)
        await self.bot.send_reminders()
        payload = self.bot._post.await_args.args[1]
        self.assertFalse(payload["disable_notification"])
        self.assertIn("NU AI CONFIRMAT", payload["text"])
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][-1][0]["callback_data"], "seen:" + rid)

    async def test_wrong_chat_cannot_stop_reminders(self):
        rid = self.make_receipt()
        self.assertFalse(self.receipts.acknowledge(rid, "999"))
        self.assertTrue(self.receipts.is_pending(rid))
        self.assertEqual(self.receipts.claim_due("999", now=2000), [])

    async def test_ack_during_initial_delivery_does_not_get_reset(self):
        rid = self.receipts.prepare("-123", self.listing, "fixture")
        self.receipts.acknowledge(rid, "-123")
        self.receipts.delivered(rid, 45, now=1000)
        self.assertEqual(self.receipts.claim_due("-123", now=99999), [])

    async def test_url_alias_and_retries_do_not_reset_ack(self):
        rid = self.make_receipt()
        self.receipts.acknowledge(rid, "-123")
        other = self.receipts.prepare("-123", {**self.listing, "url": "https://reverb.com/fr/item/123?utm_source=x"}, "updated")
        self.assertEqual(rid, other)
        self.receipts.delivered(rid, 46, now=2000)
        self.assertEqual(self.receipts.pending_count("-123"), 0)

    async def test_new_auction_and_price_drop_have_own_confirmations(self):
        rid = self.make_receipt()
        auction = self.receipts.prepare("-123", {**self.listing, "auction_end": "2026-11-01"}, "auction")
        drop = self.receipts.prepare("-123", self.listing, "drop", "drop:1500")
        self.assertEqual(len({rid, auction, drop}), 3)

    async def test_callback_dispatch_acknowledges_only_its_receipt(self):
        rid = self.make_receipt()
        commands = TelegramCommands("fake", "-123", None, lambda: False, None, None, self.bot)
        self.bot._post = AsyncMock(return_value=True)
        callback = {"id": "callback", "data": "seen:" + rid,
                    "message": {"message_id": 44, "chat": {"id": -123}}}
        await commands._dispatch({"callback_query": {**callback, "message": {"chat": {"id": 999}}}})
        self.assertTrue(self.receipts.is_pending(rid))
        await commands._dispatch({"callback_query": callback})
        self.assertFalse(self.receipts.is_pending(rid))
        self.assertEqual(self.bot._post.await_args_list[0].args[0], "answerCallbackQuery")

    async def test_old_delivery_history_is_not_imported_as_unread(self):
        db = database.Database()
        try:
            db.record_alert(self.listing, 2000)
            self.assertEqual(self.receipts.pending_count("-123"), 0)
        finally:
            db.close()

    async def test_pending_retry_does_not_call_any_search_provider(self):
        import main
        db = database.Database()
        db.enqueue(self.listing)
        db.close()
        mock = AsyncMock(return_value=[self.listing])
        with patch.object(TelegramNotifier, "send_listings", mock), \
                patch.object(TelegramNotifier, "send_reminders", AsyncMock()), \
                patch("main.verify_offer", AsyncMock(return_value=("live", "fixture"))), \
                patch("main.build_scrapers", side_effect=AssertionError("No paid search allowed")):
            await main.notification_tick()
        mock.assert_awaited_once()
        db = database.Database()
        try:
            self.assertEqual(db.pending(), [])
            self.assertTrue(db.was_url_alerted(self.listing["url"]))
        finally:
            db.close()

    async def test_retry_rechecks_identity_after_primary_details_change(self):
        import main
        db = database.Database()
        db.enqueue(self.listing)
        db.close()
        async def verify(item, client):
            item.update(title='Case for Zeta violin', description='Case only, instrument not included.')
            return 'live', 'primary object is a case'
        send = AsyncMock()
        with patch.object(TelegramNotifier, 'send_listings', send), \
                patch.object(TelegramNotifier, 'send_reminders', AsyncMock()), \
                patch('main.verify_offer', verify):
            await main.notification_tick()
        send.assert_not_awaited()

    async def test_unknown_snippet_only_candidate_stays_pending_without_false_alert(self):
        import main
        item = {**self.listing, 'title':'Stage lights', 'description':'Zeta electric violin with original case.',
                'source':'search', 'url':'https://govdeals.com/en/asset/2328/7913'}
        db = database.Database()
        db.enqueue(item)
        db.close()
        send = AsyncMock()
        with patch.object(TelegramNotifier, 'send_listings', send), \
                patch.object(TelegramNotifier, 'send_reminders', AsyncMock()), \
                patch('main.verify_offer', AsyncMock(return_value=('unknown','access unavailable'))):
            await main.notification_tick()
        send.assert_not_awaited()
        db = database.Database()
        try:
            self.assertEqual([x['url'] for x in db.pending()], [item['url']])
            self.assertFalse(db.is_seen(item['id']))
        finally:
            db.close()

    async def test_full_cycle_pending_recovery_uses_same_primary_identity_policy(self):
        import main
        item = {**self.listing, 'title':'Vintage instrument', 'description':'Zeta electric violin with case.',
                'source':'search', 'url':'https://dealer.example/item/uncertain'}
        db = database.Database()
        db.enqueue(item)
        db.close()
        send = AsyncMock()
        with patch.object(TelegramNotifier, 'send_listings', send), \
                patch.object(TelegramNotifier, 'send_no_changes', AsyncMock()), \
                patch('main.verify_offer', AsyncMock(return_value=('unknown','access unavailable'))), \
                patch('main.build_scrapers', return_value=[]), \
                patch('main.refresh_rates', AsyncMock()), patch('main.detect_egress', AsyncMock()), \
                patch('main._watchdog', AsyncMock()), patch('main.resource_tick', AsyncMock()), \
                patch('main._maybe_weekly_digest', AsyncMock()):
            await main.run_search_cycle()
        send.assert_not_awaited()
        db = database.Database()
        try:
            self.assertEqual([x['url'] for x in db.pending()], [item['url']])
            self.assertFalse(db.is_seen(item['id']))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
