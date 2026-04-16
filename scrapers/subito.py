"""
Subito.it scraper — Italian classifieds.
"""

import httpx
import logging
import json
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class SubitoScraper(BaseScraper):
    name = "Subito"

    ITALIAN_CITIES = [
        "Milan", "Rome", "Naples", "Turin", "Palermo",
        "Genoa", "Bologna", "Florence", "Bari", "Catania",
        "Venice", "Verona", "Messina", "Padua", "Trieste",
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violino",
            "Zeta violin",
            "Zeta Strados",
            "Zeta Jazz Fusion",
            "violino elettrico Zeta",
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            for city in self.ITALIAN_CITIES:
                for kw in keywords:
                    try:
                        url = "https://www.subito.it/annunci-italia/vendita/usato/"
                        resp = await client.get(url, params={"q": f"{kw} {city}"})
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        next_data_el = soup.find("script", id="__NEXT_DATA__")
                        if next_data_el and next_data_el.text:
                            try:
                                data = json.loads(next_data_el.text)
                                roots = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("items", {}).get("list", [])
                                for ad in self._extract_ads_from_tree(roots):
                                    item_url = ad.get("urls", {}).get("default") or ""
                                    title = (ad.get("subject") or "").strip()
                                    body = (ad.get("body") or "").strip()
                                    if not item_url or not title:
                                        continue

                                    # Strict Zeta check — ad's own text must mention Zeta
                                    ad_text = (title + " " + body).lower()
                                    zeta_signals = [
                                        "zeta", "zetta", "strados", "jv44", "jv45",
                                        "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
                                        "jean-luc ponty", "jlp", "jazz fusion",
                                    ]
                                    if not any(sig in ad_text for sig in zeta_signals):
                                        continue

                                    unique_id = self._make_id("subito", item_url)
                                    if unique_id in seen_ids:
                                        continue
                                    seen_ids.add(unique_id)

                                    price = self._extract_price_from_features(ad) or self._extract_price(body) or "N/A"
                                    location = ad.get("geo", {}).get("town", {}).get("value") or city

                                    if self._is_excluded(title):
                                        continue
                                    if price != "N/A" and not self._price_in_range(price):
                                        continue
                                    if not self._year_in_range(title + " " + body):
                                        continue

                                    score = self._relevance_score(title, body)
                                    if score < 2:
                                        continue

                                    results.append({
                                        "id": unique_id,
                                        "platform": f"Subito ({city})",
                                        "title": title,
                                        "price": price,
                                        "location": location,
                                        "url": item_url,
                                        "description": body[:300],
                                        "date_posted": ad.get("date") or "",
                                        "relevance_score": score,
                                    })
                            except Exception as e:
                                log.warning(f"Subito parse error for {city} '{kw}': {e}")

                    except Exception as e:
                        log.warning(f"Subito {city} '{kw}' error: {e}")

        if not results:
            results = await self._fallback_search(seen_ids)

        return results

    def _extract_price(self, text: str) -> str:
        import re
        m = re.search(r"(?:€|EUR)\s?\d[\d\.\,]*(?:,\d{2})?", text, re.IGNORECASE)
        return m.group(0) if m else ""

    def _extract_price_from_features(self, ad: dict) -> str:
        try:
            values = ad.get("features", {}).get("/price", {}).get("values", [])
            if values and isinstance(values[0], dict):
                return values[0].get("value", "")
        except Exception:
            pass
        return ""

    def _extract_ads_from_tree(self, node):
        if isinstance(node, dict):
            item = node.get("item")
            if isinstance(item, dict) and item.get("subject"):
                yield item
            for v in node.values():
                yield from self._extract_ads_from_tree(v)
        elif isinstance(node, list):
            for x in node:
                yield from self._extract_ads_from_tree(x)

    async def _fallback_search(self, seen_ids: set) -> list:
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        queries = [
            "site:subito.it zeta violino",
            "site:subito.it zeta violin",
            "site:subito.it zeta strados",
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
                        if not item_url or "subito.it" not in item_url:
                            continue

                        unique_id = self._make_id("subito", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue

                        score = self._relevance_score(title)
                        if score < 2:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Subito",
                            "title": title,
                            "price": "N/A",
                            "location": "Italy",
                            "url": item_url,
                            "description": "Found via search fallback",
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Subito fallback '{q}' error: {e}")

        return results
