"""
TikTok scraper — #zetaviolin, #electricviolin hashtags.
Note: TikTok requires authentication. This is a placeholder.
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class TikTokScraper(BaseScraper):
    name = "TikTok"

    HASHTAGS = [
        "zetaviolin", "electricviolin", "zeta", "violinist",
        "musicgear", "forsale",
    ]

    async def search(self) -> list:
        results = []
        # TikTok requires authentication and has strict ToS
        # This would need TikTok API or unofficial scraping
        # For now, returning empty results
        log.warning("TikTok scraping requires authentication - skipping")
        return results
