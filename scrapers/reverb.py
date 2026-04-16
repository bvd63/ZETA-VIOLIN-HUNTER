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
            "Zeta violin",
            "Zeta electric violin",
            "Zeta Strados",
            "Zeta Jazz Fusion",
            "Zeta JV44",
            "Zeta SV24",
            "Zeta JLP",
            "Zetta violin",
        ]
        max_pages = 2

        seen_ids = set()
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for kw in keywords:
                for page in range(1, max_pages + 1):
                    try:
                        params = {
                            "query": kw,
                            "per_page": 50,
                            "page": page,
                            "state": "all",
                            "year_min": Config.MIN_YEAR,
                            "year_max": Config.MAX_YEAR,
                        }
                        resp = await client.get(REVERB_API, headers=HEADERS, params=params)
                        if resp.status_code != 200:
                            break
                        data = resp.json()
                        listings = data.get("listings", [])
                        if not listings:
                            break

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

                            # Extract primary image URL
                            photos = item.get("photos", [])
                            image_url = ""
                            if photos and isinstance(photos, list):
                                first_photo = photos[0] if photos else {}
                                if isinstance(first_photo, dict):
                                    image_url = first_photo.get("_links", {}).get("large_crop", {}).get("href", "")
                                    if not image_url:
                                        image_url = first_photo.get("_links", {}).get("thumbnail", {}).get("href", "")

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
                                "image_url": image_url,
                            })
                    except Exception as e:
                        log.warning(f"Reverb keyword '{kw}' page {page} error: {e}")
                        break

        return results
