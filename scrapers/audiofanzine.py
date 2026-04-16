"""
Audiofanzine scraper — French music gear community.
Has classifieds section for instruments. httpx + BeautifulSoup.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

SEARCH_URL = "https://en.audiofanzine.com/search/"

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
]


class AudiofanzineScraper(BaseScraper):
    name = "Audiofanzine"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        }

        keywords = ["zeta violin", "zeta electric violin", "zeta strados"]

        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True, headers=headers
        ) as client:
            for kw in keywords:
                try:
                    params = {"q": kw, "type": "classified"}
                    resp = await client.get(SEARCH_URL, params=params)
                    if resp.status_code != 200:
                        log.warning(f"Audiofanzine '{kw}': HTTP {resp.status_code}")
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")

                    for link in soup.select(
                        "a[href*='/classified/'], a[href*='/annonce/']"
                    ):
                        href = link.get("href", "")
                        title = link.get_text(strip=True)

                        if not href or not title:
                            continue

                        if not href.startswith("http"):
                            href = "https://en.audiofanzine.com" + href

                        if not any(sig in title.lower() for sig in ZETA_SIGNALS):
                            continue

                        unique_id = self._make_id("audiofanzine", href)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue

                        score = self._relevance_score(title)
                        if score < 2:
                            continue

                        # Try to find price in parent element
                        parent = link.find_parent("div") or link.find_parent("li")
                        price = "N/A"
                        if parent:
                            price_el = parent.select_one("[class*='price'], [class*='Price']")
                            if price_el:
                                price = price_el.get_text(strip=True)

                        results.append({
                            "id": unique_id,
                            "platform": "Audiofanzine",
                            "title": title,
                            "price": price,
                            "location": "Online",
                            "url": href,
                            "description": "",
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Audiofanzine '{kw}' error: {e}")

        log.info(f"Audiofanzine: {len(results)} listings found")
        return results
