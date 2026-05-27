"""
Yahoo Auctions Japan scraper — ヤフオク! (Yahuoku)
Japan's largest auction platform. Strong second-hand instrument culture;
professional musicians frequently sell Zeta violins here.

No official public API — uses standard HTML search pages which are
publicly accessible without login.
Search URL: auctions.yahoo.co.jp/search/search?p=QUERY&n=100&s1=new&o1=d
"""

import httpx
import logging
import re
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"

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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


class YahooJPScraper(BaseScraper):
    name = "Yahoo Auctions JP"

    async def search(self) -> list:
        results = []
        seen_ids: set = set()

        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers=HEADERS
        ) as client:
            for kw in KEYWORDS:
                try:
                    params = {
                        "p": kw,
                        "va": kw,
                        "n": 100,
                        "s1": "new",
                        "o1": "d",
                        "mode": 1,
                        "istatus": 0,  # 0 = all (active + ended), 1 = active only
                    }
                    resp = await client.get(SEARCH_URL, params=params)
                    if resp.status_code != 200:
                        log.warning(f"Yahoo JP '{kw}' HTTP {resp.status_code}")
                        continue

                    listings = self._parse_page(resp.text)
                    if not listings:
                        log.debug(f"Yahoo JP '{kw}': 0 listings parsed")
                        continue

                    for item_id, title, price, url in listings:
                        # Strict Zeta signal check on title
                        title_lower = title.lower()
                        if not any(sig in title_lower for sig in ZETA_SIGNALS):
                            continue

                        unique_id = self._make_id("yahoo_jp", item_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue
                        if not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title):
                            continue

                        score = self._relevance_score(title)
                        results.append({
                            "id": unique_id,
                            "platform": "Yahoo Auctions JP",
                            "title": title,
                            "price": price or "N/A",
                            "location": "Japan",
                            "url": url,
                            "description": "",
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Yahoo JP '{kw}' error: {e}")

        log.info(f"Yahoo Auctions JP: {len(results)} listings found")
        return results

    def _parse_page(self, html: str) -> list:
        """Parse Yahoo Auctions Japan search results page.
        Returns list of (item_id, title, price, url) tuples.
        Tries multiple selector strategies to handle layout changes.
        """
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")

            # Strategy 1: Product cards (main 2023+ layout)
            # Each listing: <div class="Product"> or <li class="Product">
            items = soup.select("div.Product, li.Product, article.Product")

            for item in items:
                # Title link
                anchor = item.select_one(
                    "a.Product__titleLink, h3.Product__title a, a[href*='auctions.yahoo.co.jp']"
                )
                if not anchor:
                    continue

                url = anchor.get("href", "")
                if not url or "yahoo.co.jp" not in url:
                    continue

                title = anchor.get_text(strip=True)
                if not title:
                    # Try title element
                    title_el = item.select_one(
                        "h3.Product__title, .Product__titleText, .Product__name"
                    )
                    title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                # Extract item ID from URL
                item_id = self._extract_item_id(url)
                if not item_id:
                    item_id = self._make_id("yahoo_jp_url", url)

                # Price
                price_el = item.select_one(
                    "span.Product__price, .Product__priceValue, .Product__bid span"
                )
                price_text = price_el.get_text(strip=True) if price_el else ""
                price = self._normalize_price(price_text)

                results.append((item_id, title, price, url))

            # Strategy 2: table/list layout (older fallback)
            if not results:
                for row in soup.select("tr.Result, .auctionItem, li.bb_main"):
                    a = row.select_one("a[href*='yahoo.co.jp']")
                    if not a:
                        continue
                    url = a.get("href", "")
                    title = a.get_text(strip=True)
                    if not url or not title:
                        continue
                    item_id = self._extract_item_id(url) or self._make_id("yahoo_jp_url", url)
                    price_el = row.select_one("td.price, .price")
                    price = self._normalize_price(
                        price_el.get_text(strip=True) if price_el else ""
                    )
                    results.append((item_id, title, price, url))

        except Exception as e:
            log.warning(f"Yahoo JP page parse error: {e}")

        return results

    def _extract_item_id(self, url: str) -> str:
        """Extract auction item ID from Yahoo JP URL.
        Formats: /auction/ITEMID or /item/ITEMID
        """
        m = re.search(r"/(?:auction|item)/([a-zA-Z0-9]+)", url)
        return m.group(1) if m else ""

    def _normalize_price(self, text: str) -> str:
        """Normalize Yahoo JP price string to something like '¥1,500'."""
        if not text:
            return ""
        # Remove labels like "現在" (current), "即決" (buy now), keep numbers
        text = re.sub(r"[^\d,¥￥円]", " ", text).strip()
        m = re.search(r"[¥￥]?\s?[\d,]+", text)
        if m:
            val = m.group(0).replace(" ", "")
            if val and not val.startswith("¥"):
                val = "¥" + val
            return val
        return ""
