"""
Craigslist scraper — searches major cities worldwide.
"""

import httpx
import logging
import asyncio
import re
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

USA_GEO_INDEX = "https://geo.craigslist.org/iso/us"
STATIC_USA_FALLBACK = [
    "newyork", "losangeles", "chicago", "sfbay", "seattle",
    "boston", "miami", "austin", "denver", "portland",
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


class CraigslistScraper(BaseScraper):
    name = "Craigslist"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }

        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            craigslist_cities = await self._load_usa_city_hosts(client)

            # Full coverage mode: by default scan all discovered US Craigslist hosts.
            sem_size = max(10, min(40, Config.CRAIGSLIST_CONCURRENCY))
            if Config.CRAIGSLIST_MAX_US_CITIES > 0:
                craigslist_cities = craigslist_cities[:Config.CRAIGSLIST_MAX_US_CITIES]

            sem = asyncio.Semaphore(sem_size)

            async def fetch_city_keyword(city: str, kw: str, category: str) -> list:
                city_results = []
                try:
                    async with sem:
                        url = f"https://{city}.craigslist.org/search/{category}"
                        params = {"query": kw, "sort": "date", "format": "rss"}
                        resp = await client.get(url, params=params)
                    if resp.status_code != 200:
                        return city_results

                    root = ET.fromstring(resp.text)
                    for item in root.findall("./channel/item"):
                        item_url = (item.findtext("link") or "").strip()
                        if not item_url:
                            continue

                        unique_id = self._make_id("craigslist", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        title = (item.findtext("title") or "").strip()
                        description = (item.findtext("description") or "").strip()
                        pub_date = (item.findtext("pubDate") or "").strip()
                        price = self._extract_price(title + " " + description)

                        if self._is_excluded(title):
                            continue
                        if price and not self._price_in_range(price):
                            continue
                        if not self._year_in_range(title):
                            continue

                        score = self._relevance_score(title, description)
                        if score < 1:
                            continue

                        city_results.append({
                            "id": unique_id,
                            "platform": f"Craigslist ({city}/{category})",
                            "title": title,
                            "price": price or "N/A",
                            "location": city.title(),
                            "url": item_url,
                            "description": description[:300],
                            "date_posted": pub_date,
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Craigslist {city} '{kw}' error: {e}")
                return city_results

            tasks = [
                fetch_city_keyword(city, kw, cat)
                for city in craigslist_cities
                for kw in KEYWORDS
                for cat in SEARCH_CATEGORIES
            ]
            batches = await asyncio.gather(*tasks)
            for batch in batches:
                results.extend(batch)

        if not results:
            results = await self._fallback_search(seen_ids)
        if not results:
            results = await self._google_fallback(seen_ids)

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

    def _extract_price(self, text: str) -> str:
        import re
        m = re.search(r"(?:\$|€|£)\s?\d[\d,]*(?:\.\d{2})?", text)
        return m.group(0) if m else ""

    async def _fallback_search(self, seen_ids: set) -> list:
        from urllib.parse import parse_qs, unquote, urlparse

        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        queries = [
            "site:craigslist.org zeta violin",
            "site:craigslist.org zeta electric violin",
            "site:craigslist.org strados violin",
            "site:craigslist.org zeta jv44",
            "site:craigslist.org zeta sv24",
            "site:craigslist.org zeta ev44",
        ]

        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            for q in queries:
                try:
                    resp = await client.get("https://duckduckgo.com/html/", params={"q": q})
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    for a in soup.select("a.result__a"):
                        item_url = (a.get("href") or "").strip()
                        title = (a.get_text(" ", strip=True) or "").strip()
                        if "duckduckgo.com/l/?" in item_url:
                            if item_url.startswith("//"):
                                item_url = "https:" + item_url
                            parsed = urlparse(item_url)
                            raw = parse_qs(parsed.query).get("uddg", [""])[0]
                            if raw:
                                item_url = unquote(raw)
                        if not item_url or "craigslist.org" not in item_url:
                            continue

                        unique_id = self._make_id("craigslist", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue

                        score = self._relevance_score(title)
                        if score < 1:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Craigslist",
                            "title": title,
                            "price": "N/A",
                            "location": "Unknown",
                            "url": item_url,
                            "description": "Found via search fallback",
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Craigslist fallback '{q}' error: {e}")

        return results

    async def _google_fallback(self, seen_ids: set) -> list:
        if not Config.GOOGLE_API_KEY or not Config.GOOGLE_CSE_ID:
            return []

        results = []
        queries = [
            "site:craigslist.org zeta violin",
            "site:craigslist.org zeta electric violin",
            "site:craigslist.org zeta jv44",
            "site:craigslist.org zeta sv24",
            "site:craigslist.org zeta ev44",
        ]

        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            for q in queries:
                try:
                    resp = await client.get(
                        "https://www.googleapis.com/customsearch/v1",
                        params={
                            "key": Config.GOOGLE_API_KEY,
                            "cx": Config.GOOGLE_CSE_ID,
                            "q": q,
                            "num": 10,
                        },
                    )
                    if resp.status_code != 200:
                        continue

                    for item in resp.json().get("items", []):
                        item_url = (item.get("link") or "").strip()
                        title = (item.get("title") or "").strip()
                        snippet = (item.get("snippet") or "").strip()
                        if not item_url or "craigslist.org" not in item_url:
                            continue

                        unique_id = self._make_id("craigslist", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        score = self._relevance_score(title, snippet)
                        if score < 1:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Craigslist",
                            "title": title,
                            "price": self._extract_price(snippet) or "N/A",
                            "location": "Unknown",
                            "url": item_url,
                            "description": snippet[:300],
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Craigslist Google fallback '{q}' error: {e}")

        return results
