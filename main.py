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

# Import ALL scrapers
from scrapers.reverb import ReverbScraper
from scrapers.ebay import EbayScraper
from scrapers.google import GoogleScraper
from scrapers.craigslist import CraigslistScraper
from scrapers.facebook_marketplace import FacebookMarketplaceScraper
from scrapers.leboncoin import LeboncoinScraper
from scrapers.kleinanzeigen import KleinanzeigenScraper
from scrapers.wallapop import WallapopScraper
from scrapers.marktplaats import MarktplaatsScraper
from scrapers.subito import SubitoScraper
from scrapers.willhaben import WillhabenScraper
from scrapers.ricardo import RicardoScraper
from scrapers.blocket import BlocketScraper
from scrapers.finn import FinnScraper
from scrapers.tori import ToriScraper
from scrapers.allegro import AllegroPLScraper
from scrapers.gumtree import GumtreeScraper
from scrapers.kijiji import KijijiScraper
from scrapers.mercari import MercariScraper
from scrapers.yahoo_auctions_japan import YahooAuctionsJapanScraper
from scrapers.rakuten import RakutenScraper
from scrapers.carousell import CarousellScraper
from scrapers.com_58 import Com58Scraper
from scrapers.douban import DoubanScraper
from scrapers.tarisio import TarisioScraper
from scrapers.maestronet import MaestronetScraper
from scrapers.audiofanzine import AudiofanzineScraper
from scrapers.zikinf import ZikinfScraper
from scrapers.mercatinomusicale import MercatinomusicaleScraper
from scrapers.sweetwater import SweetwaterScraper
from scrapers.guitar_center import GuitarCenterScraper
from scrapers.thomann import ThomannScraper
from scrapers.gear4music import Gear4musicScraper
from scrapers.chicago_music_exchange import ChicagoMusicExchangeScraper
from scrapers.vintage_king import VintageKingScraper
from scrapers.musicians_friend import MusiciansFriendScraper
from scrapers.catawiki import CatawikiScraper
from scrapers.invaluable import InvaluableScraper
from scrapers.hibid import HibidScraper
from scrapers.bonhams import BonhamsScraper
from scrapers.sothebys import SothebysScaper
from scrapers.christies import ChristiesScraper
from scrapers.reddit import RedditScraper
from scrapers.instagram import InstagramScraper
from scrapers.tiktok import TikTokScraper
from scrapers.youtube import YouTubeScraper
from scrapers.discord import DiscordScraper
from scrapers.telegram import TelegramScraper
from scrapers.avito import AvitoScraper
from scrapers.yandex_market import YandexMarketScraper
from scrapers.mercadolibre import MercadolibreScraper
from scrapers.dafiti import DafitiScraper
from scrapers.tokopedia import TokopediaScraper
from scrapers.shopee import ShopeeScraper
from scrapers.lazada import LazadaScraper
from scrapers.zeta_music_official import ZetaMusicOfficialScraper

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


def build_scrapers() -> list:
    return [
        # Core platforms
        ReverbScraper(),
        EbayScraper(Config.EBAY_APP_ID),
        GoogleScraper(Config.GOOGLE_API_KEY, Config.GOOGLE_CSE_ID),
        CraigslistScraper(),
        FacebookMarketplaceScraper(),

        # European classifieds
        LeboncoinScraper(),
        KleinanzeigenScraper(),
        WallapopScraper(),
        MarktplaatsScraper(),
        SubitoScraper(),
        WillhabenScraper(),
        RicardoScraper(),
        BlocketScraper(),
        FinnScraper(),
        ToriScraper(),
        AllegroPLScraper(),
        GumtreeScraper(),
        KijijiScraper(),

        # Asia
        MercariScraper(),
        YahooAuctionsJapanScraper(),
        RakutenScraper(),
        CarousellScraper(),
        Com58Scraper(),
        DoubanScraper(),

        # Music-specific
        TarisioScraper(),
        MaestronetScraper(),
        AudiofanzineScraper(),
        ZikinfScraper(),
        MercatinomusicaleScraper(),
        SweetwaterScraper(),
        GuitarCenterScraper(),
        ThomannScraper(),
        Gear4musicScraper(),
        ChicagoMusicExchangeScraper(),
        VintageKingScraper(),
        MusiciansFriendScraper(),

        # Auctions
        CatawikiScraper(),
        InvaluableScraper(),
        HibidScraper(),
        BonhamsScraper(),
        SothebysScaper(),
        ChristiesScraper(),

        # Communities
        RedditScraper(),
        InstagramScraper(),
        TikTokScraper(),
        YouTubeScraper(),
        DiscordScraper(),
        TelegramScraper(),

        # Regional
        AvitoScraper(),
        YandexMarketScraper(),
        MercadolibreScraper(),
        DafitiScraper(),
        TokopediaScraper(),
        ShopeeScraper(),
        LazadaScraper(),

        # Official
        ZetaMusicOfficialScraper(),
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
                for listing in listings:
                    if not db.is_seen(listing["id"]):
                        db.mark_seen(listing["id"], listing)
                        new_listings.append(listing)

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
