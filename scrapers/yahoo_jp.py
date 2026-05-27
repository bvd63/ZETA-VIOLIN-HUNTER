"""
Yahoo Auctions Japan scraper — ヤフオク! (Yahuoku)
Japan's largest auction platform. Strong second-hand instrument culture;
professional musicians frequently sell Zeta violins here.

Uses the official Yahoo Japan Auctions API (V2/json/search).
This API is NOT geo-blocked (unlike the website which blocks EEA/UK since 2022).

Setup:
  1. Create a free Yahoo Japan account at yahoo.co.jp
  2. Register a developer app at https://developer.yahoo.co.jp/webapi/auctions/
  3. Set permitted URL to * (or your Railway domain)
  4. Add YAHOO_JP_APP_ID to Railway Variables

Without YAHOO_JP_APP_ID the scraper skips gracefully.
"""

import httpx
import logging
import re
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

YAHOO_API_URL = "https://auctions.yahooapis.jp/AuctionWebService/V2/json/search"

KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta JV44",
    "Zeta SV24",
    "Zeta Jazz Fusion",
    "ゼータ バイオリン",
    "ゼータ エレキバイオリン",
    "エレキバイオリン Zeta",
]

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
    "ゼータ",
]

HEADERS = {
    "User-Agent": "ZetaViolinHunter/1.0",
}


class YahooJPScraper(BaseScraper):
    name = "Yahoo Auctions JP"

    async def search(self) -> list:
        if not Config.YAHOO_JP_APP_ID:
            log.info(
                "YAHOO_JP_APP_ID not set — skipping Yahoo Auctions JP. "
                "Register free at https://developer.yahoo.co.jp/webapi/auctions/"
            )
            return []

        results = []
        seen_ids: set = set()

        async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
            for kw in KEYWORDS:
                try:
                    params = {
                        "appid": Config.YAHOO_JP_APP_ID,
                        "query": kw,
                        "results": 100,
                        "sort": "End",
                        "order": "D",
                    }
                    resp = await client.get(YAHOO_API_URL, params=params)

                    if resp.status_code == 400:
                        log.warning(
                            f"Yahoo JP API: 400 Bad Request for '{kw}' — "
                            "check YAHOO_JP_APP_ID and permitted URL setting"
                        )
                        break
                    if resp.status_code != 200:
                        log.warning(f"Yahoo JP '{kw}' HTTP {resp.status_code}")
                        continue

                    data = resp.json()
                    # Response: {"ResultSet": {"Result": {"Item": [...]}}}
                    result_set = data.get("ResultSet", {}).get("Result", {})
                    items = result_set.get("Item", [])
                    if isinstance(items, dict):
                        # Single result comes as dict, not list
                        items = [items]

                    for item in items:
                        auction_id = item.get("AuctionID", "")
                        title = item.get("Title", "")
                        if not auction_id or not title:
                            continue

                        title_lower = title.lower()
                        if not any(sig in title_lower for sig in ZETA_SIGNALS):
                            continue

                        unique_id = self._make_id("yahoo_jp", auction_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        url = item.get("AuctionItemUrl", f"https://page.auctions.yahoo.co.jp/jp/auction/{auction_id}")
                        price_val = item.get("CurrentPrice", item.get("Price", ""))
                        price = f"¥{price_val}" if price_val else "N/A"
                        category = item.get("Category", "")

                        if self._is_excluded(title):
                            continue
                        if not self._price_in_range(str(price_val)):
                            continue
                        if not self._year_in_range(title):
                            continue

                        score = self._relevance_score(title)
                        results.append({
                            "id": unique_id,
                            "platform": "Yahoo Auctions JP",
                            "title": title,
                            "price": price,
                            "location": "Japan",
                            "url": url,
                            "description": category,
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Yahoo JP '{kw}' error: {e}")

        log.info(f"Yahoo Auctions JP: {len(results)} listings found")
        return results
