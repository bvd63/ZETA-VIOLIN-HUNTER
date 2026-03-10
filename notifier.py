"""
Telegram notifier — sends formatted alerts for new Zeta listings.
"""

import logging
import httpx
from config import Config

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = TELEGRAM_API.format(token=token)

    async def send(self, text: str):
        if not self.token or not self.chat_id:
            log.warning("Telegram not configured — skipping notification.")
            log.info(f"[ALERT PREVIEW]\n{text}")
            return
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.api_url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            })
            if resp.status_code != 200:
                log.error(f"Telegram error: {resp.text}")

    async def send_listings(self, listings: list):
        # Send summary header
        header = (
            f"🎻 <b>ZETA VIOLIN HUNTER</b>\n"
            f"📦 <b>{len(listings)} new listing(s) found!</b>\n"
            f"{'─' * 30}"
        )
        await self.send(header)

        # Send each listing as a separate message
        for i, listing in enumerate(listings, 1):
            msg = self._format_listing(i, listing)
            await self.send(msg)

    async def send_no_changes(self):
        # Silent — don't spam if nothing found
        pass

    def _format_listing(self, idx: int, listing: dict) -> str:
        price = listing.get("price", "N/A")
        location = listing.get("location", "Unknown")
        platform = listing.get("platform", "Unknown")
        title = listing.get("title", "No title")
        url = listing.get("url", "")
        description = listing.get("description", "")
        date_posted = listing.get("date_posted", "")
        relevance = listing.get("relevance_score", "?")

        lines = [
            f"🎻 <b>#{idx} — {title}</b>",
            f"💰 <b>Price:</b> {price}",
            f"📍 <b>Location:</b> {location}",
            f"🛒 <b>Platform:</b> {platform}",
        ]
        if date_posted:
            lines.append(f"📅 <b>Posted:</b> {date_posted}")
        if description:
            lines.append(f"📝 {description[:200]}...")
        lines.append(f"⭐ <b>Relevance:</b> {relevance}/10")
        if url:
            lines.append(f"🔗 <a href='{url}'>View Listing</a>")

        return "\n".join(lines)
