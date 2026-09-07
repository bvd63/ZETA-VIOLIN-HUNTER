"""
Mercari Japan scraper — uses mercapi library (async API wrapper).
Mercari JP is Japan's #1 C2C marketplace. Many vintage Zeta violins
end up in Japan due to the strong collector market.

Library: pip install mercapi (already in requirements.txt)
"""

import logging
from scrapers.base import BaseScraper
from filters import has_zeta_signal

log = logging.getLogger(__name__)

KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "ゼータ バイオリン",
    "ゼータ エレキバイオリン",
    "ZETA エレキバイオリン",
    "electric violin Zeta",
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
                    self.fetched += len(search_results.items)

                    for item in search_results.items:
                        # mercapi exposes the id as `id_` (it avoids shadowing the builtin)
                        item_id = str(
                            getattr(item, "id_", None) or getattr(item, "id", None)
                            or getattr(item, "item_id", None) or ""
                        )
                        if not item_id:
                            log.debug(f"Mercari JP: item without id — attrs: {dir(item)[:20]}")
                            continue

                        # Status looks like "ITEM_STATUS_SOLD_OUT" / "ITEM_STATUS_TRADING"
                        status = str(getattr(item, "status", "") or "").lower()
                        if "sold" in status or "trading" in status:
                            continue

                        name = getattr(item, "name", "") or ""
                        if not has_zeta_signal(name):
                            continue

                        unique_id = self._make_id("mercari_jp", item_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        price_val = getattr(item, "price", 0) or 0
                        price = f"{price_val} JPY"
                        thumbnails = getattr(item, "thumbnails", None) or []
                        image_url = str(thumbnails[0]) if thumbnails else ""

                        if self._is_excluded(name):
                            continue
                        if not self._price_in_range(str(price_val)):
                            continue
                        if not self._year_in_range(name):
                            continue

                        # C2C items have ids like "m12345678901"; Mercari Shops
                        # products have opaque ids and live under /shops/product/.
                        if item_id.startswith("m") and item_id[1:].isdigit():
                            url = f"https://jp.mercari.com/item/{item_id}"
                        else:
                            url = f"https://jp.mercari.com/shops/product/{item_id}"
                        created = getattr(item, "created", None)
                        date_posted = created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else ""

                        results.append({
                            "id": unique_id,
                            "platform": "Mercari JP",
                            "title": name,
                            "price": price,
                            "location": "Japan",
                            "url": url,
                            "description": "",
                            "date_posted": date_posted,
                            "image_url": image_url,
                            "relevance_score": self._relevance_score(name),
                        })
                except Exception as e:
                    log.warning(f"Mercari JP '{kw}' error: {e}")
        except Exception as e:
            log.error(f"Mercari JP error: {e}")

        log.info(f"Mercari JP: {len(results)} listings found")
        return results
