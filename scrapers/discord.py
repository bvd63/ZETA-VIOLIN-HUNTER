"""
Discord scraper — Music communities.
Note: Discord requires bot token. This is a placeholder.
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class DiscordScraper(BaseScraper):
    name = "Discord"

    async def search(self) -> list:
        results = []
        # Discord requires bot token and has strict ToS
        # This would need Discord.py library
        # For now, returning empty results
        log.warning("Discord scraping requires bot token - skipping")
        return results
