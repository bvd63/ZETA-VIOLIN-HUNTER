"""
Telegram commands via long polling (getUpdates) — no public URL needed.

  /cauta   start a search cycle now
  /status  last cycle + per-source health
  /active  Zeta listings seen live in the last 3 days
  /help

Only messages from Config.TELEGRAM_CHAT_ID are honoured.
"""

import asyncio
import html
import logging
from datetime import datetime

import httpx
from config import Config

log = logging.getLogger(__name__)


class TelegramCommands:
    def __init__(self, token: str, chat_id: str, on_search, is_searching, status_tracker, database_factory, notifier):
        self.token = token
        self.chat_id = str(chat_id)
        self.on_search = on_search          # async callable → runs a cycle
        self.is_searching = is_searching    # callable → bool
        self.status_tracker = status_tracker
        self.database_factory = database_factory
        self.notifier = notifier
        self.offset = self._load_offset()

    # Persist the update offset so a restart never re-executes the last command.
    @staticmethod
    def _load_offset() -> int:
        try:
            from database import connect
            conn = connect()
            conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            row = conn.execute("SELECT value FROM kv WHERE key = 'telegram_offset'").fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as e:
            log.debug(f"telegram offset load failed: {e}")
            return 0

    def _save_offset(self) -> None:
        try:
            from database import connect
            conn = connect()
            conn.execute("INSERT OR REPLACE INTO kv VALUES ('telegram_offset', ?, ?)",
                         (str(self.offset), datetime.utcnow().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            log.debug(f"telegram offset save failed: {e}")

    async def run(self) -> None:
        if not self.token or not self.chat_id:
            log.info("Telegram commands disabled (no token/chat id)")
            return
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        async with httpx.AsyncClient(timeout=70) as client:
            # A webhook set once in the past makes getUpdates answer 409 forever — clear it.
            try:
                await client.post(f"https://api.telegram.org/bot{self.token}/deleteWebhook",
                                  json={"drop_pending_updates": False})
            except Exception as e:
                log.debug(f"deleteWebhook failed: {e}")
            log.info("💬 Telegram commands active: /cauta /status /active /help")
            while True:
                try:
                    resp = await client.get(url, params={"timeout": 50, "offset": self.offset,
                                                         "allowed_updates": '["message"]'})
                    if resp.status_code == 409:
                        # Usually the previous container still polling during a deploy overlap.
                        log.info("Telegram getUpdates 409 (previous container still polling) — retrying in 60s")
                        await asyncio.sleep(60)
                        continue
                    if resp.status_code != 200:
                        log.warning(f"Telegram getUpdates HTTP {resp.status_code}: {resp.text[:120]}")
                        await asyncio.sleep(15)
                        continue
                    updates = resp.json().get("result", [])
                    for update in updates:
                        self.offset = max(self.offset, int(update["update_id"]) + 1)
                        self._save_offset()
                        await self._handle(update.get("message") or {})
                except Exception as e:
                    log.warning(f"Telegram polling error: {e}")
                    await asyncio.sleep(15)

    async def _handle(self, message: dict) -> None:
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if chat_id != self.chat_id or not text.startswith("/"):
            return
        cmd = text.split()[0].lower().split("@")[0]
        log.info(f"💬 Telegram command {cmd}")
        if cmd in ("/cauta", "/search", "/run"):
            if self.is_searching():
                await self.notifier.send("⏳ O căutare rulează deja. Așteaptă să se termine.")
                return
            await self.notifier.send("🔍 Pornesc căutarea acum. Durează 1-3 minute, alertele vin pe măsură ce apar.")
            asyncio.create_task(self._search_and_report())
        elif cmd == "/status":
            await self.notifier.send(self._status_text())
        elif cmd in ("/active", "/activ", "/live"):
            await self.notifier.send(self._active_text())
        elif cmd in ("/verifica", "/verify"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await self.notifier.send("Trimite /verifica urmat de linkul anunțului.")
            else:
                await self._verify_url(parts[1].strip())
        else:
            await self.notifier.send(
                "🎻 <b>Comenzi</b>\n"
                "/cauta — pornește o căutare acum\n"
                "/status — starea surselor și ultimul ciclu\n"
                "/active — viorile Zeta văzute live în ultimele 3 zile\n"
                "/verifica LINK — verifică un anunț și istoricul notificării\n"
                "/help — acest mesaj"
            )

    async def _verify_url(self, url: str) -> None:
        from offer_verifier import verify_offer, public_url
        from filters import classify
        if not public_url(url):
            await self.notifier.send("Linkul trebuie să fie o pagină publică HTTP/HTTPS.")
            return
        listing = {"url": url, "title": "", "description": "", "source": "search"}
        async with httpx.AsyncClient(timeout=20) as client:
            state, why = await verify_offer(listing, client)
        labels = {"live": "Ofertă verificată", "unknown": "Disponibilitate neverificată",
                  "dead": "Anunț / licitație încheiată", "non_sale": "Fără ofertă de vânzare"}
        lines = [f"🔎 <b>{labels.get(state, state)}</b>", html.escape(why)]
        if listing.get("title"):
            lines.append(html.escape(listing["title"][:250]))
            reason = classify(listing)
            if reason:
                reasons = {"noise": "publicație, accesoriu sau alt instrument", "intent": "nu este un anunț de vânzare",
                           "new_stock": "instrument nou", "other_brand": "altă marcă", "non_zeta": "nu pot identifica o vioară Zeta",
                           "sold": "vândut / încheiat", "non_sale": "articol / recenzie"}
                lines.append("Filtru: " + reasons.get(reason, reason))
            else:
                lines.append("🎻 Recunoscut ca vioară Zeta; trece filtrele.")
        db = self.database_factory()
        try:
            notified = db.was_url_alerted(url)
        finally:
            db.close()
        lines.append("📬 Apare în istoricul notificărilor." if notified else "📭 Nu apare în istoricul notificărilor păstrat.")
        lines.append("Verificarea arată starea actuală; nu reconstituie o căutare din trecut.")
        await self.notifier.send("\n".join(lines))

    async def _search_and_report(self) -> None:
        try:
            count = await self.on_search()
            await self.notifier.send(f"✅ Căutare terminată: {count} anunț(uri) noi.")
        except Exception as e:
            log.error(f"/cauta error: {e}")
            await self.notifier.send(f"❌ Eroare la căutare: {html.escape(str(e))[:200]}")

    def _status_text(self) -> str:
        st = self.status_tracker.get_status()
        last = st.get("last_cycle") or {}
        lines = ["📊 <b>Status</b>"]
        hours = ", ".join(f"{int(h.strip()):02d}:00" for h in Config.SEARCH_HOURS.split(",") if h.strip().isdigit())
        lines.append(f"🕒 Program: {hours} · {html.escape(Config.SEARCH_TIMEZONE)}")
        if last:
            lines.append(f"Ultimul ciclu: {html.escape(str(last.get('run_at', ''))[:16])} UTC, "
                         f"{last.get('duration_seconds', 0)}s, {last.get('new_listings', 0)} noi, "
                         f"{last.get('raw_listings', 0)} articole citite")
        scrapers = st.get("scrapers") or {}
        for name, s in sorted(scrapers.items()):
            raw = s.get("raw", 0) or 0
            if raw < 0:
                lines.append(f"⚪ {html.escape(name)}: sărit (neconfigurat sau cotă)")
                continue
            flag = "🔴" if s.get("error") else ("🟡" if raw == 0 and not (s.get("details") or {}).get("requests_ok") else "🟢")
            lines.append(f"{flag} {html.escape(name)}: {raw} citite, {s.get('new', 0)} noi"
                         + (f", {s.get('zero_streak')} cicluri pe 0" if s.get("zero_streak") else ""))
            if s.get("error"):
                lines.append("  " + html.escape(str(s["error"])[:100]))
            rejected = (s.get("details") or {}).get("rejected") or {}
            if rejected:
                lines.append("  Filtre: " + html.escape(", ".join(f"{reason}={count}" for reason, count in rejected.items()))[:180])
        lines.append(f"🕒 {datetime.utcnow():%Y-%m-%d %H:%M} UTC")
        return "\n".join(lines)[:3900]

    def _active_text(self) -> str:
        db = self.database_factory()
        try:
            rows = db.active_listings(days=3)
        finally:
            db.close()
        if not rows:
            return "📭 Nicio vioară Zeta văzută live în ultimele 3 zile."
        lines = [f"🎻 <b>{len(rows)} anunț(uri) Zeta active</b> (văzute în ultimele 3 zile)"]
        for r in rows[:25]:
            lines.append(f"• {html.escape(r['title'][:60])} — {html.escape(r['price'])} "
                         f"[{html.escape(r['platform'][:20])}]\n  {html.escape(r['url'])}")
        return "\n".join(lines)[:3900]
