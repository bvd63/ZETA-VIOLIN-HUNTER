"""
Subito.it scraper — Italian classifieds, nationwide search.

Verified 2026-09-07: the ads live in
  __NEXT_DATA__ → props.pageProps.initialState.items.originalList (+ galleryList)
NOT in ".items.list" (always empty). Appending a city name to the query text
("Zeta violino Milan") kills results, so we search nationwide once per keyword.
"""

import httpx
import json
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.subito.it/annunci-italia/vendita/usato/"

KEYWORDS = [
    "zeta violino",
    "zeta violin",
    "zeta strados",
    "violino elettrico zeta",
    "zeta acoustic pro",
    "zeta jazz fusion",
]

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}


class SubitoScraper(BaseScraper):
    name = "Subito"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=HEADERS) as client:
            for kw in KEYWORDS:
                try:
                    resp = await client.get(SEARCH_URL, params={"q": kw})
                    if resp.status_code != 200:
                        log.warning(f"Subito '{kw}' HTTP {resp.status_code}")
                        continue
                    ads = self._extract_ads(resp.text)
                    self.fetched += len(ads)
                    for ad in ads:
                        item_url = (ad.get("urls") or {}).get("default") or ""
                        title = (ad.get("subject") or "").strip()
                        body = (ad.get("body") or "").strip()
                        if not item_url or not title:
                            continue
                        if not has_zeta_signal(title + " " + body):
                            continue

                        unique_id = self._make_id("subito", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        price = self._extract_price_from_features(ad) or "N/A"
                        geo = ad.get("geo") or {}
                        location = ", ".join(
                            p for p in [
                                ((geo.get("town") or {}).get("value") or ""),
                                ((geo.get("region") or {}).get("value") or ""),
                                "Italy",
                            ] if p
                        )

                        if self._is_excluded(title):
                            continue
                        if price != "N/A" and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title + " " + body):
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Subito",
                            "title": title,
                            "price": price,
                            "location": location,
                            "url": item_url,
                            "description": body[:300],
                            "date_posted": (ad.get("date") or "")[:10],
                            "image_url": self._extract_image(ad),
                            "relevance_score": self._relevance_score(title, body),
                        })
                except Exception as e:
                    log.warning(f"Subito '{kw}' error: {e}")

        log.info(f"Subito: {len(results)} listings found")
        return results

    def _extract_ads(self, html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        el = soup.find("script", id="__NEXT_DATA__")
        if not el or not el.string:
            log.warning("Subito: __NEXT_DATA__ not found (blocked or layout change)")
            return []
        try:
            data = json.loads(el.string)
        except json.JSONDecodeError as e:
            log.warning(f"Subito: __NEXT_DATA__ JSON error: {e}")
            return []
        items = (
            data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("items", {})
        )
        if not isinstance(items, dict):
            return []
        ads = []
        for key in ("originalList", "galleryList"):
            for entry in items.get(key) or []:
                # entries are either the ad itself or {"item": ad}
                ad = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else entry
                if isinstance(ad, dict) and ad.get("subject"):
                    ads.append(ad)
        return ads

    def _extract_price_from_features(self, ad: dict) -> str:
        try:
            values = ((ad.get("features") or {}).get("/price") or {}).get("values") or []
            if values and isinstance(values[0], dict):
                return str(values[0].get("value", "")).strip()
        except Exception as e:
            log.debug(f"Subito price parse error: {e}")
        return ""

    def _extract_image(self, ad: dict) -> str:
        try:
            images = ad.get("images") or []
            if images and isinstance(images[0], dict):
                scale = images[0].get("scale") or []
                if scale and isinstance(scale[-1], dict):
                    return scale[-1].get("secureuri") or scale[-1].get("uri") or ""
                return images[0].get("cdn_base_url") or ""
        except Exception as e:
            log.debug(f"Subito image parse error: {e}")
        return ""
