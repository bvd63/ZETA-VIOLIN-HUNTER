"""
Guitar Center Used scraper — guitarcenter.com/Used/
Guitar Center is the largest US music retailer. They accept used instruments
on trade-in, including electric violins. Their used inventory is browsable
without login and is not heavily protected.

Search URL: guitarcenter.com/search#q=QUERY&t=used
"""

import httpx
import logging
import re
import json
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.guitarcenter.com/search"

KEYWORDS = [
    "zeta violin",
    "zeta electric violin",
    "zeta strados",
    "zeta jv44",
    "zeta sv24",
    "zetta violin",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
]


class GuitarCenterScraper(BaseScraper):
    name = "Guitar Center"

    def is_configured(self) -> bool:
        # guitarcenter.com TCP-blocks European datacenter IPs; runs when the
        # container itself is in the US or a US proxy is configured.
        return Config.has_us_egress()

    async def search(self) -> list:
        if not self.is_configured():
            log.info("Guitar Center: no US egress (US_PROXY_URL unset, container not in US) — skipping")
            return []

        results = []
        seen_ids: set = set()

        async with self.make_client(us_proxy=True, timeout=20, headers=HEADERS) as client:
            for kw in KEYWORDS:
                try:
                    # Guitar Center search with used filter
                    params = {"q": kw, "t": "used"}
                    resp = await client.get(SEARCH_URL, params=params)
                    if resp.status_code != 200:
                        log.warning(f"Guitar Center '{kw}' HTTP {resp.status_code}")
                        continue

                    listings = self._parse_page(resp.text)
                    if not listings:
                        log.debug(f"Guitar Center '{kw}': 0 listings parsed")
                        continue
                    self.fetched += len(listings)

                    for item_id, title, price, url, description in listings:
                        combined = (title + " " + description).lower()
                        if not any(sig in combined for sig in ZETA_SIGNALS):
                            continue

                        unique_id = self._make_id("guitar_center", item_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue
                        if not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title + " " + description):
                            continue

                        score = self._relevance_score(title, description)
                        results.append({
                            "id": unique_id,
                            "platform": "Guitar Center Used",
                            "title": title,
                            "price": price or "N/A",
                            "location": "USA",
                            "url": url,
                            "description": description[:300],
                            "relevance_score": score,
                        })

                except Exception as e:
                    log.warning(f"Guitar Center '{kw}' error: {e}")

        log.info(f"Guitar Center: {len(results)} listings found")
        return results

    def _parse_page(self, html: str) -> list:
        """Parse Guitar Center search results.
        Returns list of (item_id, title, price, url, description) tuples.
        Tries JSON-LD first, then HTML product cards.
        """
        results = self._parse_jsonld(html)
        if not results:
            results = self._parse_html(html)
        return results

    def _parse_jsonld(self, html: str) -> list:
        """Try JSON-LD / window.__INITIAL_STATE__ for product data."""
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")

            # Try JSON-LD product listings
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    if not isinstance(data, dict):
                        continue
                    dtype = data.get("@type", "")
                    candidates = []
                    if dtype == "ItemList":
                        candidates = data.get("itemListElement", [])
                    elif dtype == "Product":
                        candidates = [data]

                    for entry in candidates:
                        item = entry.get("item", entry)
                        name = item.get("name", "")
                        url = item.get("url", "")
                        if not name or not url:
                            continue
                        if not url.startswith("http"):
                            url = "https://www.guitarcenter.com" + url

                        item_id = re.search(r"/([^/]+)\.gc$", url)
                        item_id = item_id.group(1) if item_id else url

                        offers = item.get("offers", {})
                        price = ""
                        if isinstance(offers, dict):
                            p = offers.get("price", "")
                            if p:
                                price = f"${p}"

                        description = item.get("description", "")
                        results.append((item_id, name, price, url, description))
                except (json.JSONDecodeError, AttributeError):
                    continue
        except Exception as e:
            log.debug(f"Guitar Center JSON-LD error: {e}")
        return results

    def _parse_html(self, html: str) -> list:
        """Fallback: parse HTML product cards."""
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")

            # Product cards: div.product-card, article.product, li.product-item
            cards = soup.select(
                "div.product-card, article.product, li.product-item, "
                "div[data-testid='product-card'], div.search-result-item"
            )

            for card in cards:
                anchor = card.select_one("a[href*='/Used/']") or card.select_one("a[href]")
                if not anchor:
                    continue

                url = anchor.get("href", "")
                if not url:
                    continue
                if not url.startswith("http"):
                    url = "https://www.guitarcenter.com" + url
                if "guitarcenter.com" not in url:
                    continue

                # Title
                title_el = card.select_one(
                    "span.product-name, h3, h2, .product-title, [data-testid='product-name']"
                )
                title = title_el.get_text(strip=True) if title_el else anchor.get_text(strip=True)
                if not title:
                    continue

                # Price
                price_el = card.select_one(
                    "span.price, .product-price, [data-testid='product-price']"
                )
                price = price_el.get_text(strip=True) if price_el else ""

                # Description
                desc_el = card.select_one(".product-description, .product-subtitle")
                description = desc_el.get_text(strip=True) if desc_el else ""

                item_id = re.search(r"/([^/]+?)(?:\.gc)?$", url)
                item_id = item_id.group(1) if item_id else url

                results.append((item_id, title, price, url, description))

        except Exception as e:
            log.debug(f"Guitar Center HTML parse error: {e}")
        return results
