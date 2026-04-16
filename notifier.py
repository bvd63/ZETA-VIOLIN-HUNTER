"""
Telegram notifier — sends formatted alerts for new Zeta listings.
"""

import asyncio
import logging
import httpx
import html
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime
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
            if resp.status_code == 429:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                except Exception:
                    retry_after = 30
                log.warning(f"Telegram rate limited — waiting {retry_after}s")
                await asyncio.sleep(retry_after + 1)
                resp = await client.post(self.api_url, json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                })
                if resp.status_code != 200:
                    log.error(f"Telegram retry failed: {resp.text}")
            elif resp.status_code != 200:
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
            if i < len(listings):
                await asyncio.sleep(1.5)

    async def send_no_changes(self):
        message = (
            "🎻 <b>ZETA VIOLIN HUNTER</b>\n"
            "✅ Căutarea s-a terminat cu succes.\n"
            "📭 Nu au fost găsite anunțuri noi în acest ciclu.\n"
            f"🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        await self.send(message)

    def _format_listing(self, idx: int, listing: dict) -> str:
        price = str(listing.get("price", "N/A"))
        location = str(listing.get("location", "Unknown"))
        platform = str(listing.get("platform", "Unknown"))
        title = str(listing.get("title", "No title"))
        url = listing.get("url", "")
        description = str(listing.get("description", ""))
        date_posted = str(listing.get("date_posted", ""))
        relevance = str(listing.get("relevance_score", "?"))
        ai_status = listing.get("ai_verified", "")
        price_context = listing.get("price_context", {})

        safe_url = self._normalize_url(url)
        safe_title = html.escape(title)
        safe_price = html.escape(price)
        safe_location = html.escape(location)
        safe_platform = html.escape(platform)
        safe_description = html.escape(description[:200])

        lines = [
            f"🎻 <b>#{idx} — {safe_title}</b>",
            f"💰 <b>Price:</b> {safe_price}",
        ]

        # Price context (if available)
        if price_context and price_context.get("avg_price"):
            avg = price_context["avg_price"]
            deal_pct = price_context.get("deal_pct", 0)
            total = price_context.get("total_seen", 0)
            if price_context.get("is_deal"):
                lines.append(f"🔥 <b>DEAL!</b> {abs(deal_pct):.0f}% below avg (${avg:.0f}, {total} seen)")
            else:
                lines.append(f"📊 Avg: ${avg:.0f} ({total} seen)")

        lines.extend([
            f"📍 <b>Location:</b> {safe_location}",
            f"🛒 <b>Platform:</b> {safe_platform}",
        ])

        if date_posted:
            lines.append(f"📅 <b>Posted:</b> {html.escape(date_posted)}")
        if description:
            lines.append(f"📝 {safe_description}...")

        lines.append(f"⭐ <b>Relevance:</b> {html.escape(relevance)}/10")

        # AI verification badge
        if ai_status == "YES":
            lines.append("🤖 <b>AI: ✅ Confirmed Zeta</b>")
        elif "MAYBE" in str(ai_status):
            lines.append("🤖 <b>AI: ⚠️ Possible Zeta</b>")

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
