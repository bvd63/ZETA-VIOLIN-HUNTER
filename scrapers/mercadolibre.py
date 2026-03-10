"""
MercadoLibre scraper — Latin America marketplace.
"""

import httpx
import logging
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class MercadolibreScraper(BaseScraper):
    name = "MercadoLibre"

    MERCADOLIBRE_SITES = [
        ("Argentina", "https://listado.mercadolibre.com.ar"),
        ("Brazil", "https://lista.mercadolivre.com.br"),
        ("Chile", "https://listado.mercadolibre.cl"),
        ("Colombia", "https://listado.mercadolibre.com.co"),
        ("Mexico", "https://listado.mercadolibre.com.mx"),
        ("Peru", "https://listado.mercadolibre.com.pe"),
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violin", "Zeta electric violin",
            "electric violin", "MIDI violin",
        ]

        network_failures = 0
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for country, base_url in self.MERCADOLIBRE_SITES:
                for kw in keywords:
                    try:
                        url = f"{base_url}/{quote_plus(kw)}"
                        params = {}
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("a[href*='/ML'], a[href*='mercadolibre']")

                        for item in listings:
                            item_url = item.get("href", "")
                            unique_id = self._make_id("mercadolibre", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = item.get_text(" ", strip=True)
                            price = "N/A"

                            if self._is_excluded(title):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title):
                                continue

                            score = self._relevance_score(title)
                            if score < 1:
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

                    except httpx.RequestError:
                        network_failures += 1
                    except Exception as e:
                        log.warning(f"MercadoLibre {country} '{kw}' parse error: {e}")

        if network_failures:
            log.warning(f"MercadoLibre network failures: {network_failures}")

        return results
