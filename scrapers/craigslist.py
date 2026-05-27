"""
Craigslist scraper — HTML + JSON-LD based.

Craigslist removed RSS feeds ~2020. This scraper fetches standard HTML
search pages and extracts listings via:
  1. JSON-LD structured data (<script type="application/ld+json">) — primary
  2. New 2023 CSS selectors (div.cl-search-result) — fallback

Searches ~400 US cities discovered from geo.craigslist.org.
"""

import httpx
import logging
import asyncio
import re
import json
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

USA_GEO_INDEX = "https://geo.craigslist.org/iso/us"
STATIC_USA_FALLBACK = [
    "newyork", "losangeles", "chicago", "sfbay", "seattle",
    "boston", "miami", "austin", "denver", "portland",
    "philadelphia", "atlanta", "dallas", "houston", "phoenix",
]

KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta JV44",
    "Zeta SV24",
    "Zeta EV44",
    "Zeta JLP",
    "Zetta violin",
]

SEARCH_CATEGORIES = ["msa", "sss"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class CraigslistScraper(BaseScraper):
    name = "Craigslist"

    async def search(self) -> list:
        results = []
        seen_ids: set = set()

        async with httpx.AsyncClient(
            timeout=12, follow_redirects=True, headers=HEADERS
        ) as client:
            craigslist_cities = await self._load_usa_city_hosts(client)

            sem_size = max(10, min(40, Config.CRAIGSLIST_CONCURRENCY))
            if Config.CRAIGSLIST_MAX_US_CITIES > 0:
                craigslist_cities = craigslist_cities[:Config.CRAIGSLIST_MAX_US_CITIES]

            sem = asyncio.Semaphore(sem_size)
            batch_size = max(50, sem_size * 8)

            async def fetch_city_keyword(city: str, kw: str, category: str) -> list:
                city_results = []
                try:
                    async with sem:
                        url = f"https://{city}.craigslist.org/search/{category}"
                        params = {"query": kw, "sort": "date"}
                        resp = await client.get(url, params=params)
                    if resp.status_code != 200:
                        return city_results

                    for item_url, title, price, description in self._parse_listings(
                        resp.text, city
                    ):
                        unique_id = self._make_id("craigslist", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue
                        if price and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title):
                            continue

                        score = self._relevance_score(title, description)
                        city_results.append({
                            "id": unique_id,
                            "platform": f"Craigslist ({city})",
                            "title": title,
                            "price": price or "N/A",
                            "location": city.title(),
                            "url": item_url,
                            "description": description[:300],
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Craigslist {city} '{kw}' error: {e}")
                return city_results

            work_items = [
                (city, kw, cat)
                for city in craigslist_cities
                for kw in KEYWORDS
                for cat in SEARCH_CATEGORIES
            ]

            for i in range(0, len(work_items), batch_size):
                chunk = work_items[i:i + batch_size]
                tasks = [
                    asyncio.create_task(fetch_city_keyword(city, kw, cat))
                    for city, kw, cat in chunk
                ]
                chunk_results = await asyncio.gather(*tasks)
                for batch in chunk_results:
                    results.extend(batch)

        return results

    def _parse_listings(self, html: str, city: str) -> list:
        """Parse listings from a Craigslist search results page.
        Tries JSON-LD first (stable, structured), falls back to HTML selectors.
        Returns list of (url, title, price, description) tuples.
        """
        parsed = self._parse_jsonld(html)
        if not parsed:
            parsed = self._parse_html(html, city)
        return parsed

    def _parse_jsonld(self, html: str) -> list:
        """Extract listings from JSON-LD structured data embedded in the page.
        Craigslist adds schema.org/ItemList or Product objects since 2023.
        """
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    if not isinstance(data, dict):
                        continue

                    # Collect candidate items from different schema shapes
                    candidates = []
                    dtype = data.get("@type", "")
                    if dtype == "ItemList":
                        candidates = data.get("itemListElement", [])
                    elif dtype in ("Product", "Offer", "ListItem"):
                        candidates = [data]

                    for entry in candidates:
                        # entry may be a ListItem wrapping the actual item
                        item = entry.get("item", entry)
                        url = item.get("url", "") or entry.get("url", "")
                        name = item.get("name", "") or entry.get("name", "")
                        if not url or not name:
                            continue

                        # Price from offers block
                        price = ""
                        offers = item.get("offers", {})
                        if isinstance(offers, dict):
                            p = offers.get("price", "")
                            curr = offers.get("priceCurrency", "USD")
                            if p:
                                price = f"${p}" if curr == "USD" else f"{p} {curr}"

                        description = item.get("description", "")
                        results.append((url, name, price, description))

                except (json.JSONDecodeError, AttributeError, TypeError):
                    continue
        except Exception as e:
            log.debug(f"Craigslist JSON-LD parse error: {e}")
        return results

    def _parse_html(self, html: str, city: str) -> list:
        """Fallback HTML parser using post-2023 Craigslist CSS selectors.
        Old selectors (result-title, result-info) broke in the 2023 redesign.
        New selectors: li.cl-search-result, a.posting-title, span.priceinfo.
        """
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")
            items = soup.select("li.cl-search-result, div.cl-search-result")

            for item in items:
                anchor = item.select_one("a.posting-title, a.cl-app-anchor")
                if not anchor:
                    continue

                url = anchor.get("href", "")
                if not url:
                    continue
                if not url.startswith("http"):
                    url = f"https://{city}.craigslist.org{url}"

                # Title is usually in span.label inside the anchor
                title_el = anchor.select_one("span.label")
                title = (
                    title_el.get_text(strip=True)
                    if title_el
                    else anchor.get_text(strip=True)
                )
                if not title:
                    continue

                price_el = item.select_one("span.priceinfo, .price")
                price = price_el.get_text(strip=True) if price_el else ""

                results.append((url, title, price, ""))
        except Exception as e:
            log.debug(f"Craigslist HTML parse error ({city}): {e}")
        return results

    async def _load_usa_city_hosts(self, client: httpx.AsyncClient) -> list:
        try:
            resp = await client.get(USA_GEO_INDEX)
            if resp.status_code != 200:
                return STATIC_USA_FALLBACK

            hosts = set(re.findall(r"https?://([a-z0-9\-]+)\.craigslist\.org", resp.text))
            hosts.discard("www")
            hosts.discard("forums")
            hosts.discard("blog")
            if not hosts:
                return STATIC_USA_FALLBACK
            return sorted(hosts)
        except Exception:
            return STATIC_USA_FALLBACK
