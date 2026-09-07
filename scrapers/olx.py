"""
OLX scraper — Poland, Portugal, Bulgaria, Ukraine (olx.ro deliberately
excluded: owner does not buy in Romania).

Verified 2026-09-07: every OLX country site exposes the same open JSON API
used by its frontend, reachable from EU datacenter IPs:
  GET https://www.olx.pl/api/v1/offers/?offset=0&limit=40&query=...
"""

import logging
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal

log = logging.getLogger(__name__)

# (platform label, host, country, keywords)
SITES = [
    ("OLX PL", "www.olx.pl", "Poland", ["zeta skrzypce", "zeta violin", "skrzypce elektryczne zeta", "zeta strados"]),
    ("OLX PT", "www.olx.pt", "Portugal", ["zeta violino", "zeta violin", "violino elétrico zeta"]),
    ("OLX BG", "www.olx.bg", "Bulgaria", ["zeta цигулка", "zeta violin", "електрическа цигулка zeta"]),
    ("OLX UA", "www.olx.ua", "Ukraine", ["zeta скрипка", "zeta violin", "електроскрипка zeta"]),
]

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class OlxScraper(BaseScraper):
    name = "OLX"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with self.make_client(headers=HEADERS) as client:
            for label, host, country, keywords in SITES:
                for kw in keywords:
                    try:
                        resp = await client.get(
                            f"https://{host}/api/v1/offers/",
                            params={"offset": 0, "limit": 40, "query": kw},
                        )
                        if resp.status_code != 200:
                            log.warning(f"{label} '{kw}' HTTP {resp.status_code}")
                            continue
                        offers = resp.json().get("data", []) or []
                        self.fetched += len(offers)

                        for offer in offers:
                            item_id = str(offer.get("id") or "")
                            title = (offer.get("title") or "").strip()
                            description = self._clean(offer.get("description") or "")
                            url = offer.get("url") or ""
                            if not item_id or not title or not url:
                                continue
                            if not has_zeta_signal(title + " " + description):
                                continue
                            unique_id = self._make_id("olx", f"{host}:{item_id}")
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            price = self._price(offer.get("params") or [])
                            city = ((offer.get("location") or {}).get("city") or {}).get("name", "")
                            location = ", ".join(p for p in [city, country] if p)
                            image_url = self._image(offer.get("photos") or [])

                            if self._is_excluded(title):
                                continue
                            if price != "N/A" and not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title + " " + description):
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": label,
                                "title": title,
                                "price": price,
                                "location": location,
                                "url": url,
                                "description": description[:300],
                                "date_posted": str(offer.get("created_time") or "")[:10],
                                "image_url": image_url,
                                "relevance_score": self._relevance_score(title, description),
                            })
                    except Exception as e:
                        log.warning(f"{label} '{kw}' error: {e}")

        log.info(f"OLX: {len(results)} listings found")
        return results

    @staticmethod
    def _clean(text: str) -> str:
        import re
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()

    @staticmethod
    def _price(params: list) -> str:
        for p in params:
            if p.get("key") == "price" and isinstance(p.get("value"), dict):
                v = p["value"]
                if v.get("label"):
                    return str(v["label"])
                if v.get("value"):
                    return f"{v['value']} {v.get('currency', '')}".strip()
        return "N/A"

    @staticmethod
    def _image(photos: list) -> str:
        if photos and isinstance(photos[0], dict):
            link = photos[0].get("link") or ""
            return link.replace("{width}", "800").replace("{height}", "600")
        return ""
