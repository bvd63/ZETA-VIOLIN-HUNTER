"""
Telegram notifier — sends formatted alerts for new Zeta listings.
Listings with a photo go out as sendPhoto (image + caption); everything else
as sendMessage. Also sends price-drop re-alerts and watchdog warnings.
"""

import asyncio
import logging
import httpx
import html
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime
from config import Config

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
CAPTION_LIMIT = 1000  # Telegram allows 1024 for captions


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    # --- low level -----------------------------------------------------------
    async def _post(self, method: str, payload: dict) -> bool:
        """POST to the Bot API with one retry on 429. Returns True on success."""
        if not self.token or not self.chat_id:
            log.warning("Telegram not configured — skipping notification.")
            log.info(f"[ALERT PREVIEW {method}]\n{payload.get('caption') or payload.get('text')}")
            return True
        url = TELEGRAM_API.format(token=self.token, method=method)
        payload = {"chat_id": self.chat_id, **payload}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 429:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                except Exception:
                    retry_after = 30
                log.warning(f"Telegram rate limited — waiting {retry_after}s")
                await asyncio.sleep(retry_after + 1)
                resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                log.error(f"Telegram {method} error: {resp.text[:300]}")
                return False
            return True

    async def send(self, text: str) -> bool:
        return await self._post("sendMessage", {
            "text": text, "parse_mode": "HTML", "disable_web_page_preview": False,
        })

    async def send_photo(self, image_url: str, caption: str) -> bool:
        return await self._post("sendPhoto", {
            "photo": image_url, "caption": caption[:CAPTION_LIMIT], "parse_mode": "HTML",
        })

    # --- high level ----------------------------------------------------------
    async def send_listings(self, listings: list, header_note: str = ""):
        header = (
            f"🎻 <b>ZETA VIOLIN HUNTER</b>\n"
            f"📦 <b>{len(listings)} new listing(s) found!</b>{header_note}\n"
            f"{'─' * 30}"
        )
        await self.send(header)

        for i, listing in enumerate(listings, 1):
            msg = self._format_listing(i, listing)
            image_url = self._normalize_url(str(listing.get("image_url", "") or ""))
            sent = False
            if Config.SEND_PHOTOS and image_url:
                sent = await self.send_photo(image_url, msg)
                if not sent:
                    log.info("sendPhoto failed — falling back to text message")
            if not sent:
                await self.send(msg)
            if i < len(listings):
                await asyncio.sleep(1.5)

    async def send_price_drops(self, drops: list):
        """drops: list of (listing, {old_price, new_price, drop_pct})."""
        if not drops:
            return
        await self.send(
            f"📉 <b>ZETA VIOLIN HUNTER — PRICE DROP</b>\n"
            f"{len(drops)} known listing(s) got cheaper\n{'─' * 30}"
        )
        for i, (listing, info) in enumerate(drops, 1):
            title = html.escape(str(listing.get("title", "")))
            url = self._normalize_url(listing.get("url", ""))
            lines = [
                f"📉 <b>#{i} — {title}</b>",
                f"💰 <b>{html.escape(str(info.get('old_price', '')))} → {html.escape(str(info.get('new_price', '')))}</b>"
                f" (−{info.get('drop_pct', 0):.0f}%)",
                f"🛒 <b>Platform:</b> {html.escape(str(listing.get('platform', '')))}",
            ]
            if url:
                lines.append(f"🔗 <a href=\"{html.escape(url, quote=True)}\">View Listing</a>")
                lines.append(url)
            await self.send("\n".join(lines))
            await asyncio.sleep(1.5)

    async def send_watchdog(self, alerts: list):
        if not alerts:
            return
        lines = ["⚠️ <b>ZETA VIOLIN HUNTER — WATCHDOG</b>",
                 "Aceste surse nu mai returnează nimic. Verifică logurile Railway:"]
        lines += [f"• {html.escape(a)}" for a in alerts]
        lines.append(f"🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        await self.send("\n".join(lines))

    async def send_no_changes(self):
        if not Config.SEND_NO_CHANGES:
            log.info("SEND_NO_CHANGES=false — not sending the no-changes message")
            return
        await self.send(
            "🎻 <b>ZETA VIOLIN HUNTER</b>\n"
            "✅ Căutarea s-a terminat cu succes.\n"
            "📭 Nu au fost găsite anunțuri noi în acest ciclu.\n"
            f"🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

    # --- formatting ----------------------------------------------------------
    def _format_listing(self, idx: int, listing: dict) -> str:
        price = str(listing.get("price", "N/A"))
        location = str(listing.get("location", "Unknown"))
        platform = str(listing.get("platform", "Unknown"))
        title = str(listing.get("title", "No title"))
        url = listing.get("url", "")
        description = str(listing.get("description", ""))
        date_posted = str(listing.get("date_posted", ""))
        relevance = str(listing.get("relevance_score", "?"))
        price_context = listing.get("price_context", {}) or {}

        safe_url = self._normalize_url(url)
        lines = [
            f"🎻 <b>#{idx} — {html.escape(title)}</b>",
            f"💰 <b>Price:</b> {html.escape(price)}",
        ]

        if price_context.get("avg_price"):
            avg = price_context["avg_price"]
            deal_pct = price_context.get("deal_pct", 0) or 0
            total = price_context.get("total_seen", 0)
            if price_context.get("is_deal"):
                lines.append(f"🔥 <b>DEAL!</b> {abs(deal_pct):.0f}% below avg (${avg:.0f}, {total} seen)")
            else:
                lines.append(f"📊 Avg: ${avg:.0f} ({total} seen)")

        lines.append(f"📍 <b>Location:</b> {html.escape(location)}")
        lines.append(f"🛒 <b>Platform:</b> {html.escape(platform)}")
        if date_posted:
            lines.append(f"📅 <b>Posted:</b> {html.escape(date_posted)}")
        if description:
            lines.append(f"📝 {html.escape(description[:180])}...")
        lines.append(f"⭐ <b>Relevance:</b> {html.escape(relevance)}/10")

        if safe_url:
            lines.append(f"🔗 <a href=\"{html.escape(safe_url, quote=True)}\">View Listing</a>")
            lines.append(safe_url)

        return "\n".join(lines)

    def _normalize_url(self, raw_url: str) -> str:
        if not raw_url:
            return ""
        url = str(raw_url).strip()
        if url.startswith("//"):
            url = "https:" + url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment))
