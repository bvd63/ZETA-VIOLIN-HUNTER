"""
Reverb.com scraper — public listings API.

IMPORTANT (verified 2026-09-07): Reverb's edge blocks non-browser User-Agents
with HTTP 403 (an HTML page). With a browser User-Agent the API answers 200
WITHOUT any token. REVERB_API_TOKEN is therefore optional; if set it is sent
as X-Auth-Token (raises rate limits).
"""

import httpx
import logging
import re
from scrapers.base import BaseScraper, BROWSER_UA
from config import Config

log = logging.getLogger(__name__)

REVERB_API = "https://api.reverb.com/api/listings"

KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "Zeta Jazz Modern",
    "Zeta Acoustic Pro",
    "Zeta JV44",
    "Zeta SV24",
    "Zeta JLP",
    "Zetta violin",
    "Strados violin",
    "Jean-Luc Ponty violin",
    "electric violin 5 string MIDI",
]
MAX_PAGES = 2
TAG_RX = re.compile(r"<[^>]+>")


def _build_headers() -> dict:
    headers = {
        "Accept": "application/hal+json",
        "Accept-Version": "3.0",
        "User-Agent": BROWSER_UA,
    }
    if Config.REVERB_API_TOKEN:
        headers["X-Auth-Token"] = Config.REVERB_API_TOKEN
    return headers


class ReverbScraper(BaseScraper):
    name = "Reverb"

    async def search(self) -> list:
        results = []
        seen_ids = set()
        headers = _build_headers()

        # Keyword searches plus Reverb's structured brand filter (make=Zeta),
        # which catches odd titles like "Very fine rare 5 strings Zeta Midi".
        searches = [{"query": kw} for kw in KEYWORDS] + [{"make": "Zeta"}]

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for base_params in searches:
                kw = base_params.get("query") or f"make={base_params.get('make')}"
                for page in range(1, MAX_PAGES + 1):
                    try:
                        params = {**base_params, "per_page": 50, "page": page}
                        resp = await client.get(REVERB_API, headers=headers, params=params)
                        if resp.status_code != 200:
                            log.warning(
                                f"Reverb API HTTP {resp.status_code} for '{kw}' p{page}: "
                                f"{resp.text[:120]!r}"
                            )
                            break
                        listings = resp.json().get("listings", [])
                        if not listings:
                            break
                        self.fetched += len(listings)

                        for item in listings:
                            state = (item.get("state") or {}).get("slug", "live")
                            if state != "live":
                                continue

                            url = item.get("_links", {}).get("web", {}).get("href", "")
                            listing_id = self._make_id("reverb", str(item.get("id") or url))
                            if not url or listing_id in seen_ids:
                                continue
                            seen_ids.add(listing_id)

                            title = item.get("title", "")
                            price_obj = item.get("price", {}) or {}
                            price = f"{price_obj.get('amount', '?')} {price_obj.get('currency', '')}".strip()
                            condition = (item.get("condition") or {}).get("display_name", "")
                            location = self._location(item)
                            description = TAG_RX.sub(" ", item.get("description", "") or "")
                            description = re.sub(r"\s+", " ", description).strip()[:300]

                            image_url = ""
                            photos = item.get("photos") or []
                            if photos and isinstance(photos[0], dict):
                                links = photos[0].get("_links", {})
                                image_url = (links.get("large_crop") or links.get("thumbnail") or {}).get("href", "")

                            if self._is_excluded(title):
                                continue
                            if self._is_excluded_location(location):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title + " " + description):
                                continue

                            results.append({
                                "id": listing_id,
                                "platform": "Reverb",
                                "title": title,
                                "price": price,
                                "location": location or "Unknown",
                                "url": url,
                                "description": description,
                                "condition": condition,
                                "date_posted": (item.get("published_at") or "")[:10],
                                "relevance_score": self._relevance_score(title, description),
                                "image_url": image_url,
                            })
                    except Exception as e:
                        log.warning(f"Reverb keyword '{kw}' page {page} error: {e}")
                        break

        log.info(f"Reverb: {len(results)} listings found")
        return results

    @staticmethod
    def _location(item: dict) -> str:
        """The listings API no longer returns a shop address. Derive the seller
        region from the first shipping rate (e.g. US_CON → US, GB → GB,
        XX → Worldwide); fall back to the shop address if present."""
        address = (item.get("shop") or {}).get("address") or {}
        parts = [address.get("locality", ""), address.get("region", ""), address.get("country_code", "")]
        joined = ", ".join(p for p in parts if p)
        if joined:
            return joined
        rates = (item.get("shipping") or {}).get("rates") or []
        for rate in rates:
            code = str((rate or {}).get("region_code", "")).upper()
            if code:
                return "Worldwide" if code == "XX" else code.split("_")[0]
        return "Unknown"
