"""
Telegram notifier — sends formatted alerts for new Zeta listings.
"""

import logging
import httpx
import html
from urllib.parse import urlsplit, urlunsplit
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
        price = str(listing.get("price", "N/A"))
        location = str(listing.get("location", "Unknown"))
        platform = str(listing.get("platform", "Unknown"))
        title = str(listing.get("title", "No title"))
        url = listing.get("url", "")
        description = str(listing.get("description", ""))
        date_posted = str(listing.get("date_posted", ""))
        relevance = str(listing.get("relevance_score", "?"))

        safe_url = self._normalize_url(url)
        safe_title = html.escape(title)
        safe_price = html.escape(price)
        safe_location = html.escape(location)
        safe_platform = html.escape(platform)
        safe_description = html.escape(description[:200])

        lines = [
            f"🎻 <b>#{idx} — {safe_title}</b>",
            f"💰 <b>Price:</b> {safe_price}",
            f"📍 <b>Location:</b> {safe_location}",
            f"🛒 <b>Platform:</b> {safe_platform}",
        ]
        if date_posted:
            lines.append(f"📅 <b>Posted:</b> {html.escape(date_posted)}")
        if description:
            lines.append(f"📝 {safe_description}...")
        lines.append(f"⭐ <b>Relevance:</b> {html.escape(relevance)}/10")
        if safe_url:
            lines.append(f"🔗 <a href=\"{html.escape(safe_url, quote=True)}\">View Listing</a>")
            # Plain URL fallback: if Telegram refuses HTML link rendering, user still sees a clickable URL.
            lines.append(safe_url)

        return "\n".join(lines)

    def _normalize_url(self, raw_url: str) -> str:
        if not raw_url:
            return ""

        url = str(raw_url).strip()
        if url.startswith("//"):
            url = "https:" + url

        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return ""
        if not parts.netloc:
            return ""

        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment))
