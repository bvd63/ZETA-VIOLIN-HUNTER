"""
Zeta Violin Hunter Agent
Searches globally for Zeta electric violin listings and sends Telegram alerts.
"""

import asyncio
import logging
import random
import warnings
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Python 3.12 (Railway image) warns on datetime.utcnow(); keep the logs readable.
warnings.filterwarnings("ignore", message=".*utcnow.*", category=DeprecationWarning)

from database import Database, connect  # noqa: E402
from notifier import TelegramNotifier  # noqa: E402
from filters import classify  # noqa: E402
from liveness import is_live  # noqa: E402
from dedup import is_duplicate  # noqa: E402
from fx import refresh_rates  # noqa: E402
from diagnostics import run_reachability  # noqa: E402
from telegram_commands import TelegramCommands  # noqa: E402
from scrapers.google import DIRECT_HOSTS  # noqa: E402

from scrapers.reverb import ReverbScraper  # noqa: E402
from scrapers.ebay import EbayScraper  # noqa: E402
from scrapers.google import GoogleScraper  # noqa: E402
from scrapers.brave import BraveScraper  # noqa: E402
from scrapers.craigslist import CraigslistScraper  # noqa: E402
from scrapers.shopgoodwill import ShopGoodwillScraper  # noqa: E402
from scrapers.hibid import HiBidScraper  # noqa: E402
from scrapers.kijiji import KijijiScraper  # noqa: E402
from scrapers.marktplaats import MarktplaatsScraper  # noqa: E402
from scrapers.willhaben import WillhabenScraper  # noqa: E402
from scrapers.schibsted import SchibstedScraper  # noqa: E402
from scrapers.gumtree import GumtreeScraper  # noqa: E402
from scrapers.olx import OlxScraper  # noqa: E402
from scrapers.subito import SubitoScraper  # noqa: E402
from scrapers.shopify_dealers import ShopifyDealersScraper  # noqa: E402
from scrapers.mercari_jp import MercariJPScraper  # noqa: E402
from scrapers.yahoo_auctions_jp import YahooAuctionsJPScraper  # noqa: E402
from scrapers.guitar_center import GuitarCenterScraper  # noqa: E402
from scrapers.reddit_scraper import RedditScraper  # noqa: E402
from scrapers.facebook_marketplace import FacebookMarketplaceScraper  # noqa: E402

from price_tracker import PriceTracker, parse_price_usd  # noqa: E402
from status_tracker import StatusTracker  # noqa: E402
from config import Config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
search_cycle_lock = asyncio.Lock()
status_tracker = StatusTracker()

DROP_REASON_LABELS = {
    "other_brand": "other-brand",
    "new_stock": "new-stock/dealer",
    "noise": "noise",
    "intent": "non-sale intent",
    "sold": "sold/ended",
    "non_zeta": "non-Zeta-violin",
    "url": "invalid-url",
    "direct_covered": "search-hit on directly-scraped site",
    "dead_link": "ended/expired page",
    "dead_cached": "known dead page (re-check after 7 days)",
    "liveness_deferred": "search-hit deferred to next cycle (liveness budget)",
    "duplicate_flagged": "flagged as possible duplicate (still sent)",
}
MAX_LIVENESS_CHECKS = 60      # per scraper per cycle
LIVENESS_CONCURRENCY = 6
LIVENESS_TIMEOUT_SEC = 180
DEAD_CACHE_DAYS = 7


def _is_direct_host(url: str) -> bool:
    """True for hosts we scrape directly. Exact domain or subdomain match —
    'gumtree.com' must not swallow gumtree.com.au, 'jp.mercari.com' must not
    swallow mercari.com (US)."""
    host = urlsplit(url).netloc.lower().split(":")[0]
    labels = host.split(".")
    for d in DIRECT_HOSTS:
        if d.endswith("."):            # "ebay." → any ebay.<tld> / <sub>.ebay.<tld>
            if d[:-1] in labels:
                return True
        elif host == d or host.endswith("." + d):
            return True
    return False


def build_scrapers() -> list:
    return [
        ReverbScraper(),
        EbayScraper(Config.EBAY_CLIENT_ID, Config.EBAY_CLIENT_SECRET),
        GoogleScraper(Config.GOOGLE_API_KEY, Config.GOOGLE_CSE_ID),
        BraveScraper(),
        CraigslistScraper(),
        ShopGoodwillScraper(),
        HiBidScraper(),
        KijijiScraper(),
        MarktplaatsScraper(),
        WillhabenScraper(),
        SchibstedScraper(),
        GumtreeScraper(),
        OlxScraper(),
        SubitoScraper(),
        ShopifyDealersScraper(),
        MercariJPScraper(),
        YahooAuctionsJPScraper(),       # runs only from non-EEA egress (Railway = Singapore)
        GuitarCenterScraper(),          # runs only with US egress
        FacebookMarketplaceScraper(),   # Playwright; runs only with US egress
        RedditScraper(),
        # Disabled (see CLAUDE.md §2): Kleinanzeigen, Wallapop, Leboncoin,
        # Maestronet, Violinist.com, Audiofanzine — covered by Google/Brave.
    ]


async def _verify_search_hits(candidates: list, db: Database, http: httpx.AsyncClient, dropped: dict) -> list:
    """Liveness-check search-engine hits concurrently. Returns the live ones.
    Dead pages go to liveness_cache (re-checked after DEAD_CACHE_DAYS); hard
    404/410 and Reverb 'not live' are permanent (seen)."""
    sem = asyncio.Semaphore(LIVENESS_CONCURRENCY)
    alive_listings = []

    async def check(listing: dict):
        async with sem:
            return listing, await is_live(listing.get("url", ""), http)

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*[check(c) for c in candidates], return_exceptions=True), timeout=LIVENESS_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        log.warning(f"   liveness checks timed out after {LIVENESS_TIMEOUT_SEC}s — deferring {len(candidates)} hits")
        dropped["liveness_deferred"] = dropped.get("liveness_deferred", 0) + len(candidates)
        return []
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            dropped["liveness_deferred"] = dropped.get("liveness_deferred", 0) + 1
            continue
        listing, (alive, why) = outcome
        if alive:
            listing["liveness"] = why
            alive_listings.append(listing)
            continue
        dropped["dead_link"] = dropped.get("dead_link", 0) + 1
        log.info(f"   dead link ({why}): {listing.get('title', '')[:60]} {listing.get('url', '')[:80]}")
        permanent = why.startswith("http 4") or why.startswith("reverb")
        if permanent:
            db.mark_seen(listing["id"], listing)
        else:
            db.mark_dead(listing["id"], why)
    return alive_listings


async def _run_scraper_with_resilience(scraper, db: Database, price_tracker: PriceTracker,
                                       semaphore: asyncio.Semaphore) -> dict:
    """Run one scraper with timeout + retry, filter, verify, detect price drops.
    Returns {name, new, drops, status, raw, duration}. NOTE: new listings are
    NOT marked seen here — run_search_cycle does that after Telegram delivery."""
    async with semaphore:
        retries = max(0, Config.SCRAPER_RETRIES)
        for attempt in range(1, retries + 2):
            started = datetime.utcnow()
            scraper.fetched = 0
            scraper.skipped = False
            try:
                log.info(f"🔍 Searching: {scraper.name} (attempt {attempt}/{retries + 1})")
                listings = await asyncio.wait_for(
                    scraper.search(), timeout=max(1, Config.SCRAPER_TIMEOUT_SEC)
                )
                fetched = max(int(getattr(scraper, "fetched", 0) or 0), len(listings))
                if getattr(scraper, "skipped", False) and not listings:
                    fetched = -1  # skipped (guard/unconfigured): ignored by the watchdog
                    log.info(f"   {scraper.name}: skipped this cycle")
                else:
                    log.info(f"   Found {len(listings)} candidate listings from {scraper.name} ({fetched} items fetched)")

                new_listings, drops, dropped = [], [], {}
                to_verify = []
                seen_in_run = set()
                recent_alerts = db.recent_alerts(days=60)

                for listing in listings:
                    reason = classify(listing)
                    if reason:
                        dropped[reason] = dropped.get(reason, 0) + 1
                        log.debug(f"   drop[{reason}] {listing.get('title', '')[:70]}")
                        continue
                    if listing["id"] in seen_in_run:
                        continue
                    seen_in_run.add(listing["id"])
                    if listing.get("source") != "search":
                        db.touch_active(listing)  # still live on its platform this cycle
                    if db.is_seen(listing["id"]):
                        # Price-drop re-alerts only for instruments the owner actually received
                        if db.was_alerted(listing["id"]):
                            info = price_tracker.update_price(listing)
                            if info:
                                drops.append((listing, info))
                        continue
                    if listing.get("source") == "search":
                        if _is_direct_host(listing.get("url", "")):
                            dropped["direct_covered"] = dropped.get("direct_covered", 0) + 1
                            db.mark_seen(listing["id"], listing)
                            continue
                        if db.dead_recently(listing["id"], DEAD_CACHE_DAYS):
                            dropped["dead_cached"] = dropped.get("dead_cached", 0) + 1
                            continue
                        to_verify.append(listing)
                        continue
                    new_listings.append(listing)

                if to_verify:
                    # Random order so a permanently long tail cannot starve the same hits every day
                    random.shuffle(to_verify)
                    if len(to_verify) > MAX_LIVENESS_CHECKS:
                        dropped["liveness_deferred"] = dropped.get("liveness_deferred", 0) + len(to_verify) - MAX_LIVENESS_CHECKS
                        to_verify = to_verify[:MAX_LIVENESS_CHECKS]
                    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
                        new_listings.extend(await _verify_search_hits(to_verify, db, http, dropped))

                # Same instrument already alerted from another platform / relisted?
                # Two sellers can list the same model at similar prices, so NEVER
                # suppress — annotate the alert instead.
                for listing in new_listings:
                    price_usd, _ = parse_price_usd(listing.get("price", ""))
                    listing["price_usd"] = price_usd
                    dup = is_duplicate(listing.get("title", ""), price_usd, recent_alerts)
                    if dup:
                        dropped["duplicate_flagged"] = dropped.get("duplicate_flagged", 0) + 1
                        listing["duplicate_of"] = dup
                        log.info(f"   possible duplicate of [{dup.get('platform')}] {dup.get('title', '')[:50]}: "
                                 f"{listing.get('title', '')[:50]}")
                    recent_alerts.append({"title": listing.get("title", ""), "price_usd": price_usd,
                                          "platform": listing.get("platform", ""), "url": listing.get("url", "")})

                for reason, count in dropped.items():
                    log.info(f"   Filtered out {count} {DROP_REASON_LABELS.get(reason, reason)} listing(s) from {scraper.name}")
                log.info(f"   ✅ {len(new_listings)} NEW listings from {scraper.name}"
                         + (f", {len(drops)} price drop(s)" if drops else ""))
                return {
                    "name": scraper.name, "new": new_listings, "drops": drops,
                    "status": len(new_listings), "raw": fetched,
                    "duration": (datetime.utcnow() - started).total_seconds(),
                }

            except asyncio.TimeoutError:
                log.warning(
                    f"   ⏱️ Timeout in {scraper.name} after {Config.SCRAPER_TIMEOUT_SEC}s "
                    f"(attempt {attempt}/{retries + 1})"
                )
            except Exception as e:
                log.error(f"   ❌ Error in {scraper.name} (attempt {attempt}/{retries + 1}): {e}", exc_info=True)

            if attempt <= retries:
                await asyncio.sleep(min(2 * attempt, 5))

        return {"name": scraper.name, "new": [], "drops": [], "status": "ERROR", "raw": 0, "duration": 0}


async def _watchdog(scrapers: list, notifier: TelegramNotifier) -> None:
    """Warn on Telegram when a configured scraper keeps fetching nothing or
    keeps erroring — this is exactly how Reverb/Craigslist/Subito silently
    died for months before Prompt 13."""
    streaks = status_tracker.get_streaks()
    threshold = max(2, Config.WATCHDOG_ZERO_STREAK)
    alerts = []
    for scraper in scrapers:
        if not scraper.is_configured():
            continue
        st = streaks.get(scraper.name)
        if not st:
            continue
        err, zero = st["error"], st["zero"]
        if err >= 2 and (err == 2 or err % 10 == 0):
            alerts.append(f"{scraper.name}: eroare în {err} cicluri consecutive")
        elif zero >= threshold and (zero == threshold or zero % 10 == 0):
            alerts.append(f"{scraper.name}: 0 rezultate brute în {zero} cicluri consecutive")
    if alerts:
        log.warning("WATCHDOG: " + "; ".join(alerts))
        try:
            await notifier.send_watchdog(alerts)
        except Exception as e:
            log.error(f"Watchdog Telegram error: {e}")


async def _deliver(notifier: TelegramNotifier, db: Database, price_tracker: PriceTracker, listings: list) -> int:
    """Send listings; mark as seen/alerted ONLY the delivered ones."""
    for listing in listings:
        listing["price_context"] = price_tracker.record_listing(listing)
    delivered = await notifier.send_listings(listings)
    for listing in delivered:
        db.mark_seen(listing["id"], listing)
        db.record_alert(listing, listing.get("price_usd"))
    if len(delivered) < len(listings):
        log.warning(f"   {len(listings) - len(delivered)} listing(s) not delivered — kept unseen for the next cycle")
    return len(delivered)


async def run_search_cycle():
    """Run one full search cycle across ALL platforms. Concurrent triggers
    queue behind the lock (a scheduled run is never lost)."""
    if search_cycle_lock.locked():
        log.info("Search cycle already running — this trigger will wait for it to finish.")

    async with search_cycle_lock:
        log.info("=" * 80)
        log.info(f"🎻 STARTING ZETA VIOLIN HUNT at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        status_tracker.start_cycle()
        log.info("=" * 80)

        if not Config.EGRESS_COUNTRY:
            await detect_egress()  # retry — Yahoo JP depends on it
        await refresh_rates()
        db = Database()
        notifier = TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
        price_tracker = PriceTracker()
        scrapers = build_scrapers()

        total_sent = 0
        all_drops = []
        platform_stats = {}
        sources_ok = sources_error = sources_zero = 0

        try:
            semaphore = asyncio.Semaphore(max(1, Config.SCRAPER_CONCURRENCY))
            tasks = [
                asyncio.create_task(_run_scraper_with_resilience(scraper, db, price_tracker, semaphore))
                for scraper in scrapers
            ]

            for task in asyncio.as_completed(tasks):
                result = await task
                name, new_listings, drops, status = result["name"], result["new"], result["drops"], result["status"]
                all_drops.extend(drops)
                platform_stats[name] = status
                if status == "ERROR":
                    sources_error += 1
                elif result["raw"] == 0:
                    sources_zero += 1
                elif result["raw"] > 0:
                    sources_ok += 1

                status_tracker.record_scraper(
                    name,
                    raw=result["raw"],
                    new=len(new_listings),
                    error="ERROR" if status == "ERROR" else "",
                    duration=result["duration"],
                )

                # Send immediately per-platform so results are not lost on container restarts.
                if new_listings:
                    try:
                        log.info(f"📬 Sending {len(new_listings)} listing(s) from {name} to Telegram...")
                        total_sent += await _deliver(notifier, db, price_tracker, new_listings)
                    except Exception as e:
                        log.error(f"Telegram send error for {name}: {e}", exc_info=True)

            if all_drops:
                try:
                    await notifier.send_price_drops(all_drops)
                except Exception as e:
                    log.error(f"Telegram price-drop send error: {e}")

            if not total_sent and not all_drops:
                log.info("\n✅ No new listings found this cycle.")
                try:
                    await notifier.send_no_changes(sources_ok, sources_error, sources_zero)
                except Exception as e:
                    log.error(f"Telegram no-changes send error: {e}")
        finally:
            price_tracker.close()
            db.close()
            log.info("=" * 80)
            log.info("SEARCH CYCLE COMPLETE")
            log.info("=" * 80)
            for platform, count in platform_stats.items():
                log.info(f"  {platform}: {count}")
            log.info("=" * 80)
            status_tracker.end_cycle(total_sent=total_sent, total_ai_rejected=sources_error)
            try:
                await _watchdog(scrapers, notifier)
            except Exception as e:
                log.error(f"Watchdog error: {e}")
            try:
                await _maybe_weekly_digest(notifier)
            except Exception as e:
                log.error(f"Weekly digest error: {e}")
        return total_sent


def _local_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(Config.SEARCH_TIMEZONE))
    except Exception:
        return datetime.utcnow()


async def _maybe_weekly_digest(notifier: TelegramNotifier) -> None:
    """Send the weekly overview once, right after the search of the digest day
    (so listing_activity is fully updated)."""
    if not Config.WEEKLY_DIGEST_DAY:
        return
    now = _local_now()
    if now.strftime("%a").lower()[:3] != Config.WEEKLY_DIGEST_DAY[:3]:
        return
    today = now.strftime("%Y-%m-%d")
    conn = connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        row = conn.execute("SELECT value FROM kv WHERE key = 'digest_sent'").fetchone()
        if row and row[0] == today:
            return
        conn.execute("INSERT OR REPLACE INTO kv VALUES ('digest_sent', ?, ?)", (today, datetime.utcnow().isoformat()))
        conn.commit()
    finally:
        conn.close()
    await send_weekly_digest(notifier)


async def send_weekly_digest(notifier: TelegramNotifier = None):
    """Overview of live Zeta listings, disappeared ones and price stats."""
    notifier = notifier or TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
    db = Database()
    try:
        active = db.active_listings(days=3)
        gone = db.gone_since(days_active=3, days_gone=14)
    finally:
        db.close()
    stats = status_tracker.get_status().get("price_stats") or {}
    log.info(f"📰 Weekly digest: {len(active)} active, {len(gone)} gone")
    await notifier.send_digest(active, gone, stats)


def _authorized(request) -> bool:
    if not Config.SEARCH_TOKEN:
        return True
    supplied = request.headers.get("X-Search-Token") or request.query.get("token", "")
    return supplied == Config.SEARCH_TOKEN


async def handle_search(request):
    """HTTP endpoint to trigger manual search (protected by SEARCH_TOKEN if set)."""
    if not _authorized(request):
        return web.json_response({"status": "error", "message": "unauthorized"}, status=401)
    if search_cycle_lock.locked():
        return web.json_response({"status": "busy", "message": "A search cycle is already running."}, status=409)
    try:
        count = await run_search_cycle()
        return web.json_response({
            "status": "success",
            "message": f"Search completed. Found {count} new listings.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        log.error(f"Manual search error: {e}", exc_info=True)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_health(request):
    return web.json_response({"status": "healthy"})


async def handle_status(request):
    if not _authorized(request):
        return web.json_response({"status": "error", "message": "unauthorized"}, status=401)
    try:
        return web.json_response(status_tracker.get_status())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def detect_egress() -> None:
    """Log where the container's traffic comes out (IP + country) and store the
    country in Config.EGRESS_COUNTRY. Decides whether US-only / non-EEA
    scrapers can run without a proxy. Never fatal."""
    for url in ("https://ipapi.co/json/", "https://ipinfo.io/json"):
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url, headers={"User-Agent": "ZetaViolinHunter/1.0"})
                if resp.status_code != 200:
                    continue
                data = resp.json()
                country = str(data.get("country_code") or data.get("country") or "").upper()
                city = data.get("city", "")
                ip = data.get("ip", "")
                if country:
                    Config.EGRESS_COUNTRY = country
                    log.info(f"🌍 Egress IP {ip} — {city}, {country}"
                             + (" → US-only scrapers enabled" if country == "US" else
                                " → US-only sites (Guitar Center, OfferUp, Mercari US, Etsy, Facebook) need US_PROXY_URL"))
                    return
        except Exception as e:
            log.debug(f"egress detection via {url} failed: {e}")
    log.warning("🌍 Could not detect egress country — will retry at the next cycle")


def _parse_hours(raw: str) -> str:
    hours = sorted({int(h) for h in raw.split(",") if h.strip().isdigit() and 0 <= int(h) <= 23})
    return ",".join(str(h) for h in hours) or "12"


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name()} crashed: {exc!r}", exc_info=exc)


async def _startup_cycle() -> None:
    """Run a cycle at container start unless one ran recently (deploys and
    crash loops must not re-scrape everything and re-spend quotas)."""
    last = status_tracker.last_cycle_started()
    if last and datetime.utcnow() - last < timedelta(hours=Config.STARTUP_RUN_GUARD_HOURS):
        log.info(f"⏭️ Startup cycle skipped — last cycle started {last:%Y-%m-%d %H:%M} UTC "
                 f"(< {Config.STARTUP_RUN_GUARD_HOURS:g}h ago). Next scheduled run stands.")
        return
    await run_search_cycle()


async def main():
    log.info("🎻 Zeta Violin Hunter starting up...")
    log.info(f"   DB: {Config.DB_PATH} | US proxy: {'yes' if Config.US_PROXY_URL else 'no'} | "
             f"Brave: {'yes' if Config.BRAVE_API_KEY else 'no'} | condition: {Config.CONDITION}")
    await detect_egress()
    if Config.STARTUP_DIAGNOSTICS:
        asyncio.create_task(run_reachability(), name="reachability").add_done_callback(_log_task_exception)

    app = web.Application()
    app.router.add_post('/search', handle_search)
    app.router.add_get('/health', handle_health)
    app.router.add_get('/status', handle_status)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    log.info(f"🌐 HTTP server started on port {Config.PORT}")
    log.info("   POST /search - Trigger manual search" + (" (X-Search-Token required)" if Config.SEARCH_TOKEN else ""))
    log.info("   GET /health - Health check")
    log.info("   GET /status - Detailed status dashboard")

    # First search in background (guarded) so the health endpoint is available immediately.
    asyncio.create_task(_startup_cycle(), name="startup-cycle").add_done_callback(_log_task_exception)

    hours = _parse_hours(Config.SEARCH_HOURS)
    scheduler = AsyncIOScheduler()
    # misfire_grace_time: if the loop is busy at 12:00:00 the run must still happen
    # (default grace is 1 s, which silently DROPS the only run of the day).
    job = scheduler.add_job(run_search_cycle, "cron", hour=hours, minute=0, timezone=Config.SEARCH_TIMEZONE,
                            misfire_grace_time=3600, coalesce=True, max_instances=1)
    scheduler.start()
    log.info(f"⏰ Scheduled daily at {hours}:00 {Config.SEARCH_TIMEZONE} "
             f"(next run {job.next_run_time:%Y-%m-%d %H:%M %Z}); "
             f"Google {Config.GOOGLE_QUERIES_PER_RUN}/run (≤{Config.GOOGLE_DAILY_QUOTA}/day), "
             f"Brave {Config.BRAVE_QUERIES_PER_RUN}/run"
             + (f"; weekly digest after the {Config.WEEKLY_DIGEST_DAY} search" if Config.WEEKLY_DIGEST_DAY else ""))

    # Telegram commands (/cauta /status /active) via long polling
    commands = TelegramCommands(
        Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID,
        on_search=run_search_cycle,
        is_searching=search_cycle_lock.locked,
        status_tracker=status_tracker,
        database_factory=Database,
        notifier=TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID),
    )
    asyncio.create_task(commands.run(), name="telegram-commands").add_done_callback(_log_task_exception)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent stopped.")
        scheduler.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
