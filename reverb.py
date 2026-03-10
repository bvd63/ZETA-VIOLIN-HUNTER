"""
Reverb.com scraper — uses public search API (no key required).
"""

import httpx
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

REVERB_API = "https://api.reverb.com/api/listings"
HEADERS = {
    "Accept": "application/hal+json",
    "Accept-Version": "3.0",
    "User-Agent": "ZetaViolinHunter/1.0",
}


class ReverbScraper(BaseScraper):
    name = "Reverb"

    async def search(self) -> list:
        results = []
        keywords = [
            "Zeta violin", "Zeta electric violin", "Zeta Strados",
            "Zeta Jazz Fusion", "Zeta JV44", "Zeta SV24", "Zeta SV25",
            "Zeta EV25", "Zeta EV44", "Zeta JLP", "Zeta CV44",
            "Zetta violin", "violino elettrico Zeta", "violon Zeta",
        ]

        seen_ids = set()
        async with httpx.AsyncClient(timeout=15) as client:
            for kw in keywords:
                try:
                    params = {
                        "query": kw,
                        "category_uuids": "f9d0f7d6-4c7e-4b1e-b3e8-4b3e4b1e4b3e",  # Violins
                        "per_page": 50,
                        "state": "all",
                        "year_min": Config.MIN_YEAR,
                        "year_max": Config.MAX_YEAR,
                    }
                    resp = await client.get(REVERB_API, headers=HEADERS, params=params)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    listings = data.get("listings", [])

                    for item in listings:
                        url = item.get("_links", {}).get("web", {}).get("href", "")
                        listing_id = self._make_id("reverb", url)

                        if listing_id in seen_ids:
                            continue
                        seen_ids.add(listing_id)

                        title = item.get("title", "")
                        price_obj = item.get("price", {})
                        price = f"{price_obj.get('amount', '?')} {price_obj.get('currency', '')}"
                        condition = item.get("condition", {}).get("display_name", "")
                        location = item.get("shop", {}).get("address", {}).get("country_code", "")
                        description = item.get("description", "")[:300]

                        if self._is_excluded(title + " " + condition):
                            continue
                        if self._is_excluded_location(location):
                            continue
                        if not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title + " " + description):
                            continue

                        score = self._relevance_score(title, description)
                        if score < 2:
                            continue

                        results.append({
                            "id": listing_id,
                            "platform": "Reverb",
                            "title": title,
                            "price": price,
                            "location": location,
                            "url": url,
                            "description": description,
                            "condition": condition,
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Reverb keyword '{kw}' error: {e}")

        return results
