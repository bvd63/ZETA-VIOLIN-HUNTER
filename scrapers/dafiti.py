"""
Dafiti scraper — Latin America fashion/music marketplace.
"""

import httpx
import logging
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class DafitiScraper(BaseScraper):
    name = "Dafiti"

    DAFITI_COUNTRIES = [
        ("Brazil", "https://www.dafiti.com.br"),
        ("Argentina", "https://www.dafiti.com.ar"),
        ("Chile", "https://www.dafiti.cl"),
        ("Colombia", "https://www.dafiti.com.co"),
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
            for country, base_url in self.DAFITI_COUNTRIES:
                for kw in keywords:
                    try:
                        url = f"{base_url}/catalog/"
                        params = {"q": kw}
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("a[href*='/p/']")

                        for link_el in listings:
                            item_url = link_el.get("href", "")
                            if item_url.startswith("/"):
                                item_url = f"{base_url}{item_url}"
                            unique_id = self._make_id("dafiti", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = link_el.get_text(" ", strip=True)
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
                                "platform": f"Dafiti ({country})",
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
                        log.warning(f"Dafiti {country} '{kw}' parse error: {e}")

        if network_failures:
            log.warning(f"Dafiti network failures: {network_failures}")

        return results
