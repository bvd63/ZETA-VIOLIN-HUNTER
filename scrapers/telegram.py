"""
Telegram scraper — Music groups.
Note: Telegram requires bot token. This is a placeholder.
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class TelegramScraper(BaseScraper):
    name = "Telegram"

    async def search(self) -> list:
        results = []
        # Telegram requires bot token and has strict ToS
        # This would need Telethon or python-telegram-bot
        # For now, returning empty results
        log.warning("Telegram scraping requires bot token - skipping")
        return results
