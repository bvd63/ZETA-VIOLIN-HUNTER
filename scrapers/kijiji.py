"""
Kijiji.ca scraper — Canada's largest classifieds.

Verified 2026-09-07: the search page is reachable from EU IPs and embeds an
Apollo cache in __NEXT_DATA__ (props.pageProps.__APOLLO_STATE__) with
"StandardListing:<id>" entries: title, description, url, imageUrls, price,
location, activationDate.
"""

import json
import logging
import re
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_HEADERS
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.kijiji.ca/b-canada/{slug}/k0l0"

KEYWORDS = [
    "zeta violin",
    "zeta electric violin",
    "zeta strados",
    "violon zeta",
]


class KijijiScraper(BaseScraper):
    name = "Kijiji"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with self.make_client(headers=BROWSER_HEADERS) as client:
            for kw in KEYWORDS:
                slug = re.sub(r"[^a-z0-9]+", "-", kw.lower()).strip("-")
                try:
                    resp = await client.get(SEARCH_URL.format(slug=slug))
                    if resp.status_code != 200:
                        log.warning(f"Kijiji '{kw}' HTTP {resp.status_code}")
                        continue
                    listings = self._extract(resp.text)
                    self.fetched += len(listings)

                    for ad in listings:
                        item_id = str(ad.get("id") or "")
                        title = (ad.get("title") or "").strip()
                        description = (ad.get("description") or "").strip()
                        url = ad.get("url") or ""
                        if not item_id or not title or not url:
                            continue
                        if not has_zeta_signal(title + " " + description):
                            continue
                        unique_id = self._make_id("kijiji", item_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        price = self._price(ad.get("price") or {})
                        location = ((ad.get("location") or {}).get("name") or "").strip()
                        location = ", ".join(p for p in [location, "Canada"] if p)
                        images = ad.get("imageUrls") or []
                        image_url = images[0] if images else ""

                        if self._is_excluded(title):
                            continue
                        if price != "N/A" and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title + " " + description):
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Kijiji",
                            "title": title,
                            "price": price,
                            "location": location,
                            "url": url if url.startswith("http") else f"https://www.kijiji.ca{url}",
                            "description": description[:300],
                            "date_posted": str(ad.get("activationDate") or "")[:10],
                            "image_url": image_url,
                            "relevance_score": self._relevance_score(title, description),
                        })
                except Exception as e:
                    log.warning(f"Kijiji '{kw}' error: {e}")

        log.info(f"Kijiji: {len(results)} listings found")
        return results

    @staticmethod
    def _extract(html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        el = soup.find("script", id="__NEXT_DATA__")
        if not el or not el.string:
            log.warning("Kijiji: __NEXT_DATA__ not found (blocked or layout change)")
            return []
        try:
            data = json.loads(el.string)
        except json.JSONDecodeError as e:
            log.warning(f"Kijiji: __NEXT_DATA__ JSON error: {e}")
            return []
        state = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {}) or {}
        return [v for k, v in state.items() if "Listing:" in k and isinstance(v, dict) and v.get("title")]

    @staticmethod
    def _price(price: dict) -> str:
        amount = price.get("amount")
        if isinstance(amount, (int, float)) and amount > 0:
            return f"{amount / 100:.2f} CAD"  # Kijiji stores cents
        ptype = str(price.get("type") or "")
        return ptype.replace("_", " ").title() if ptype else "N/A"
