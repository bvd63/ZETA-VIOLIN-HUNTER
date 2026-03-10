"""
YouTube scraper — Comments on Zeta violin videos.
Note: YouTube requires API key. This is a placeholder.
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class YouTubeScraper(BaseScraper):
    name = "YouTube"

    async def search(self) -> list:
        results = []
        # YouTube requires API key and has strict ToS
        # This would need YouTube Data API
        # For now, returning empty results
        log.warning("YouTube scraping requires API key - skipping")
        return results
