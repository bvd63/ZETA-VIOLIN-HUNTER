"""
ShopGoodwill.com scraper — Goodwill's US-wide online auction site.

Donated instruments are auctioned here daily and descriptions are searchable.
Verified 2026-09-07: the buyer API answers without authentication:
  POST https://buyerapi.shopgoodwill.com/api/Search/ItemListing  (JSON body)
Item URL: https://shopgoodwill.com/item/{itemId}
"""

import httpx
import logging
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal

log = logging.getLogger(__name__)

API_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"

# Broad queries; the Zeta-signal filter on title + description does the work.
QUERIES = ["zeta violin", "zeta strados", "zeta", "electric violin"]
PAGE_SIZE = 40
MAX_PAGES = 3

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://shopgoodwill.com",
    "Referer": "https://shopgoodwill.com/",
}


def _body(text: str, page: int) -> dict:
    return {
        "isSize": False, "isWeddingCatalog": False, "mainCategoryId": None, "catIds": "",
        "categoryId": None, "categoryLevel": 1, "categoryLevelNo": "1",
        "closedAuctionDaysBack": "7", "closedAuctionEndingDate": "",
        "highPrice": "999999", "lowPrice": "0", "page": page, "pageSize": PAGE_SIZE,
        "partNumber": "", "savedSearchId": 0, "searchBuyNowOnly": "",
        "searchCanadaShipping": "false", "searchClosedAuctions": "false",
        "searchDescriptions": "true", "searchInternationalShippingOnly": "false",
        "searchNoPickupOnly": "false", "searchOneCentShippingOnly": "false",
        "searchPickupOnly": "false", "searchText": text, "searchUSOnlyShipping": "false",
        "selectedCategoryIds": "", "selectedGroup": "", "selectedSellerIds": "",
        "sortColumn": "1", "sortDescending": "false", "useBuyerPrefs": "true",
    }


class ShopGoodwillScraper(BaseScraper):
    name = "ShopGoodwill"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
            for q in QUERIES:
                for page in range(1, MAX_PAGES + 1):
                    try:
                        resp = await client.post(API_URL, json=_body(q, page))
                        if resp.status_code != 200:
                            log.warning(f"ShopGoodwill '{q}' p{page} HTTP {resp.status_code}: {resp.text[:120]!r}")
                            break
                        search = resp.json().get("searchResults", {}) or {}
                        items = search.get("items", []) or []
                        if not items:
                            break
                        self.fetched += len(items)

                        for item in items:
                            item_id = str(item.get("itemId") or "")
                            title = (item.get("title") or "").strip()
                            description = (item.get("description") or "").strip()
                            if not item_id or not title:
                                continue
                            if not has_zeta_signal(title + " " + description):
                                continue
                            unique_id = self._make_id("shopgoodwill", item_id)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            price_val = item.get("buyNowPrice") or item.get("currentPrice") or item.get("minimumBid")
                            price = f"{price_val} USD" if price_val else "N/A"
                            if self._is_excluded(title):
                                continue
                            if price != "N/A" and not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title + " " + description):
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": "ShopGoodwill",
                                "title": title,
                                "price": price,
                                "location": "USA",
                                "url": f"https://shopgoodwill.com/item/{item_id}",
                                "description": description[:300],
                                "date_posted": str(item.get("startTime") or "")[:10],
                                "image_url": item.get("imageURL") or "",
                                "relevance_score": self._relevance_score(title, description),
                            })

                        if len(items) < PAGE_SIZE:
                            break
                    except Exception as e:
                        log.warning(f"ShopGoodwill '{q}' p{page} error: {e}")
                        break

        log.info(f"ShopGoodwill: {len(results)} listings found")
        return results
