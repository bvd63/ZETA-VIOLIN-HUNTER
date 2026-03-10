"""
MercadoLibre scraper — Latin America marketplace.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class MercadolibreScraper(BaseScraper):
    name = "MercadoLibre"

    MERCADOLIBRE_SITES = [
        ("Argentina", "mla"),
        ("Brazil", "mlb"),
        ("Chile", "mlc"),
        ("Colombia", "mco"),
        ("Mexico", "mlm"),
        ("Peru", "mpe"),
        ("Venezuela", "mlv"),
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violin", "Zeta electric violin",
            "electric violin", "MIDI violin",
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for country, site_code in self.MERCADOLIBRE_SITES:
                for kw in keywords:
                    try:
                        url = f"https://{site_code}.mercadolibre.com/jm/search"
                        params = {
                            "q": kw,
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
                            unique_id = self._make_id("mercadolibre", item_url)
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
                                "platform": f"MercadoLibre ({country})",
                                "title": title,
                                "price": price,
                                "location": country,
                                "url": item_url,
                                "description": "",
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"MercadoLibre {country} '{kw}' error: {e}")

        return results
