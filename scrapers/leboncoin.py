"""
Leboncoin.fr scraper — France's largest classifieds marketplace.
Uses httpx + __NEXT_DATA__ JSON extraction (same pattern as Subito).
No authentication required for search results.
"""

import httpx
import json
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.leboncoin.fr/recherche"

KEYWORDS = [
    "Zeta violon",
    "Zeta violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "violon électrique Zeta",
    "violon electrique Zeta",
]

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
]


class LeboncoinScraper(BaseScraper):
    name = "Leboncoin"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            for kw in KEYWORDS:
                try:
                    resp = await client.get(SEARCH_URL, params={"text": kw})
                    if resp.status_code != 200:
                        log.warning(f"Leboncoin '{kw}': HTTP {resp.status_code}")
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    next_data = soup.find("script", id="__NEXT_DATA__")
                    if not next_data or not next_data.string:
                        log.info(f"Leboncoin '{kw}': no __NEXT_DATA__ found")
                        continue

                    try:
                        data = json.loads(next_data.string)
                    except json.JSONDecodeError:
                        log.warning(f"Leboncoin '{kw}': invalid JSON in __NEXT_DATA__")
                        continue

                    # Navigate to the ads list in Leboncoin's JSON structure
                    ads = self._extract_ads(data)

                    for ad in ads:
                        title = (ad.get("subject") or ad.get("title") or "").strip()
                        body = (ad.get("body") or ad.get("description") or "").strip()
                        ad_url = ad.get("url") or ""
                        if not ad_url and ad.get("list_id"):
                            ad_url = f"https://www.leboncoin.fr/ad/{ad.get('category_name', 'autre')}/{ad['list_id']}.htm"

                        if not ad_url or not title:
                            continue

                        # Strict Zeta check on the ad's own text
                        ad_text = (title + " " + body).lower()
                        if not any(sig in ad_text for sig in ZETA_SIGNALS):
                            continue

                        unique_id = self._make_id("leboncoin", ad_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        price = self._extract_price_from_ad(ad)
                        location = self._extract_location_from_ad(ad)

                        full_text = f"{title} {body}"
                        if self._is_excluded(full_text):
                            continue
                        if self._is_excluded_location(location):
                            continue
                        if price != "N/A" and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(full_text):
                            continue

                        score = self._relevance_score(title, body)
                        if score < 2:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Leboncoin",
                            "title": title,
                            "price": price,
                            "location": location,
                            "url": ad_url,
                            "description": body[:300],
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Leboncoin '{kw}' error: {e}")

        log.info(f"Leboncoin: {len(results)} listings found")
        return results

    def _extract_ads(self, data: dict) -> list:
        """Navigate Leboncoin's __NEXT_DATA__ to find ad objects."""
        ads = []
        try:
            # Try common Leboncoin JSON paths
            props = data.get("props", {}).get("pageProps", {})

            # Path 1: searchData.ads
            search_data = props.get("searchData", {})
            if isinstance(search_data, dict):
                raw_ads = search_data.get("ads", [])
                if isinstance(raw_ads, list):
                    ads.extend(raw_ads)

            # Path 2: initialProps.searchData.ads
            initial = props.get("initialProps", {})
            if isinstance(initial, dict):
                search_data2 = initial.get("searchData", {})
                if isinstance(search_data2, dict):
                    raw_ads2 = search_data2.get("ads", [])
                    if isinstance(raw_ads2, list):
                        ads.extend(raw_ads2)

            # Path 3: direct ads array
            if not ads:
                raw_ads3 = props.get("ads", [])
                if isinstance(raw_ads3, list):
                    ads.extend(raw_ads3)

        except Exception as e:
            log.warning(f"Leboncoin JSON nav error: {e}")
        return ads

    def _extract_price_from_ad(self, ad: dict) -> str:
        """Extract price from Leboncoin ad object."""
        try:
            price_list = ad.get("price", [])
            if isinstance(price_list, list) and price_list:
                return f"{price_list[0]} EUR"
            if isinstance(price_list, (int, float)):
                return f"{price_list} EUR"
            attrs = ad.get("attributes", [])
            if isinstance(attrs, list):
                for attr in attrs:
                    if isinstance(attr, dict) and attr.get("key") == "price":
                        return f"{attr.get('value', '?')} EUR"
        except Exception:
            pass
        return "N/A"

    def _extract_location_from_ad(self, ad: dict) -> str:
        """Extract location from Leboncoin ad object."""
        try:
            loc = ad.get("location", {})
            if isinstance(loc, dict):
                parts = [
                    loc.get("city", ""),
                    loc.get("department_name", ""),
                    loc.get("region_name", ""),
                ]
                return ", ".join(p for p in parts if p)
        except Exception:
            pass
        return "France"
