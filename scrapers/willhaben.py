"""
Willhaben.at scraper — Austria's largest marketplace.

Verified 2026-09-07: the search page is reachable from EU IPs and embeds the
result set in __NEXT_DATA__ → pageProps.searchResult.advertSummaryList
.advertSummary[], each with a flat attribute list (HEADING, BODY_DYN,
PRICE_FOR_DISPLAY, LOCATION, SEO_URL, PUBLISHED_String, ALL_IMAGE_URLS).
"""

import json
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_HEADERS
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz"
# Musikinstrumente category — bare brand query without the non-music noise
CATEGORY_URL = "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/musikinstrumente-2001"

# (keyword, category-scoped)
KEYWORDS = [
    ("zeta", True),
    ("strados", True),
    ("zeta geige", False),
    ("zeta violine", False),
    ("zeta violin", False),
    ("e-geige zeta", False),
]


class WillhabenScraper(BaseScraper):
    name = "Willhaben"

    async def search(self) -> list:
        results = []
        seen_ids = set()
        headers = {**BROWSER_HEADERS, "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"}

        async with self.make_client(headers=headers) as client:
            for kw, in_category in KEYWORDS:
                try:
                    resp = await client.get(CATEGORY_URL if in_category else SEARCH_URL, params={"keyword": kw})
                    if resp.status_code != 200:
                        log.warning(f"Willhaben '{kw}' HTTP {resp.status_code}")
                        continue
                    adverts = self._extract(resp.text)
                    self.fetched += len(adverts)

                    for ad in adverts:
                        attrs = self._attrs(ad)
                        item_id = str(ad.get("id") or attrs.get("ADID", ""))
                        title = attrs.get("HEADING", "").strip()
                        description = attrs.get("BODY_DYN", "").strip()
                        seo = attrs.get("SEO_URL", "")
                        if not item_id or not title or not seo:
                            continue
                        if not has_zeta_signal(title + " " + description):
                            continue
                        unique_id = self._make_id("willhaben", item_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        price = attrs.get("PRICE_FOR_DISPLAY") or (
                            f"{attrs['PRICE/AMOUNT']} EUR" if attrs.get("PRICE/AMOUNT") else "N/A"
                        )
                        location = ", ".join(p for p in [attrs.get("LOCATION", ""), "Austria"] if p)
                        image_url = ""
                        imgs = attrs.get("ALL_IMAGE_URLS", "")
                        if imgs:
                            first = imgs.split(";")[0].strip()
                            image_url = first if first.startswith("http") else f"https://cache.willhaben.at/mmo/{first}"

                        if self._is_excluded(title):
                            continue
                        if price != "N/A" and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title + " " + description):
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Willhaben",
                            "title": title,
                            "price": price,
                            "location": location,
                            "url": f"https://www.willhaben.at/iad/{seo.lstrip('/')}",
                            "description": description[:300],
                            "date_posted": attrs.get("PUBLISHED_String", "")[:10],
                            "image_url": image_url,
                            "relevance_score": self._relevance_score(title, description),
                        })
                except Exception as e:
                    log.warning(f"Willhaben '{kw}' error: {e}")

        log.info(f"Willhaben: {len(results)} listings found")
        return results

    @staticmethod
    def _extract(html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        el = soup.find("script", id="__NEXT_DATA__")
        if not el or not el.string:
            log.warning("Willhaben: __NEXT_DATA__ not found (blocked or layout change)")
            return []
        try:
            data = json.loads(el.string)
        except json.JSONDecodeError as e:
            log.warning(f"Willhaben: __NEXT_DATA__ JSON error: {e}")
            return []
        sr = data.get("props", {}).get("pageProps", {}).get("searchResult", {}) or {}
        return (sr.get("advertSummaryList") or {}).get("advertSummary") or []

    @staticmethod
    def _attrs(ad: dict) -> dict:
        out = {}
        for a in ((ad.get("attributes") or {}).get("attribute") or []):
            name = a.get("name")
            values = a.get("values") or []
            if name and values:
                out[name] = str(values[0])
        return out
