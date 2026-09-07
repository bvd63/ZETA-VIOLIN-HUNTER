"""
Marktplaats (NL) + 2dehands / 2ememain (BE) scraper — Adevinta's classifieds.

Verified 2026-09-07: both sites expose the same open JSON search API used by
their own frontend, reachable from EU datacenter IPs without auth:
  GET https://www.marktplaats.nl/lrp/api/search?query=...&limit=30
  GET https://www.2dehands.be/lrp/api/search?query=...&limit=30
"""

import logging
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SITES = [
    ("Marktplaats", "https://www.marktplaats.nl", "Netherlands"),
    ("2dehands", "https://www.2dehands.be", "Belgium"),
]

KEYWORDS = [
    "zeta viool",
    "zeta violin",
    "zeta strados",
    "elektrische viool zeta",
    "zeta jazz fusion",
]

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}


class MarktplaatsScraper(BaseScraper):
    name = "Marktplaats/2dehands"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with self.make_client(headers=HEADERS) as client:
            for site_name, base, country in SITES:
                for kw in KEYWORDS:
                    try:
                        resp = await client.get(f"{base}/lrp/api/search", params={"query": kw, "limit": 30, "offset": 0})
                        if resp.status_code != 200:
                            log.warning(f"{site_name} '{kw}' HTTP {resp.status_code}")
                            continue
                        listings = resp.json().get("listings", []) or []
                        self.fetched += len(listings)

                        for ad in listings:
                            item_id = str(ad.get("itemId") or "")
                            title = (ad.get("title") or "").strip()
                            description = (ad.get("description") or "").strip()
                            if not item_id or not title:
                                continue
                            if not has_zeta_signal(title + " " + description):
                                continue
                            unique_id = self._make_id(site_name.lower(), item_id)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            price = self._price(ad.get("priceInfo") or {})
                            loc = ad.get("location") or {}
                            location = ", ".join(p for p in [loc.get("cityName", ""), country] if p)
                            vip = ad.get("vipUrl") or ""
                            url = vip if vip.startswith("http") else f"{base}{vip}"
                            image_url = self._image(ad)

                            if self._is_excluded(title):
                                continue
                            if price != "N/A" and not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title + " " + description):
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": site_name,
                                "title": title,
                                "price": price,
                                "location": location,
                                "url": url,
                                "description": description[:300],
                                "date_posted": str(ad.get("date") or "")[:10],
                                "image_url": image_url,
                                "relevance_score": self._relevance_score(title, description),
                            })
                    except Exception as e:
                        log.warning(f"{site_name} '{kw}' error: {e}")

        log.info(f"Marktplaats/2dehands: {len(results)} listings found")
        return results

    @staticmethod
    def _price(price_info: dict) -> str:
        cents = price_info.get("priceCents")
        if isinstance(cents, (int, float)) and cents > 0:
            return f"{cents / 100:.2f} EUR"
        ptype = str(price_info.get("priceType") or "")
        return ptype.replace("_", " ").title() if ptype and ptype not in ("FIXED", "MIN_BID") else "N/A"

    @staticmethod
    def _image(ad: dict) -> str:
        for key in ("imageUrls", "pictures"):
            imgs = ad.get(key) or []
            if imgs:
                first = imgs[0]
                if isinstance(first, str):
                    return ("https:" + first) if first.startswith("//") else first
                if isinstance(first, dict):
                    for k in ("extraExtraLargeUrl", "extraLargeUrl", "largeUrl", "mediumUrl", "url"):
                        if first.get(k):
                            v = first[k]
                            return ("https:" + v) if v.startswith("//") else v
        return ""
