"""
Maestronet.com scraper — Violin community classifieds (CRITICAL!).
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class MaestronetScraper(BaseScraper):
    name = "Maestronet"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violin", "Zeta electric violin", "Zeta Strados",
            "electric violin", "MIDI violin", "5-string violin",
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for kw in keywords:
                try:
                    url = "https://maestronet.com/forum/index.php"
                    params = {
                        "action": "search",
                        "search": kw,
                        "board": "1",  # For Sale section
                    }
                    resp = await client.get(url, params=params, headers={
                        "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                    })
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    listings = soup.select("div.post")

                    for item in listings:
                        link_el = item.select_one("a.subject")
                        if not link_el:
                            continue
                        
                        item_url = link_el.get("href", "")
                        unique_id = self._make_id("maestronet", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        title = link_el.text.strip()
                        content = item.select_one(".post-content")
                        description = content.text.strip() if content else ""

                        if self._is_excluded(title + " " + description):
                            continue
                        if not self._year_in_range(title + " " + description):
                            continue

                        score = self._relevance_score(title, description)
                        if score < 3:
                            continue

                        # Extract price from description
                        price = self._extract_price(description)

                        results.append({
                            "id": unique_id,
                            "platform": "Maestronet",
                            "title": title,
                            "price": price or "See listing",
                            "location": "Global",
                            "url": item_url,
                            "description": description[:300],
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Maestronet '{kw}' error: {e}")

        return results
