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
    async def _request(self, method: str, payload: dict) -> dict:
        """Check Telegram's acknowledgement, not just HTTP delivery. Never log tokens."""
        if not self.token or not self.chat_id:
            log.warning("Telegram not configured — skipping notification.")
            return {}
        url = TELEGRAM_API.format(token=self.token, method=method)
        payload = {"chat_id": self.chat_id, **payload}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 429:
                    data = resp.json()
                    retry_after = max(1, int(data.get("parameters", {}).get("retry_after", 30)))
                    log.warning("Telegram rate limited — waiting %ss", retry_after)
                    await asyncio.sleep(retry_after + 1)
                    resp = await client.post(url, json=payload)
                data = resp.json()
                if resp.status_code != 200 or data.get("ok") is not True:
                    log.error("Telegram %s rejected notification (HTTP %s, code %s)",
                              method, resp.status_code, data.get("error_code"))
                    return {}
                return data
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("Telegram %s request failed (%s)", method, type(exc).__name__)
            return {}

    async def _post(self, method: str, payload: dict) -> bool:
        return bool(await self._request(method, payload))

    async def send(self, text: str, *, silent: bool = False) -> bool:
        return await self._post("sendMessage", {
            "text": text, "parse_mode": "HTML", "disable_web_page_preview": False,
            "disable_notification": silent,
        })

    async def send_photo(self, image_url: str, caption: str) -> bool:
        return await self._post("sendPhoto", {
            "photo": image_url, "caption": caption[:CAPTION_LIMIT], "parse_mode": "HTML",
        })

    # --- high level ----------------------------------------------------------
    @staticmethod
    def _alert_buttons(receipt_id: str, url: str) -> dict:
        rows = []
        if url:
            rows.append([{"text": "🎻 Deschide anunțul", "url": url}])
        rows.append([{"text": "✅ Am văzut — oprește reamintirile", "callback_data": "seen:" + receipt_id}])
        return {"inline_keyboard": rows}

    async def _send_violin(self, listing: dict, text: str, *, event: str = "new", photo: bool = True) -> bool:
        from alert_receipts import AlertReceipts
        receipts = AlertReceipts()
        try:
            receipt_id = receipts.prepare(self.chat_id, listing, text, event)
            options = {"parse_mode": "HTML", "disable_notification": False,
                       "reply_markup": self._alert_buttons(receipt_id, self._normalize_url(listing.get("url", "")))}
            image_url = self._normalize_url(str(listing.get("image_url", "") or ""))
            data = {}
            if photo and Config.SEND_PHOTOS and image_url and len(text) <= CAPTION_LIMIT:
                data = await self._request("sendPhoto", {**options, "photo": image_url, "caption": text})
            if not data:
                data = await self._request("sendMessage", {**options, "text": text, "disable_web_page_preview": False})
            if not data:
                return False
            receipts.delivered(receipt_id, (data.get("result") or {}).get("message_id"))
            log.info("Telegram accepted violin alert %s: %s", receipt_id, listing.get("url", ""))
            return True
        finally:
            receipts.close()

    async def send_reminders(self) -> None:
        """Run every minute, even while the search scheduler is idle."""
        from alert_receipts import AlertReceipts
        receipts = AlertReceipts()
        try:
            for alert in receipts.claim_due(self.chat_id):
                if not receipts.is_pending(alert["id"]):
                    continue
                text = ("🔔🚨 <b>VIOARĂ ZETA — NU AI CONFIRMAT ALERTA</b>\n"
                        "Apasă «Am văzut» ca să oprești reamintirile.\n"
                        "Disponibilitatea se poate schimba între timp.\n\n" + alert["text"])
                sent = await self._post("sendMessage", {
                    "text": text, "parse_mode": "HTML", "disable_notification": False,
                    "reply_markup": self._alert_buttons(alert["id"], self._normalize_url(alert["url"])),
                })
                receipts.reminder_result(alert["id"], sent)
        finally:
            receipts.close()

    async def send_listings(self, listings: list, header_note: str = "") -> list:
        """Send every listing; return the ones that were DELIVERED (main.py only
        marks those as seen, so a Telegram hiccup never loses an instrument)."""
        delivered = []
        for i, listing in enumerate(listings, 1):
            msg = self._format_listing(i, listing)
            sent = False
            try:
                sent = await self._send_violin(listing, msg)
            except Exception as e:
                log.error(f"Telegram delivery error for {listing.get('title', '')[:50]}: {e}")
                sent = False
            if sent:
                delivered.append(listing)
            else:
                log.warning(f"NOT delivered (will retry next cycle): {listing.get('title', '')[:60]}")
            if i < len(listings):
                await asyncio.sleep(1.5)
        return delivered

    async def send_price_drops(self, drops: list):
        """drops: list of (listing, {old_price, new_price, drop_pct})."""
        if not drops:
            return []
        delivered = []
        for i, (listing, info) in enumerate(drops, 1):
            title = html.escape(str(listing.get("title", "")))
            url = self._normalize_url(listing.get("url", ""))
            lines = [
                "🚨📉 <b>VIOARĂ ZETA — PREȚ REDUS</b>",
                f"<b>{title}</b>",
                f"💰 <b>{html.escape(str(info.get('old_price', '')))} → {html.escape(str(info.get('new_price', '')))}</b>"
                f" (−{info.get('drop_pct', 0):.0f}%)",
                f"🛒 <b>Platform:</b> {html.escape(str(listing.get('platform', '')))}",
            ]
            if url:
                lines.append(f"🔗 <a href=\"{html.escape(url, quote=True)}\">View Listing</a>")
                lines.append(url)
            try:
                if await self._send_violin(listing, "\n".join(lines), event=f"drop:{info.get('new_price')}", photo=False):
                    delivered.append((listing, info))
            except Exception as exc:
                log.warning("Price-drop delivery failed (%s)", type(exc).__name__)
            await asyncio.sleep(1.5)
        return delivered

    async def send_watchdog(self, alerts: list):
        if not alerts:
            return
        lines = ["⚠️ <b>ZETA VIOLIN HUNTER — WATCHDOG</b>",
                 "Unele surse au probleme de acces sau acoperire. Celelalte căutări continuă:"]
        lines += [f"• {html.escape(a)}" for a in alerts]
        lines.append(f"🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        await self.send("\n".join(lines))

    async def send_no_changes(self, sources_ok: int = 0, sources_error: int = 0, sources_zero: int = 0,
                              error_details: list = None, pending: int = 0):
        if not Config.SEND_NO_CHANGES:
            log.info("SEND_NO_CHANGES=false — not sending the no-changes message")
            return
        health = f"🔎 {sources_ok} surse au răspuns"
        if sources_error:
            health += f", ⚠️ {sources_error} cu eroare sau acoperire parțială"
        if sources_zero:
            health += f", {sources_zero} fără rezultate"
        status_line = "✅ Căutarea s-a terminat cu succes." if not sources_error else "⚠️ Căutare terminată cu erori la unele surse."
        from zoneinfo import ZoneInfo
        detail = "\n".join(f"• {html.escape(item)}" for item in (error_details or []))
        pending_line = f"\n📬 {pending} alertă/alerte așteaptă livrarea; Telegram va fi reîncercat." if pending else ""
        await self.send(
            "📋 <b>RAPORT CĂUTARE — fără alertă nouă livrată</b>\n"
            f"{status_line}\n"
            f"{health}{pending_line}\n{detail}\n"
            f"🕒 {datetime.now(ZoneInfo(Config.SEARCH_TIMEZONE)):%d.%m.%Y %H:%M %Z}",
            silent=True,
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
        price_context = listing.get("price_context", {}) or {}

        safe_url = self._normalize_url(url)
        lines = [
            "🚨🎻 <b>VIOARĂ ZETA GĂSITĂ!</b>",
            f"<b>{html.escape(title[:240])}</b>",
            f"💰 <b>Preț: {html.escape(price)}</b>{self._eur_hint(price_context)}",
        ]
        if listing.get("verification") == "unknown":
            lines.append("⚠️ Disponibilitate neverificată; verifică pagina înainte de a cumpăra.")
        if listing.get("auction") or "bid)" in price.lower():
            lines.append("🔨 Licitație — suma afișată poate fi oferta curentă, fără comisioane.")
        if listing.get("auction_end"):
            try:
                from zoneinfo import ZoneInfo
                end = datetime.fromisoformat(str(listing["auction_end"]).replace("Z", "+00:00"))
                lines.append(f"⏳ Închidere: {end.astimezone(ZoneInfo(Config.SEARCH_TIMEZONE)):%d.%m.%Y %H:%M %Z}")
            except ValueError:
                pass
        if listing.get("pickup_only"):
            lines.append("📦 Ridicare personală; vânzătorul nu expediază.")
        if listing.get("mixed_lot"):
            lines.append("🎻 Lot mixt: include viori Zeta și alte instrumente.")
        if listing.get("estate_sale"):
            from filters import estate_violin_evidence
            evidence = estate_violin_evidence(title, description)
            if evidence:
                lines.append(f"🎻 În inventar: {html.escape(evidence[:180])}")
            lines.append("🏠 Vânzare locală; confirmă ridicarea sau expedierea cu organizatorul.")
            if listing.get("sale_end"):
                lines.append(f"📅 Ultima zi anunțată: {html.escape(str(listing['sale_end']))}")
        rare = self._rare_flags(title, description)
        if rare:
            lines.append(f"🔥 <b>Rar:</b> {html.escape(', '.join(rare))}")
        dup = listing.get("duplicate_of") or {}
        if dup:
            lines.append(f"♻️ Posibil același instrument ca [{html.escape(str(dup.get('platform', '')))}] "
                         f"{html.escape(str(dup.get('title', ''))[:50])}")

        if price_context.get("avg_price"):
            avg = price_context["avg_price"]
            deal_pct = price_context.get("deal_pct", 0) or 0
            total = price_context.get("total_seen", 0)
            if price_context.get("is_deal"):
                lines.append(f"📊 {abs(deal_pct):.0f}% sub mediana prețurilor cerute comparabile (${avg:.0f}, {total} anunțuri)")
            else:
                lines.append(f"📊 Mediană cerută comparabilă: ${avg:.0f} ({total} anunțuri)")

        lines.append(f"📍 <b>Location:</b> {html.escape(location)}")
        ships = listing.get("ships_to_ro")
        if ships is True:
            lines.append("🚚 <b>Livrează în România:</b> da")
        elif ships is False:
            lines.append("🚚 <b>Livrează în România:</b> nu / neprecizat")
        lines.append(f"🛒 <b>Platform:</b> {html.escape(platform)}"
                     + (f" · {html.escape(str(listing.get('condition')))}" if listing.get("condition") else ""))
        if date_posted:
            lines.append(f"📅 <b>Posted:</b> {html.escape(date_posted)}")
        if description:
            lines.append(f"📝 {html.escape(description[:180])}...")
        lines.append(f"🔔 Reamintire în {Config.ALERT_FIRST_REMINDER_MINUTES} min, apoi la "
                     f"{Config.ALERT_REPEAT_MINUTES} min, până apeși «Am văzut».")

        if safe_url:
            lines.append(f"🔗 <a href=\"{html.escape(safe_url, quote=True)}\">View Listing</a>")
            lines.append(safe_url)

        return "\n".join(lines)

    @staticmethod
    def _eur_hint(price_context: dict) -> str:
        usd = (price_context or {}).get("price_usd")
        if not usd:
            return ""
        try:
            from fx import usd_to
            eur = usd_to(float(usd), "EUR")
            return f"  (≈ {eur:,.0f} €)".replace(",", " ")
        except Exception:
            return ""

    @staticmethod
    def _rare_flags(title: str, description: str) -> list:
        import re
        text = f"{title} {description}".lower()
        flags = []
        if re.search(r"jean[\s\-]*luc[\s\-]*ponty|\bjlp\b|ponty signature", text):
            flags.append("Jean-Luc Ponty signature")
        if re.search(r"\b5[\s\-]*(string|strings|str|corde|corzi|saiten|cordes|cuerdas|snarig|弦)", text):
            flags.append("5 corzi")
        if re.search(r"\bmidi\b", text):
            flags.append("MIDI")
        if re.search(r"boyd tinsley|eileen ivers", text):
            flags.append("model signature")
        return flags

    async def send_digest(self, active: list, gone: list, price_stats: dict):
        """Weekly overview: live Zeta listings, ones that disappeared (probably sold), price stats."""
        lines = [f"📰 <b>ZETA VIOLIN HUNTER — rezumat săptămânal</b>",
                 f"🎻 {len(active)} anunț(uri) active, {len(gone)} dispărute în ultimele 2 săptămâni"]
        for r in active[:20]:
            lines.append(f"• {html.escape(r['title'][:55])} — {html.escape(r['price'])} [{html.escape(r['platform'][:18])}]\n  {html.escape(r['url'])}")
        if gone:
            lines.append("\n<b>Nerevăzute recent (disponibilitate necunoscută):</b>")
            for r in gone[:10]:
                lines.append(f"• {html.escape(r['title'][:55])} — {html.escape(r['price'])} [{html.escape(r['platform'][:18])}]")
        if price_stats and price_stats.get("total_tracked"):
            lines.append(f"\n📊 Prețuri urmărite: {price_stats['total_tracked']} · medie ${price_stats.get('avg_usd', 0):,.0f} "
                         f"· min ${price_stats.get('min_usd', 0):,.0f} · max ${price_stats.get('max_usd', 0):,.0f}")
        lines.append(f"🕒 {datetime.utcnow():%Y-%m-%d %H:%M} UTC")
        return await self.send("\n".join(lines)[:3900])

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
