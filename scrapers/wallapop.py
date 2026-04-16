"""
Wallapop scraper — Spain's largest classifieds.
Uses Wallapop's web search endpoint with browser-like headers.
"""

import httpx
import logging
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.wallapop.com/api/v3/general/search"

KEYWORDS = [
    "Zeta violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "violín eléctrico Zeta",
]

DEFAULT_LAT = 40.4168
DEFAULT_LNG = -3.7038


class WallapopScraper(BaseScraper):
    name = "Wallapop"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Origin": "https://es.wallapop.com",
            "Referer": "https://es.wallapop.com/",
            "X-DeviceOS": "0",
        }

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for kw in KEYWORDS:
                try:
                    params = {
                        "keywords": kw,
                        "latitude": DEFAULT_LAT,
                        "longitude": DEFAULT_LNG,
                        "distance": 200000,
                        "order_by": "newest",
                    }
                    resp = await client.get(SEARCH_URL, params=params)

                    if resp.status_code == 403:
                        log.warning(f"Wallapop '{kw}': 403 — trying web fallback")
                        items = await self._web_fallback(client, kw)
                    elif resp.status_code != 200:
                        log.warning(f"Wallapop '{kw}': HTTP {resp.status_code}")
                        continue
                    else:
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
                        if isinstance(price_obj, dict):
                            price = f"{price_obj.get('amount', '?')} EUR"
                        else:
                            price = str(price_obj)
                        loc_obj = item.get("location", {})
                        location = loc_obj.get("city", "") if isinstance(loc_obj, dict) else ""

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

    async def _web_fallback(self, client: httpx.AsyncClient, kw: str) -> list:
        """If API returns 403, try scraping the web search page."""
        try:
            web_url = f"https://es.wallapop.com/app/search?keywords={kw}&distance=200000"
            resp = await client.get(web_url)
            if resp.status_code != 200:
                return []
            from bs4 import BeautifulSoup
            import json
            soup = BeautifulSoup(resp.text, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if script and script.string:
                data = json.loads(script.string)
                props = data.get("props", {}).get("pageProps", {})
                items = props.get("searchResults", {}).get("items", [])
                return items
        except Exception as e:
            log.warning(f"Wallapop web fallback '{kw}' error: {e}")
        return []
