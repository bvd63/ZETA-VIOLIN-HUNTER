"""
Schibsted / Vend "recommerce" marketplaces — FINN (NO), Tori (FI), DBA (DK),
Blocket (SE).

Verified 2026-09-07: all four are reachable from EU IPs and embed the result
list as schema.org JSON-LD (ItemList of Product with name, description, url,
image, offers.price/priceCurrency), so one parser covers all of them.
"""

import json
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_HEADERS
from filters import has_zeta_signal

log = logging.getLogger(__name__)

# (label, search url, query param, country, currency, keywords)
SITES = [
    ("FINN", "https://www.finn.no/recommerce/forsale/search", "q", "Norway", "NOK",
     ["zeta fiolin", "zeta violin", "elektrisk fiolin zeta", "zeta strados"]),
    ("Tori", "https://www.tori.fi/recommerce/forsale/search", "q", "Finland", "EUR",
     ["zeta viulu", "zeta violin", "sähköviulu zeta", "zeta strados"]),
    ("DBA", "https://www.dba.dk/soeg/", "soeg", "Denmark", "DKK",
     ["zeta violin", "elektrisk violin zeta", "zeta strados"]),
    ("Blocket", "https://www.blocket.se/annonser/hela_sverige", "q", "Sweden", "SEK",
     ["zeta fiol", "zeta violin", "elfiol zeta", "zeta strados"]),
]


class SchibstedScraper(BaseScraper):
    name = "FINN/Tori/DBA/Blocket"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with self.make_client(headers=BROWSER_HEADERS) as client:
            for label, url, param, country, currency, keywords in SITES:
                for kw in keywords:
                    try:
                        resp = await client.get(url, params={param: kw})
                        if resp.status_code != 200:
                            log.warning(f"{label} '{kw}' HTTP {resp.status_code}")
                            continue
                        products = self._extract_products(resp.text)
                        self.fetched += len(products)

                        for p in products:
                            item_url = p.get("url") or ""
                            title = (p.get("name") or "").strip()
                            description = (p.get("description") or "").strip()
                            if not item_url or not title:
                                continue
                            if not has_zeta_signal(title + " " + description):
                                continue
                            unique_id = self._make_id(label.lower(), item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            offers = p.get("offers") or {}
                            if isinstance(offers, list):
                                offers = offers[0] if offers else {}
                            amount = offers.get("price")
                            price = f"{amount} {offers.get('priceCurrency') or currency}" if amount else "N/A"
                            image = p.get("image") or ""
                            if isinstance(image, list):
                                image = image[0] if image else ""

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
                                "location": country,
                                "url": item_url,
                                "description": description[:300],
                                "image_url": image if isinstance(image, str) else "",
                                "relevance_score": self._relevance_score(title, description),
                            })
                    except Exception as e:
                        log.warning(f"{label} '{kw}' error: {e}")

        log.info(f"FINN/Tori/DBA/Blocket: {len(results)} listings found")
        return results

    @staticmethod
    def _extract_products(html: str) -> list:
        """Return schema.org Product dicts from any JSON-LD ItemList on the page."""
        soup = BeautifulSoup(html, "lxml")
        products = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for block in data if isinstance(data, list) else [data]:
                if not isinstance(block, dict):
                    continue
                entity = block.get("mainEntity") if isinstance(block.get("mainEntity"), dict) else block
                for element in entity.get("itemListElement") or []:
                    item = element.get("item") if isinstance(element, dict) else None
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        products.append(item)
        return products
