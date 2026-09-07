"""
Gumtree UK scraper — server-rendered result tiles.

Verified 2026-09-07: reachable from EU IPs; results are
<article data-q="search-result"> tiles with an anchor to /p/... and the
title/description/location/price as text.
"""

import logging
import re
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_HEADERS
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.gumtree.com/search"
KEYWORDS = ["zeta violin", "zeta electric violin", "zeta strados"]
PRICE_RX = re.compile(r"£\s?[\d,]+(?:\.\d{2})?")


class GumtreeScraper(BaseScraper):
    name = "Gumtree UK"

    async def search(self) -> list:
        results = []
        seen_ids = set()
        headers = {**BROWSER_HEADERS, "Accept-Language": "en-GB,en;q=0.9"}

        async with self.make_client(headers=headers) as client:
            for kw in KEYWORDS:
                try:
                    resp = await client.get(SEARCH_URL, params={"search_category": "all", "q": kw})
                    if resp.status_code != 200:
                        log.warning(f"Gumtree '{kw}' HTTP {resp.status_code}")
                        continue
                    tiles = self._extract(resp.text)
                    self.fetched += len(tiles)

                    for tile in tiles:
                        text = tile["text"]
                        if not has_zeta_signal(text):
                            continue
                        unique_id = self._make_id("gumtree", tile["url"])
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        title = tile["title"]
                        if self._is_excluded(title):
                            continue
                        price = tile["price"]
                        if price != "N/A" and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(text):
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Gumtree UK",
                            "title": title,
                            "price": price,
                            "location": tile["location"] or "United Kingdom",
                            "url": tile["url"],
                            "description": tile["description"][:300],
                            "image_url": tile["image"],
                            "relevance_score": self._relevance_score(title, tile["description"]),
                        })
                except Exception as e:
                    log.warning(f"Gumtree '{kw}' error: {e}")

        log.info(f"Gumtree UK: {len(results)} listings found")
        return results

    @staticmethod
    def _extract(html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        tiles = []
        for art in soup.select("article[data-q='search-result']"):
            a = art.select_one("a[href]")
            if not a:
                continue
            href = a.get("href", "")
            url = href if href.startswith("http") else f"https://www.gumtree.com{href}"
            title_el = art.select_one("[data-q='tile-title'], h2, h3")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            desc_el = art.select_one("[data-q='tile-description'], p")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""
            loc_el = art.select_one("[data-q='tile-location']")
            location = loc_el.get_text(" ", strip=True) if loc_el else ""
            text = art.get_text(" ", strip=True)
            if not title:
                # First sentence of the tile text is the title
                title = text.split(" £")[0][:120]
            price_m = PRICE_RX.search(text)
            img = art.select_one("img")
            image = (img.get("src") or img.get("data-src") or "") if img else ""
            tiles.append({
                "url": url, "title": title, "description": description or text[:300],
                "location": location, "price": price_m.group(0) if price_m else "N/A",
                "image": image if image.startswith("http") else "", "text": text,
            })
        return tiles
