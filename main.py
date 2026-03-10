"""
Zeta Violin Hunter Agent
Searches globally for Zeta electric violin listings and sends Telegram alerts.
"""

import asyncio
import logging
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

from database import Database
from notifier import TelegramNotifier
from scrapers.reverb import ReverbScraper
from scrapers.ebay import EbayScraper
from scrapers.google import GoogleScraper
from scrapers.craigslist import CraigslistScraper
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)


async def run_search_cycle():
    """Run one full search cycle across all platforms."""
    log.info("=" * 60)
    log.info(f"🎻 Starting Zeta search cycle at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info("=" * 60)

    db = Database()
    notifier = TelegramNotifier(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)

    scrapers = [
        ReverbScraper(),
        EbayScraper(Config.EBAY_APP_ID),
        GoogleScraper(Config.GOOGLE_API_KEY, Config.GOOGLE_CSE_ID),
        CraigslistScraper(),
    ]

    all_new_listings = []

    for scraper in scrapers:
        try:
            log.info(f"🔍 Searching: {scraper.name}")
            listings = await scraper.search()
            log.info(f"   Found {len(listings)} raw listings from {scraper.name}")

            new_listings = []
            for listing in listings:
                if not db.is_seen(listing["id"]):
                    db.mark_seen(listing["id"], listing)
                    new_listings.append(listing)

            log.info(f"   ✅ {len(new_listings)} NEW listings from {scraper.name}")
            all_new_listings.extend(new_listings)

        except Exception as e:
            log.error(f"   ❌ Error in {scraper.name}: {e}")

    if all_new_listings:
        log.info(f"\n📬 Sending {len(all_new_listings)} new listings to Telegram...")
        await notifier.send_listings(all_new_listings)
    else:
        log.info("\n✅ No new listings found this cycle.")
        await notifier.send_no_changes()

    db.close()
    log.info("=" * 60)
    log.info("Search cycle complete.")
    log.info("=" * 60)


async def main():
    log.info("🎻 Zeta Violin Hunter starting up...")

    # Run immediately on startup
    await run_search_cycle()

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


if __name__ == "__main__":
    asyncio.run(main())
