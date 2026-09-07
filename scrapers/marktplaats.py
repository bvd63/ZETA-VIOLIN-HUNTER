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

# (keyword, l1CategoryId or None). 728 = Muziek en Instrumenten — lets the bare
# brand query skip the "Linea Zeta" shoes.
KEYWORDS = [
    ("zeta", 728),
    ("strados", 728),
    ("zeta viool", None),
    ("zeta violin", None),
    ("elektrische viool zeta", None),
    ("zeta jazz fusion", None),
]

DUTCH_MONTHS = {"jan": 1, "feb": 2, "mrt": 3, "maa": 3, "apr": 4, "mei": 5, "jun": 6, "jul": 7,
                "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
                "fév": 2, "mar": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7, "aoû": 8, "oct": 10, "déc": 12}

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
                for kw, category in KEYWORDS:
                    try:
                        params = {"query": kw, "limit": 30, "offset": 0}
                        if category:
                            params["l1CategoryId"] = category
                        resp = await client.get(f"{base}/lrp/api/search", params=params)
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
                                "date_posted": self._date(str(ad.get("date") or "")),
                                "image_url": image_url,
                                "relevance_score": self._relevance_score(title, description),
                            })
                    except Exception as e:
                        log.warning(f"{site_name} '{kw}' error: {e}")

        log.info(f"Marktplaats/2dehands: {len(results)} listings found")
        return results

    @staticmethod
    def _date(raw: str) -> str:
        """'26 aug 26' / '26 aug. 2026' → '2026-08-26'; 'Vandaag'/'Gisteren'/'Eergisteren' → ISO."""
        from datetime import date, timedelta
        import re
        low = raw.strip().lower()
        if not low:
            return ""
        today = date.today()
        if low.startswith(("vandaag", "aujourd", "today")):
            return today.isoformat()
        if low.startswith(("gisteren", "hier", "yesterday")):
            return (today - timedelta(days=1)).isoformat()
        if low.startswith(("eergisteren", "avant-hier")):
            return (today - timedelta(days=2)).isoformat()
        m = re.match(r"(\d{1,2})\s+([a-zéû]+)\.?\s+(\d{2,4})", low)
        if m:
            month = DUTCH_MONTHS.get(m.group(2)[:3]) or DUTCH_MONTHS.get(m.group(2)[:4])
            year = int(m.group(3))
            year = year + 2000 if year < 100 else year
            if month:
                try:
                    return date(year, month, int(m.group(1))).isoformat()
                except ValueError:
                    return ""
        return raw[:10]

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
