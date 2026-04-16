"""
Mercari Japan scraper — uses mercapi library (async API wrapper).
Mercari JP is Japan's #1 C2C marketplace. Many vintage Zeta violins
end up in Japan due to the strong collector market.

Library: pip install mercapi (already in requirements.txt)
"""

import logging
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "ゼータ バイオリン",
    "ゼータ エレキバイオリン",
    "electric violin Zeta",
]

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
    "ゼータ",
]


class MercariJPScraper(BaseScraper):
    name = "Mercari JP"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        try:
            from mercapi import Mercapi
        except ImportError:
            log.warning("mercapi not installed — skipping Mercari JP")
            return []

        try:
            m = Mercapi()

            for kw in KEYWORDS:
                try:
                    search_results = await m.search(kw)

                    if not search_results or not search_results.items:
                        continue

                    for item in search_results.items:
                        item_id = str(item.id) if hasattr(item, 'id') else ""
                        if not item_id:
                            continue

                        # Skip sold items
                        status = getattr(item, 'status', None)
                        if status and str(status).lower() in ('sold_out', 'trading', 'sold'):
                            continue

                        name = getattr(item, 'name', '') or ''
                        price_val = getattr(item, 'price', 0) or 0
                        price = f"{price_val} JPY"

                        url = f"https://jp.mercari.com/item/{item_id}"

                        # Strict Zeta check
                        item_text = name.lower()
                        if not any(sig in item_text for sig in ZETA_SIGNALS):
                            continue

                        unique_id = self._make_id("mercari_jp", item_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(name):
                            continue
                        if not self._price_in_range(str(price_val)):
                            continue
                        if not self._year_in_range(name):
                            continue

                        score = self._relevance_score(name)
                        if score < 2:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Mercari JP",
                            "title": name,
                            "price": price,
                            "location": "Japan",
                            "url": url,
                            "description": "",
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Mercari JP '{kw}' error: {e}")

        except Exception as e:
            log.error(f"Mercari JP error: {e}")

        log.info(f"Mercari JP: {len(results)} listings found")
        return results
