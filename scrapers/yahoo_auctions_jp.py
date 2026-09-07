"""
Yahoo Auctions Japan (ヤフオク!) — web search scraper.

The official Auctions API is dead (403) and the website blocks EEA/UK IPs,
but it answers from Railway's Singapore egress (verified in the startup
reachability log, HTTP 200 / 221 KB). Japan is one of the largest second-hand
Zeta markets. Skips itself when the container egress is in the EEA/UK.

Parsing strategy: <li class="Product"> cards (a.Product__titleLink,
span.Product__priceValue, img.Product__imageData); fallback = every anchor to
page.auctions.yahoo.co.jp/jp/auction/{id}. When nothing parses on a 200 page,
the log prints the most common CSS classes so the selectors can be fixed
from the Railway logs without local access.
"""

import logging
import re
from collections import Counter
from urllib.parse import quote
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_UA
from config import Config
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"
KEYWORDS = [
    "ZETA バイオリン",
    "ゼータ バイオリン",
    "ゼータ ヴァイオリン",
    "ZETA エレキバイオリン",
    "zeta violin",
    "zeta strados",
]
AUCTION_RX = re.compile(r"page\.auctions\.yahoo\.co\.jp/jp/auction/([a-z]\d+)", re.I)
# 未使用 = unused (new); 未使用に近い = "nearly unused" and 新品同様 = "like new" are USED
NEW_JP_RX = re.compile(r"未使用(?!に近い)|新品(?!同様)")

EEA_UK = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
    "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "GB", "CH",
}

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
}


class YahooAuctionsJPScraper(BaseScraper):
    name = "Yahoo Auctions JP"

    def is_configured(self) -> bool:
        # Blocked for European egress; only meaningful from Asia/US.
        return bool(Config.EGRESS_COUNTRY) and Config.EGRESS_COUNTRY.upper() not in EEA_UK

    async def search(self) -> list:
        if not self.is_configured():
            log.info(f"Yahoo Auctions JP: egress {Config.EGRESS_COUNTRY or '?'} is EEA/UK-blocked — skipping")
            return []

        results = []
        seen_ids = set()
        async with self.make_client(headers=HEADERS) as client:
            for kw in KEYWORDS:
                try:
                    resp = await client.get(SEARCH_URL, params={
                        "p": kw, "va": kw, "n": 50, "s1": "new", "o1": "d", "exflg": 1, "b": 1,
                    })
                    if resp.status_code != 200:
                        log.warning(f"Yahoo Auctions JP '{kw}' HTTP {resp.status_code}")
                        continue
                    items = self._parse(resp.text)
                    if not items and len(resp.text) > 50000:
                        self._log_structure(resp.text, kw)
                    self.fetched += len(items)

                    for item in items:
                        if not has_zeta_signal(item["title"]):
                            continue
                        unique_id = self._make_id("yahoo_jp", item["auction_id"])
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)
                        if self._is_excluded(item["title"]):
                            continue
                        if item["price"] != "N/A" and not self._price_in_range(item["price"]):
                            continue
                        results.append({
                            "id": unique_id,
                            "platform": "Yahoo Auctions JP",
                            "title": item["title"],
                            "price": item["price"],
                            "location": "Japan",
                            "url": item["url"],
                            "description": item["extra"][:300],
                            "condition": "New" if NEW_JP_RX.search(item["extra"]) else "",
                            "image_url": item["image"],
                            "relevance_score": self._relevance_score(item["title"]),
                        })
                except Exception as e:
                    log.warning(f"Yahoo Auctions JP '{kw}' error: {e}")

        log.info(f"Yahoo Auctions JP: {len(results)} listings found")
        return results

    @staticmethod
    def _parse(html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        out, seen = [], set()

        def push(auction_id, title, price, image, url, extra):
            if not auction_id or not title or auction_id in seen:
                return
            seen.add(auction_id)
            out.append({"auction_id": auction_id, "title": title.strip(), "price": price or "N/A",
                        "image": image or "", "url": url, "extra": extra})

        # Primary: product cards
        for card in soup.select("li.Product, div.Product"):
            a = card.select_one("a.Product__titleLink, a[href*='/jp/auction/']")
            if not a:
                continue
            href = a.get("href", "")
            m = AUCTION_RX.search(href)
            if not m:
                continue
            title = a.get("title") or a.get_text(" ", strip=True)
            price_el = card.select_one(".Product__priceValue, .Product__price")
            price = price_el.get_text(" ", strip=True) if price_el else ""
            price = re.sub(r"\s+", "", price.split("円")[0]) + "円" if "円" in price else price
            img = card.select_one("img")
            image = (img.get("src") or img.get("data-src") or "") if img else ""
            push(m.group(1), title, price, image, href.split("?")[0], card.get_text(" ", strip=True)[:400])

        # Fallback: any auction anchor
        if not out:
            for a in soup.select("a[href*='page.auctions.yahoo.co.jp/jp/auction/']"):
                m = AUCTION_RX.search(a.get("href", ""))
                title = a.get("title") or a.get_text(" ", strip=True)
                if not m or len(title) < 4:
                    continue
                parent = a.find_parent(["li", "div"]) or a
                text = parent.get_text(" ", strip=True)
                pm = re.search(r"([\d,]+)\s*円", text)
                img = parent.select_one("img") if parent else None
                push(m.group(1), title, f"{pm.group(1)}円" if pm else "", (img.get("src") if img else ""),
                     a.get("href", "").split("?")[0], text[:400])
        return out

    @staticmethod
    def _log_structure(html: str, kw: str) -> None:
        """Nothing parsed although the page is large: print the dominant CSS
        classes so the selectors can be adjusted from the Railway logs."""
        try:
            soup = BeautifulSoup(html, "lxml")
            classes = Counter()
            for tag in soup.find_all(["li", "div", "a"], class_=True):
                for c in tag.get("class", []):
                    if "roduct" in c or "item" in c.lower() or "auction" in c.lower():
                        classes[c] += 1
            anchors = len(soup.select("a[href*='/jp/auction/']"))
            log.warning(f"Yahoo Auctions JP '{kw}': 0 parsed from {len(html)//1024} KB; auction anchors={anchors}; "
                        f"top classes={classes.most_common(12)}")
        except Exception as e:
            log.debug(f"Yahoo structure log failed: {e}")
