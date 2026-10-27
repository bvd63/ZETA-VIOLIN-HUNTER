"""Emergency escalation with a shared acknowledgement, no public webhook needed."""

import html
import logging
import re
import time
import httpx
from alert_receipts import AlertReceipts
from config import Config
from provider_state import read, write, reserve

log = logging.getLogger(__name__)
API = "https://api.pushover.net/1/"
RECEIPT = re.compile(r"^[A-Za-z0-9]{30}$")


class PushoverNotifier:
    def is_configured(self) -> bool:
        return bool(Config.PUSHOVER_APP_TOKEN and Config.PUSHOVER_USER_KEY)

    async def request(self, method: str, path: str, payload: dict = None) -> dict:
        if not self.is_configured():
            return {}
        payload = {"token": Config.PUSHOVER_APP_TOKEN, **(payload or {})}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.request(method, API + path,
                    **({"params": payload} if method == "GET" else {"data": payload}))
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid Pushover response")
            if response.status_code != 200 or data.get("status") != 1:
                write("pushover:health", {"error": f"HTTP {response.status_code}", "checked": time.time()})
                if response.status_code == 429:
                    reset = response.headers.get("X-Limit-App-Reset", "")
                    write("pushover:retry_after", float(reset) if reset.isdigit() else time.time() + 3600)
                log.warning("Pushover request failed (HTTP %s)", response.status_code)
                return {}
            # A successful receipt poll/cancellation must not hide a failed
            # message send or an exhausted message allowance.
            if path in ("messages.json", "users/validate.json"):
                write("pushover:health", {"error": "", "checked": time.time()})
            return data
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("Pushover request failed (%s)", type(exc).__name__)
            write("pushover:health", {"error": type(exc).__name__, "checked": time.time()})
            return {}

    async def cancel(self, alert_id: str) -> bool:
        # Tags also cancel alarms whose POST response was lost before we could
        # save a receipt. Never include a user key/token in diagnostic logs.
        if not re.fullmatch(r"[a-f0-9]{24}", alert_id):
            return False
        return bool(await self.request("POST", f"receipts/cancel_by_tag/{alert_id}.json"))

    async def validate(self) -> bool:
        """Validate credentials/device registration without sending an alert."""
        result = await self.request("POST", "users/validate.json", {"user": Config.PUSHOVER_USER_KEY})
        if result and not result.get("devices"):
            write("pushover:health", {"error": "no registered notification device", "checked": time.time()})
            return False
        return bool(result)

    async def tick(self, chat_id: str) -> None:
        if not self.is_configured():
            return
        receipts = AlertReceipts()
        try:
            for alert in receipts.push_active(chat_id):
                if alert["acknowledged_at"] is not None or alert.get('technically_closed'):
                    if await self.cancel(alert["id"]):
                        receipts.push_finished(alert["id"])
                    continue
                if not alert["receipt"]:
                    continue  # uncertain POST; cancel-by-tag before the retry
                if not RECEIPT.fullmatch(alert["receipt"]):
                    log.warning("Invalid stored Pushover receipt")
                    continue
                result = await self.request("GET", f"receipts/{alert['receipt']}.json")
                if result.get("acknowledged") == 1:
                    receipts.acknowledge(alert["id"], chat_id)
                    receipts.push_finished(alert["id"])
                elif result.get("expired") == 1:
                    receipts.push_finished(alert["id"])
            if (read("pushover:retry_after", 0) or 0) > time.time():
                return
            for alert in receipts.claim_push_due(chat_id):
                if not receipts.is_pending(alert["id"]):
                    continue
                # Cancel by tag before replacing an expired/uncertain emergency
                # so there can never intentionally be two active alarms/event.
                if alert["attempts"] and not await self.cancel(alert["id"]):
                    receipts.push_failed(alert["id"])
                    continue
                if not reserve("pushover", 1, 9000, 300):
                    receipts.push_failed(alert["id"])
                    write("pushover:health", {"error": "bot message reservation limit", "checked": time.time()})
                    break
                receipts.push_starting(alert["id"])
                text = html.unescape(re.sub(r"<[^>]+>", "", alert["text"]))
                result = await self.request("POST", "messages.json", {
                    "user": Config.PUSHOVER_USER_KEY, "title": "VIOARĂ ZETA — confirmă alerta",
                    "message": ("Alertă neconfirmată. Disponibilitatea se poate schimba.\n" + text)[:1024],
                    "url": alert["url"] if len(alert["url"]) <= 512 else "",
                    "url_title": "Deschide anunțul", "priority": 2,
                    "retry": Config.PUSHOVER_RETRY_SECONDS, "expire": Config.PUSHOVER_EXPIRE_SECONDS,
                    "tags": alert["id"],
                })
                receipt = result.get("receipt", "")
                if isinstance(receipt, str) and RECEIPT.fullmatch(receipt):
                    receipts.push_sent(alert["id"], receipt)
                    if not receipts.is_pending(alert["id"]):
                        if await self.cancel(alert["id"]):
                            receipts.push_finished(alert["id"])
                else:
                    receipts.push_failed(alert["id"])
        finally:
            receipts.close()
