"""
Wallapop scraper — uses Wallapop's public search API.
Spain's largest classifieds marketplace. No authentication required.
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

API_URL = "https://api.wallapop.com/api/v3/general/search"

KEYWORDS = [
    "Zeta violin",
    "Zeta violín eléctrico",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "violín eléctrico Zeta",
]

# Default coordinates: Madrid, Spain (center of Spain, 200km radius
# covers most of the country)
DEFAULT_LAT = 40.4168
DEFAULT_LNG = -3.7038


class WallapopScraper(BaseScraper):
    name = "Wallapop"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            for kw in KEYWORDS:
                try:
                    params = {
                        "keywords": kw,
                        "latitude": DEFAULT_LAT,
                        "longitude": DEFAULT_LNG,
                        "distance": 200000,
                        "order_by": "newest",
                    }
                    resp = await client.get(API_URL, params=params)
                    if resp.status_code != 200:
                        log.warning(f"Wallapop '{kw}': HTTP {resp.status_code}")
                        continue

                    data = resp.json()
                    items = data.get("search_objects", [])

                    for item in items:
                        web_slug = item.get("web_slug", "")
                        url = f"https://es.wallapop.com/item/{web_slug}" if web_slug else ""
                        if not url:
                            continue

                        unique_id = self._make_id("wallapop", url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        title = item.get("title", "")
                        description = item.get("description", "")
                        price_obj = item.get("price", {})
                        price = f"{price_obj.get('amount', '?')} {price_obj.get('currency', 'EUR')}"
                        location = item.get("location", {}).get("city", "")

                        full_text = f"{title} {description}"
                        if self._is_excluded(full_text):
                            continue
                        if self._is_excluded_location(location):
                            continue
                        if not self._price_in_range(price):
                            continue
                        if not self._year_in_range(full_text):
                            continue

                        score = self._relevance_score(title, description)
                        if score < 2:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Wallapop",
                            "title": title,
                            "price": price,
                            "location": location,
                            "url": url,
                            "description": description[:300],
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Wallapop '{kw}' error: {e}")

        log.info(f"Wallapop: {len(results)} listings found")
        return results
