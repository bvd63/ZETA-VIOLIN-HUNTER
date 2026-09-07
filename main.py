"""
Zeta Violin Hunter Agent
Searches globally for Zeta electric violin listings and sends Telegram alerts.
"""

import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from aiohttp import web

from database import Database
from notifier import TelegramNotifier
from filters import classify

from scrapers.reverb import ReverbScraper
from scrapers.ebay import EbayScraper
from scrapers.google import GoogleScraper
from scrapers.brave import BraveScraper
from scrapers.craigslist import CraigslistScraper
from scrapers.shopgoodwill import ShopGoodwillScraper
from scrapers.hibid import HiBidScraper
from scrapers.kijiji import KijijiScraper
from scrapers.marktplaats import MarktplaatsScraper
from scrapers.willhaben import WillhabenScraper
from scrapers.schibsted import SchibstedScraper
from scrapers.gumtree import GumtreeScraper
from scrapers.olx import OlxScraper
from scrapers.subito import SubitoScraper
from scrapers.mercari_jp import MercariJPScraper
from scrapers.guitar_center import GuitarCenterScraper
from scrapers.reddit_scraper import RedditScraper
from scrapers.facebook_marketplace import FacebookMarketplaceScraper

from price_tracker import PriceTracker
from status_tracker import StatusTracker
from config import Config

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
}


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
        MercariJPScraper(),
        GuitarCenterScraper(),          # runs only when US_PROXY_URL is set
        FacebookMarketplaceScraper(),   # Playwright; uses US_PROXY_URL when set
        RedditScraper(),
        # Disabled (see CLAUDE.md §2): Kleinanzeigen, Wallapop, Leboncoin,
        # Maestronet, Violinist.com, Audiofanzine — covered by Google/Brave.
    ]


async def _run_scraper_with_resilience(scraper, db: Database, price_tracker: PriceTracker,
                                       semaphore: asyncio.Semaphore) -> dict:
    """Run one scraper with timeout + retry, filter, dedup and detect price drops.
    Returns {name, new, drops, status, raw}; status is int (== len(new)) or 'ERROR'."""
    async with semaphore:
        retries = max(0, Config.SCRAPER_RETRIES)
        for attempt in range(1, retries + 2):
            started = datetime.utcnow()
            try:
                log.info(f"🔍 Searching: {scraper.name} (attempt {attempt}/{retries + 1})")
                listings = await asyncio.wait_for(
                    scraper.search(), timeout=max(1, Config.SCRAPER_TIMEOUT_SEC)
                )
                fetched = max(int(getattr(scraper, "fetched", 0) or 0), len(listings))
                log.info(f"   Found {len(listings)} candidate listings from {scraper.name} ({fetched} items fetched)")

                new_listings, drops, dropped = [], [], {}
                for listing in listings:
                    reason = classify(listing)
                    if reason:
                        dropped[reason] = dropped.get(reason, 0) + 1
                        log.debug(f"   drop[{reason}] {listing.get('title', '')[:70]}")
                        continue
                    if db.is_seen(listing["id"]):
                        info = price_tracker.update_price(listing)
                        if info:
                            drops.append((listing, info))
                        continue
                    db.mark_seen(listing["id"], listing)
                    new_listings.append(listing)

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
                log.error(f"   ❌ Error in {scraper.name} (attempt {attempt}/{retries + 1}): {e}")

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


async def run_search_cycle():
    """Run one full search cycle across ALL platforms."""
    if search_cycle_lock.locked():
        log.warning("Search cycle already running — skipping overlapping trigger.")
        return 0

    async with search_cycle_lock:
        log.info("=" * 80)
        log.info(f"🎻 STARTING ZETA VIOLIN HUNT at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        status_tracker.start_cycle()
        log.info("=" * 80)

        db = Database()
        notifier = TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
        price_tracker = PriceTracker()
        scrapers = build_scrapers()

        all_new_listings, all_drops = [], []
        platform_stats = {}
        sent_any = False

        semaphore = asyncio.Semaphore(max(1, Config.SCRAPER_CONCURRENCY))
        tasks = [
            asyncio.create_task(_run_scraper_with_resilience(scraper, db, price_tracker, semaphore))
            for scraper in scrapers
        ]

        for task in asyncio.as_completed(tasks):
            result = await task
            name, new_listings, drops, status = result["name"], result["new"], result["drops"], result["status"]
            all_new_listings.extend(new_listings)
            all_drops.extend(drops)
            platform_stats[name] = status

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
                    for listing in new_listings:
                        listing["price_context"] = price_tracker.record_listing(listing)
                    log.info(f"📬 Sending {len(new_listings)} listing(s) from {name} to Telegram...")
                    await notifier.send_listings(new_listings)
                    sent_any = True
                except Exception as e:
                    log.error(f"Telegram send error for {name}: {e}")

        if all_drops:
            try:
                await notifier.send_price_drops(all_drops)
            except Exception as e:
                log.error(f"Telegram price-drop send error: {e}")

        if not all_new_listings and not all_drops:
            log.info("\n✅ No new listings found this cycle.")
            await notifier.send_no_changes()
        elif all_new_listings and not sent_any:
            log.info(f"\n📬 Sending {len(all_new_listings)} new listings to Telegram...")
            await notifier.send_listings(all_new_listings)

        price_tracker.close()
        db.close()
        log.info("=" * 80)
        log.info("SEARCH CYCLE COMPLETE")
        log.info("=" * 80)
        for platform, count in platform_stats.items():
            log.info(f"  {platform}: {count}")
        log.info("=" * 80)

        status_tracker.end_cycle(
            total_sent=len(all_new_listings),
            total_ai_rejected=sum(1 for s in platform_stats.values() if s == "ERROR"),
        )
        await _watchdog(scrapers, notifier)
        return len(all_new_listings)


def _authorized(request) -> bool:
    if not Config.SEARCH_TOKEN:
        return True
    supplied = request.headers.get("X-Search-Token") or request.query.get("token", "")
    return supplied == Config.SEARCH_TOKEN


async def handle_search(request):
    """HTTP endpoint to trigger manual search (protected by SEARCH_TOKEN if set)."""
    if not _authorized(request):
        return web.json_response({"status": "error", "message": "unauthorized"}, status=401)
    try:
        count = await run_search_cycle()
        return web.json_response({
            "status": "success",
            "message": f"Search completed. Found {count} new listings.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        log.error(f"Manual search error: {e}")
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
    country in Config.EGRESS_COUNTRY. Decides whether US-only scrapers can run
    without a proxy. Never fatal."""
    import httpx
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
    log.warning("🌍 Could not detect egress country — assuming non-US")


def _parse_hours(raw: str) -> str:
    hours = sorted({int(h) for h in raw.split(",") if h.strip().isdigit() and 0 <= int(h) <= 23})
    return ",".join(str(h) for h in hours) or "9,21"


async def main():
    log.info("🎻 Zeta Violin Hunter starting up...")
    log.info(f"   DB: {Config.DB_PATH} | US proxy: {'yes' if Config.US_PROXY_URL else 'no'} | "
             f"Brave: {'yes' if Config.BRAVE_API_KEY else 'no'} | condition: {Config.CONDITION}")
    await detect_egress()

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

    # Run first search in background so health endpoint is available immediately.
    asyncio.create_task(run_search_cycle())

    hours = _parse_hours(Config.SEARCH_HOURS)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_search_cycle, "cron", hour=hours, minute=0, timezone="UTC")
    scheduler.start()
    log.info(f"⏰ Scheduled to run daily at {hours} UTC")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent stopped.")
        scheduler.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
