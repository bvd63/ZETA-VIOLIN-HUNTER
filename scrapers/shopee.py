"""
Shopee scraper — Southeast Asia marketplace.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class ShopeeScraper(BaseScraper):
    name = "Shopee"

    SHOPEE_COUNTRIES = [
        ("Singapore", "https://shopee.sg"),
        ("Malaysia", "https://shopee.com.my"),
        ("Philippines", "https://shopee.ph"),
        ("Thailand", "https://shopee.co.th"),
        ("Vietnam", "https://shopee.vn"),
        ("Indonesia", "https://shopee.co.id"),
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
            for country, base_url in self.SHOPEE_COUNTRIES:
                for kw in keywords:
                    try:
                        url = f"{base_url}/search"
                        params = {
                            "keyword": kw,
                        }
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("div.product")

                        for item in listings:
                            link_el = item.select_one("a")
                            if not link_el:
                                continue
                            
                            item_url = link_el.get("href", "")
                            unique_id = self._make_id("shopee", item_url)
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
                                "platform": f"Shopee ({country})",
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
                        log.warning(f"Shopee {country} '{kw}' parse error: {e}")

        if network_failures:
            log.warning(f"Shopee network failures: {network_failures}")

        return results
