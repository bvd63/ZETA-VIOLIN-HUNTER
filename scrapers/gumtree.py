"""
Gumtree UK scraper — server-rendered result tiles.

Supports both legacy article tiles and the 2026 div/anchor/article cards.
Reachability from a Romanian connection does not imply Railway access.
"""

from keywords import market_queries

import logging
import re
import httpx
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_HEADERS
from native_candidates import card_hints
from filters import has_zeta_signal
from browser_fetch import PublicSearchBrowser
from public_node_fetch import fetch as node_fetch
from primary_page_reserve import fresh_gumtree_search, remember_market_search

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.gumtree.com/search"
# "electric violin" is broad on purpose: it always returns tiles (so the
# watchdog sees the site alive) and the Zeta-signal filter picks any Zeta.
PRICE_RX = re.compile(r"£\s?[\d,]+(?:\.\d{2})?")
CARD_METADATA = "[data-q='tile-location'], [data-q='tile-price'], [data-testid='rh-standard-card-price'], [data-testid='rh-standard-card-location']"


def _card_identity_text(element):
    copy = BeautifulSoup(str(element), 'lxml')
    for metadata in copy.select(CARD_METADATA):
        metadata.decompose()
    return copy.get_text(' ',strip=True)


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

        async with self.make_client(headers=headers) as client, PublicSearchBrowser() as browser:
            for kw in market_queries("en", broad=True, limit=8, extra=("electric violin",)):
                try:
                    attempted_before, ok_before = self.requests_attempted, self.requests_ok
                    failures_before = len(self.failures)
                    content_before = len(self.content_failures)
                    resp = await client.get(SEARCH_URL, params={"search_category": "all", "q": kw})
                    if resp.status_code != 200:
                        import asyncio
                        await asyncio.sleep(3)
                        resp = await client.get(SEARCH_URL, params={"search_category": "all", "q": kw})
                    tiles = None
                    parse_error = None
                    if resp.status_code == 200:
                        try:
                            tiles = self._extract(resp.text)
                        except ValueError as exc:
                            parse_error = str(exc)
                            self.content_failures.append(parse_error)
                    if tiles is None and resp.status_code in (200, 247, 403):
                        self.requests_attempted += 1
                        try:
                            code, node_html, _ = await node_fetch(str(httpx.URL(SEARCH_URL,
                                params={'search_category':'all', 'q':kw})))
                            if code != 200:
                                self.failures.append(f'Node HTTP {code} (gumtree.com)')
                            else:
                                self.requests_ok += 1
                                try:
                                    tiles = self._extract(node_html)
                                except ValueError as exc:
                                    self.content_failures.append('Gumtree Node layout: ' + str(exc))
                                if tiles is not None:
                                    self.requests_recovered += max(0, self.requests_attempted-attempted_before-(self.requests_ok-ok_before))
                                    del self.failures[failures_before:]
                                    del self.content_failures[content_before:]
                                    log.info("Gumtree '%s': Node recovered %s cards", kw, len(tiles))
                        except Exception as exc:
                            self.content_failures.append('Gumtree Node fallback '+type(exc).__name__)
                    if tiles is None and (resp.status_code in (200, 247, 403)):
                        # Same exact query through ordinary Chromium. No login,
                        # CAPTCHA solving, proxy or reduced keyword coverage.
                        self.requests_attempted += 1
                        try:
                            code, html = await browser.fetch(str(httpx.URL(SEARCH_URL,
                                params={"search_category":"all", "q":kw})))
                            if code == 200:
                                self.requests_ok += 1
                                tiles = self._extract(html)
                                recovered = self.requests_attempted-attempted_before-(self.requests_ok-ok_before)
                                self.requests_recovered += max(0,recovered)
                                del self.failures[failures_before:]
                                del self.content_failures[content_before:]
                                log.info("Gumtree '%s': browser recovered query after HTTP %s (%s tiles)",
                                         kw,resp.status_code,len(tiles))
                            else:
                                self.failures.append(f"Browser HTTP {code} (gumtree.com)")
                        except Exception as exc:
                            self.content_failures.append('Gumtree browser fallback '+type(exc).__name__)
                            log.warning("Gumtree browser fallback failed (%s)",type(exc).__name__)
                    if tiles is None:
                        recovered=await fresh_gumtree_search(str(httpx.URL(SEARCH_URL,
                            params={'search_category':'all','q':kw})),client)
                        try:
                            tiles,complete=self._extract_exa(recovered)
                            remember_market_search(str(httpx.URL(SEARCH_URL,
                                params={'search_category':'all','q':kw})),recovered)
                            cached = recovered.get('_cached') is True
                            if cached:
                                self.content_failures.append('Gumtree: cached search observation; fresh coverage unverified')
                            if complete and not cached:
                                self.requests_recovered+=max(0,self.requests_attempted-attempted_before-(self.requests_ok-ok_before))
                                del self.failures[failures_before:]
                                del self.content_failures[content_before:]
                            elif not complete:
                                self.content_failures.append('Gumtree: Exa recovered partial result links')
                            log.info("Gumtree '%s': Exa %s recovered %s item links (complete=%s)",
                                     kw,'cached' if cached else 'live',len(tiles),complete)
                        except ValueError as exc:
                            self.content_failures.append(str(exc))
                    if tiles is None:
                        log.warning("Gumtree '%s' unresolved HTTP %s",kw,resp.status_code)
                        continue
                    self.fetched += len(tiles)

                    for tile in tiles:
                        text = tile["text"]
                        hints = card_hints(tile['title'], tile['description'], kw)
                        if hints is None and has_zeta_signal(tile.get('_discovery_text', text)):
                            # Preserve a weak lead found in an older layout;
                            # seller/location/other card text is not identity.
                            hints = {'_native_search_query': kw, '_query_identity_unverified': True,
                                     '_native_identity_hint': 'unscoped card text'}
                        if hints is None:
                            continue
                        if tile.get('_identity_unscoped'):
                            hints['_query_identity_unverified'] = True
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
                            **hints,
                        })
                except Exception as e:
                    log.warning(f"Gumtree '{kw}' error: {e}")

        log.info(f"Gumtree UK: {len(results)} listings found")
        return results

    @staticmethod
    def _extract_exa(result: dict) -> tuple:
        if result.get('_error'):
            raise ValueError(result['_error'])
        count=re.search(r'(?:^|\n)\s*(?:#+\s*)?([\d,]+)\s+ads?\s+for\b',result.get('text',''),re.I)
        if not count:
            raise ValueError('Gumtree: live search result count not verified')
        total=int(count.group(1).replace(',',''))
        tiles=[];seen=set()
        for link in (result.get('extras') or {}).get('links',[]):
            if not isinstance(link,str):
                continue
            parts=urlsplit(urljoin(SEARCH_URL,link))
            if (parts.scheme!='https' or parts.hostname not in ('gumtree.com','www.gumtree.com')
                    or not re.fullmatch(r'/p/(?:[^/]+/)+\d+/?',parts.path)):
                continue
            url=urlunsplit(('https','www.gumtree.com',parts.path.rstrip('/'),'',''))
            if url in seen:
                continue
            seen.add(url)
            title=unquote(parts.path.rstrip('/').split('/')[-2]).replace('-',' ')
            tiles.append({'url':url,'title':title,'text':title,'description':'','price':'N/A','location':'','image':''})
        if total>0 and not tiles:
            raise ValueError('Gumtree: results exist but live item links missing')
        return tiles,len(tiles)==total

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
            desc_el = art.select_one("[data-q='tile-description'], [data-testid='rh-standard-card-description']")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""
            loc_el = art.select_one("[data-q='tile-location']")
            location = loc_el.get_text(" ", strip=True) if loc_el else ""
            text = art.get_text(" ", strip=True)
            if not title:
                # Anchor text is a discovery clue if this layout has no scoped
                # title. It must be re-read on the primary page before alerting.
                title = a.get('title') or a.get('aria-label') or _card_identity_text(a)[:240]
            price_el = art.select_one("[data-q='tile-price'], [data-testid='rh-standard-card-price']")
            price_m = PRICE_RX.search(price_el.get_text(" ", strip=True) if price_el else text)
            img = art.select_one("img")
            image = (img.get("src") or img.get("data-src") or "") if img else ""
            tiles.append({
                # Seller/location/price text is not the item's description.
                # In particular "Zeta Road" must never establish its brand.
                "url": url, "title": title, "description": description,
                "location": location, "price": price_m.group(0) if price_m else "N/A",
                "image": image if image.startswith("http") else "", "text": text,
                '_identity_unscoped': title_el is None,
                '_discovery_text': _card_identity_text(art),
            })
        if not tiles:
            # The current public empty page explicitly says "0 ads for ...".
            # A JS/challenge shell or another layout must remain an error.
            h1 = soup.find('h1')
            if not h1 or not re.match(r'^0 ads\b', h1.get_text(' ', strip=True), re.I):
                raise ValueError("Gumtree results unrecognized (access or layout change)")
        return tiles
