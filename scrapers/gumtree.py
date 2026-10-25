"""
Gumtree UK scraper — server-rendered result tiles.

Supports both legacy article tiles and the 2026 div/anchor/article cards.
Reachability from a Romanian connection does not imply Railway access.
"""

from keywords import market_queries

import logging
import re
import httpx
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_HEADERS
from filters import has_zeta_signal

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.gumtree.com/search"
# "electric violin" is broad on purpose: it always returns tiles (so the
# watchdog sees the site alive) and the Zeta-signal filter picks any Zeta.
PRICE_RX = re.compile(r"£\s?[\d,]+(?:\.\d{2})?")


class GumtreeScraper(BaseScraper):
    name = "Gumtree UK"

    async def _response_is_healthy(self, response: httpx.Response) -> bool:
        # The site's 247 anti-bot response contains no usable results. It must
        # not be reported as a healthy empty search just because it is <400.
        return response.status_code == 200 or 300 <= response.status_code < 400

    async def search(self) -> list:
        results = []
        seen_ids = set()
        headers = {**BROWSER_HEADERS, "Accept-Language": "en-GB,en;q=0.9"}

        async with self.make_client(headers=headers) as client:
            for kw in market_queries("en", broad=True, limit=8, extra=("electric violin",)):
                try:
                    resp = await client.get(SEARCH_URL, params={"search_category": "all", "q": kw})
                    if resp.status_code != 200:
                        # Gumtree's anti-bot answers odd codes (247, 403) intermittently — one retry
                        import asyncio
                        await asyncio.sleep(3)
                        resp = await client.get(SEARCH_URL, params={"search_category": "all", "q": kw})
                    if resp.status_code != 200:
                        log.warning(f"Gumtree '{kw}' HTTP {resp.status_code}")
                        continue
                    try:
                        tiles = self._extract(resp.text)
                    except ValueError as exc:
                        self.content_failures.append(str(exc))
                        log.warning("Gumtree '%s': %s", kw, exc)
                        continue
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
                            "description": tile["description"],
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
        seen = set()
        for art in soup.select("[data-q='search-result'], [data-testid='rh-standard-card-listing']"):
            a = next((link for link in art.select("a[href]")
                      if re.match(r"^/p/(?:[^/]+/)+\d+/?$",
                                  urlsplit(urljoin(SEARCH_URL, link['href'])).path)), None)
            if not a:
                continue
            parsed = urlsplit(urljoin(SEARCH_URL, a['href']))
            if parsed.hostname not in ("gumtree.com", "www.gumtree.com"):
                continue
            url = urlunsplit(('https', 'www.gumtree.com', parsed.path.rstrip('/'), '', ''))
            if url in seen:
                continue
            seen.add(url)
            title_el = art.select_one("[data-q='tile-title'], [data-testid='rh-standard-card-title'], h2, h3")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            desc_el = art.select_one("[data-q='tile-description'], [data-testid='rh-standard-card-description'], p")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""
            loc_el = art.select_one("[data-q='tile-location']")
            location = loc_el.get_text(" ", strip=True) if loc_el else ""
            text = art.get_text(" ", strip=True)
            if not title:
                # First sentence of the tile text is the title
                title = text.split(" £")[0][:120]
            price_el = art.select_one("[data-q='tile-price'], [data-testid='rh-standard-card-price']")
            price_m = PRICE_RX.search(price_el.get_text(" ", strip=True) if price_el else text)
            img = art.select_one("img")
            image = (img.get("src") or img.get("data-src") or "") if img else ""
            tiles.append({
                "url": url, "title": title, "description": description or text,
                "location": location, "price": price_m.group(0) if price_m else "N/A",
                "image": image if image.startswith("http") else "", "text": text,
            })
        if not tiles:
            # The current public empty page explicitly says "0 ads for ...".
            # A JS/challenge shell or another layout must remain an error.
            h1 = soup.find('h1')
            if not h1 or not re.match(r'^0 ads\b', h1.get_text(' ', strip=True), re.I):
                raise ValueError("Gumtree results unrecognized (access or layout change)")
        return tiles
