"""
Craigslist scraper — searches major US and international cities.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

# Major Craigslist cities worldwide
CRAIGSLIST_CITIES = [
    "newyork", "losangeles", "chicago", "sfbay", "seattle", "boston",
    "miami", "austin", "denver", "portland", "atlanta", "dallas",
    "london", "toronto", "vancouver", "sydney", "berlin", "paris",
]

KEYWORDS = [
    "Zeta violin", "Zeta electric violin", "Zeta Strados",
    "electric violin Zeta", "Zetta violin",
]


class CraigslistScraper(BaseScraper):
    name = "Craigslist"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for city in CRAIGSLIST_CITIES:
                for kw in KEYWORDS:
                    try:
                        url = f"https://{city}.craigslist.org/search/msa"
                        params = {"query": kw, "sort": "date"}
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("li.cl-static-search-result")

                        for item in listings:
                            link_el = item.select_one("a")
                            if not link_el:
                                continue
                            item_url = link_el.get("href", "")
                            unique_id = self._make_id("craigslist", item_url)

                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title_el = item.select_one(".title")
                            price_el = item.select_one(".price")
                            title = title_el.text.strip() if title_el else ""
                            price = price_el.text.strip() if price_el else "N/A"

                            if self._is_excluded(title):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title):
                                continue

                            score = self._relevance_score(title)
                            if score < 2:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": f"Craigslist ({city})",
                                "title": title,
                                "price": price,
                                "location": city.title(),
                                "url": item_url,
                                "description": "",
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Craigslist {city} '{kw}' error: {e}")

        return results
