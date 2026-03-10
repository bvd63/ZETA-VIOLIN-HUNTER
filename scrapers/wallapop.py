"""
Wallapop.com scraper — Spanish classifieds with radius search.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class WallapopScraper(BaseScraper):
    name = "Wallapop"

    # Spanish cities
    SPANISH_CITIES = [
        "Madrid", "Barcelona", "Valencia", "Seville", "Bilbao",
        "Malaga", "Murcia", "Palma", "Las Palmas", "Alicante",
        "Cordoba", "Valladolid", "Vigo", "Gijon", "Eibar",
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violín", "violín eléctrico Zeta",
            "violín eléctrico", "violín MIDI", "violín 5 cuerdas",
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for city in self.SPANISH_CITIES:
                for kw in keywords:
                    try:
                        url = "https://wallapop.com/search"
                        params = {
                            "keywords": kw,
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
                            unique_id = self._make_id("wallapop", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title_el = item.select_one(".item-title")
                            price_el = item.select_one(".item-price")
                            title = title_el.text.strip() if title_el else ""
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
                                "platform": f"Wallapop ({city})",
                                "title": title,
                                "price": price,
                                "location": city,
                                "url": item_url,
                                "description": "",
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Wallapop {city} '{kw}' error: {e}")

        return results
