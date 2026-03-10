"""
Ricardo.ch scraper — Swiss classifieds.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class RicardoScraper(BaseScraper):
    name = "Ricardo"

    SWISS_CITIES = [
        "Zurich", "Geneva", "Basel", "Bern", "Lausanne",
        "Lucerne", "St. Gallen", "Winterthur", "Aarau", "Chur",
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta Geige", "Zeta Violine", "elektrische Geige Zeta",
            "violon électrique Zeta", "violino elettrico Zeta",
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for city in self.SWISS_CITIES:
                for kw in keywords:
                    try:
                        url = "https://www.ricardo.ch/en/search"
                        params = {
                            "q": kw,
                            "location": city,
                        }
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("div.item")

                        for item in listings:
                            link_el = item.select_one("a")
                            if not link_el:
                                continue
                            
                            item_url = link_el.get("href", "")
                            unique_id = self._make_id("ricardo", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = link_el.text.strip()
                            price_el = item.select_one(".price")
                            price = price_el.text.strip() if price_el else "N/A"

                            if self._is_excluded(title):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title):
                                continue

                            score = self._relevance_score(title)
                            if score < 3:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": f"Ricardo ({city})",
                                "title": title,
                                "price": price,
                                "location": city,
                                "url": item_url,
                                "description": "",
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Ricardo {city} '{kw}' error: {e}")

        return results
