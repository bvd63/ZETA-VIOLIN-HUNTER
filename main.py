"""
Zeta Violin Hunter Agent
Searches globally for Zeta electric violin listings and sends Telegram alerts.
"""

import asyncio
import logging
import re
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from aiohttp import web

from database import Database
from notifier import TelegramNotifier

from scrapers.reverb import ReverbScraper
from scrapers.ebay import EbayScraper
from scrapers.google import GoogleScraper
from scrapers.craigslist import CraigslistScraper
from scrapers.subito import SubitoScraper
from scrapers.kleinanzeigen import KleinanzeigenScraper
from scrapers.wallapop import WallapopScraper
from scrapers.leboncoin import LeboncoinScraper
from scrapers.mercari_jp import MercariJPScraper
from scrapers.reddit_scraper import RedditScraper

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


VIOLIN_TERMS = {
    "violin", "violins", "violino", "violon", "viola", "fiddle",
    "geige", "skrzypce", "violonul", "violinul", "violines", "violoniste",
}

ZETA_MODEL_TERMS = {
    "strados",
    "jazz fusion",
    "jazz modern",
    "jazz classic",
    "strados legacy",
    "acoustic-pro",
    "acoustic pro",
    "jv44",
    "jv45",
    "sv24",
    "sv25",
    "sv43",
    "ev25",
    "ev44",
    "cv44",
    "jlp",
    "jean-luc ponty",
}

NOISE_TERMS = {
    "arcteryx", "arc'teryx", "jacket", "jackets", "shell", "hardshell",
    "coat", "hoodie", "pants", "backpack", "ski", "snowboard",
}

EXCLUDE_LISTING_TERMS = {
    "wanted", "wtb", "looking for", "part only", "parts only", "for parts",
    "repair", "broken", "defect", "not working", "case only", "bow only",
    "bridge only", "pickup only case", "cover only", "gig bag only",
}

MIN_SCORE_BY_PLATFORM = {
    "reverb": 2,
    "ebay": 2,
    "facebook marketplace": 3,
    "subito": 3,
    "kleinanzeigen": 3,
    "leboncoin": 3,
    "mercatinomusicale": 2,
    "audiofanzine": 2,
    "maestronet": 2,
    "yahoo auctions japan": 2,
}


def _is_strict_zeta_violin(listing: dict) -> bool:
    """Allow only Zeta violin-family listings, including explicit Zeta model names."""
    text = " ".join([
        str(listing.get("title", "")),
        str(listing.get("description", "")),
        str(listing.get("platform", "")),
    ]).lower()

    has_zeta = "zeta" in text or "zetta" in text
    if not has_zeta:
        return False

    has_model_term = any(term in text for term in ZETA_MODEL_TERMS)
    if has_model_term:
        return True

    tokens = set(re.findall(r"[a-zA-Z\u00C0-\u024F\u0400-\u04FF]+", text))
    has_violin_term = any(term in tokens for term in VIOLIN_TERMS)
    return has_violin_term


def _has_noise_terms(listing: dict) -> bool:
    text = " ".join([
        str(listing.get("title", "")),
        str(listing.get("description", "")),
    ]).lower()
    return any(term in text for term in NOISE_TERMS)


def _is_excluded_listing_intent(listing: dict) -> bool:
    text = " ".join([
        str(listing.get("title", "")),
        str(listing.get("description", "")),
    ]).lower()
    return any(term in text for term in EXCLUDE_LISTING_TERMS)


def _is_valid_listing_url(url: str) -> bool:
    if not url:
        return False
    raw = str(url).strip().lower()
    if raw.startswith("//"):
        raw = "https:" + raw
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return False

    # Drop obvious non-listing pages.
    bad_fragments = ["/search", "?q=", "/category", "/categories", "/forum", "/help", "/about"]
    if any(fragment in raw for fragment in bad_fragments):
        return False

    return True


def _passes_platform_score(listing: dict) -> bool:
    platform = str(listing.get("platform", "")).lower()
    score = int(listing.get("relevance_score", 0) or 0)

    threshold = 2
    for key, value in MIN_SCORE_BY_PLATFORM.items():
        if key in platform:
            threshold = value
            break

    return score >= threshold


def build_scrapers() -> list:
    return [
        ReverbScraper(),
        EbayScraper(Config.EBAY_CLIENT_ID, Config.EBAY_CLIENT_SECRET),
        GoogleScraper(Config.GOOGLE_API_KEY, Config.GOOGLE_CSE_ID),
        CraigslistScraper(),
        SubitoScraper(),
        KleinanzeigenScraper(),
        WallapopScraper(),
        LeboncoinScraper(),
        MercariJPScraper(),
        RedditScraper(),
    ]


async def _run_scraper_with_resilience(scraper, db: Database, semaphore: asyncio.Semaphore):
    async with semaphore:
        retries = max(0, Config.SCRAPER_RETRIES)
        for attempt in range(1, retries + 2):
            try:
                log.info(f"🔍 Searching: {scraper.name} (attempt {attempt}/{retries + 1})")
                listings = await asyncio.wait_for(
                    scraper.search(), timeout=max(1, Config.SCRAPER_TIMEOUT_SEC)
                )
                log.info(f"   Found {len(listings)} raw listings from {scraper.name}")

                new_listings = []
                dropped_non_zeta = 0
                dropped_noise = 0
                dropped_intent = 0
                dropped_url = 0
                dropped_score = 0
                for listing in listings:
                    if not _is_strict_zeta_violin(listing):
                        dropped_non_zeta += 1
                        continue
                    if _has_noise_terms(listing):
                        dropped_noise += 1
                        continue
                    if _is_excluded_listing_intent(listing):
                        dropped_intent += 1
                        continue
                    if not _is_valid_listing_url(str(listing.get("url", ""))):
                        dropped_url += 1
                        continue
                    if not _passes_platform_score(listing):
                        dropped_score += 1
                        continue
                    if not db.is_seen(listing["id"]):
                        db.mark_seen(listing["id"], listing)
                        new_listings.append(listing)

                if dropped_non_zeta:
                    log.info(f"   Filtered out {dropped_non_zeta} non-Zeta-violin listing(s) from {scraper.name}")
                if dropped_noise:
                    log.info(f"   Filtered out {dropped_noise} noise listing(s) from {scraper.name}")
                if dropped_intent:
                    log.info(f"   Filtered out {dropped_intent} non-sale listing(s) from {scraper.name}")
                if dropped_url:
                    log.info(f"   Filtered out {dropped_url} invalid-url listing(s) from {scraper.name}")
                if dropped_score:
                    log.info(f"   Filtered out {dropped_score} low-score listing(s) from {scraper.name}")

                log.info(f"   ✅ {len(new_listings)} NEW listings from {scraper.name}")
                return scraper.name, new_listings, len(new_listings)

            except asyncio.TimeoutError:
                log.warning(
                    f"   ⏱️ Timeout in {scraper.name} after {Config.SCRAPER_TIMEOUT_SEC}s "
                    f"(attempt {attempt}/{retries + 1})"
                )
            except Exception as e:
                log.error(f"   ❌ Error in {scraper.name} (attempt {attempt}/{retries + 1}): {e}")

            if attempt <= retries:
                await asyncio.sleep(min(2 * attempt, 5))

        return scraper.name, [], "ERROR"


async def run_search_cycle():
    """Run one full search cycle across ALL platforms."""
    if search_cycle_lock.locked():
        log.warning("Search cycle already running — skipping overlapping trigger.")
        return 0

    async with search_cycle_lock:
        log.info("=" * 80)
        log.info(f"🎻 STARTING ZETA VIOLIN HUNT at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log.info("=" * 80)

        db = Database()
        notifier = TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)

        all_new_listings = []
        platform_stats = {}
        sent_any = False

        semaphore = asyncio.Semaphore(max(1, Config.SCRAPER_CONCURRENCY))
        tasks = [
            asyncio.create_task(_run_scraper_with_resilience(scraper, db, semaphore))
            for scraper in build_scrapers()
        ]

        for task in asyncio.as_completed(tasks):
            platform_name, new_listings, status = await task
            all_new_listings.extend(new_listings)
            platform_stats[platform_name] = status

            # Send immediately per-platform so results are not lost on container restarts.
            if new_listings:
                try:
                    log.info(f"📬 Sending {len(new_listings)} new listing(s) from {platform_name} to Telegram...")
                    await notifier.send_listings(new_listings)
                    sent_any = True
                except Exception as e:
                    log.error(f"Telegram send error for {platform_name}: {e}")

        if not all_new_listings:
            log.info("\n✅ No new listings found this cycle.")
            await notifier.send_no_changes()
        elif not sent_any:
            # Fallback: if incremental sends were skipped for any reason, send all once.
            log.info(f"\n📬 Sending {len(all_new_listings)} new listings to Telegram...")
            await notifier.send_listings(all_new_listings)

        db.close()
        log.info("=" * 80)
        log.info("SEARCH CYCLE COMPLETE")
        log.info("=" * 80)
        for platform, count in platform_stats.items():
            log.info(f"  {platform}: {count}")
        log.info("=" * 80)

        return len(all_new_listings)


async def handle_search(request):
    """HTTP endpoint to trigger manual search."""
    try:
        count = await run_search_cycle()
        return web.json_response({
            "status": "success",
            "message": f"Search completed. Found {count} new listings.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        log.error(f"Manual search error: {e}")
        return web.json_response({
            "status": "error",
            "message": str(e)
        }, status=500)


async def handle_health(request):
    """Health check endpoint."""
    return web.json_response({"status": "healthy"})


async def main():
    log.info("🎻 Zeta Violin Hunter starting up...")

    # Start HTTP server
    app = web.Application()
    app.router.add_post('/search', handle_search)
    app.router.add_get('/health', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    log.info("🌐 HTTP server started on port 8080")
    log.info("   POST /search - Trigger manual search")
    log.info("   GET /health - Health check")

    # Run first search in background so health endpoint is available immediately.
    asyncio.create_task(run_search_cycle())

    # Then schedule daily at configured time
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_search_cycle,
        "cron",
        hour=Config.SEARCH_HOUR,
        minute=0,
        timezone="UTC"
    )
    scheduler.start()
    log.info(f"⏰ Scheduled to run daily at {Config.SEARCH_HOUR}:00 UTC")

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent stopped.")
        scheduler.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
