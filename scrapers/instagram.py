"""
Instagram scraper — #zetaviolin, #electricviolin hashtags.
Note: Instagram requires authentication. This is a placeholder.
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class InstagramScraper(BaseScraper):
    name = "Instagram"

    HASHTAGS = [
        "zetaviolin", "electricviolin", "zeta", "violinist",
        "musicgear", "forsale", "classifieds",
    ]

    async def search(self) -> list:
        results = []
        # Instagram requires authentication and has strict ToS
        # This would need Instagram API or unofficial scraping
        # For now, returning empty results
        log.warning("Instagram scraping requires authentication - skipping")
        return results
