"""
Leboncoin.fr scraper — French classifieds with radius search.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class LeboncoinScraper(BaseScraper):
    name = "Leboncoin"

    # French cities with postal codes for search
    FRENCH_CITIES = [
        ("Paris", "75"),
        ("Lyon", "69"),
        ("Marseille", "13"),
        ("Toulouse", "31"),
        ("Nice", "06"),
        ("Nantes", "44"),
        ("Strasbourg", "67"),
        ("Montpellier", "34"),
        ("Bordeaux", "33"),
        ("Lille", "59"),
        ("Rennes", "35"),
        ("Reims", "51"),
        ("Le Havre", "76"),
        ("Saint-Étienne", "42"),
        ("Toulon", "83"),
        ("Grenoble", "38"),
        ("Angers", "49"),
        ("Nîmes", "30"),
        ("Dijon", "21"),
        ("Aix-en-Provence", "13"),
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violin", "Zeta violon", "violon électrique Zeta",
            "violon électrique", "violon MIDI", "violon 5 cordes",
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for city, postal_code in self.FRENCH_CITIES:
                for kw in keywords:
                    try:
                        # Leboncoin search URL
                        url = "https://www.leboncoin.fr/search"
                        params = {
                            "q": kw,
                            "location": city,
                            "category": "instruments_musique",
                        }
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("a.list-item")

                        for item in listings:
                            item_url = item.get("href", "")
                            if not item_url:
                                continue
                            
                            unique_id = self._make_id("leboncoin", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title_el = item.select_one(".list-item-title")
                            price_el = item.select_one(".list-item-price")
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
                                "platform": f"Leboncoin ({city})",
                                "title": title,
                                "price": price,
                                "location": city,
                                "url": item_url,
                                "description": "",
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Leboncoin {city} '{kw}' error: {e}")

        return results
