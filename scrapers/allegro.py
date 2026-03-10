"""
Allegro.pl scraper — Polish classifieds.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class AllegroPLScraper(BaseScraper):
    name = "Allegro"

    POLISH_CITIES = [
        "Warsaw", "Krakow", "Wroclaw", "Poznan", "Gdansk",
        "Szczecin", "Bydgoszcz", "Lodz", "Lublin", "Katowice",
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta skrzypce", "skrzypce elektryczne Zeta",
            "skrzypce elektryczne", "skrzypce MIDI",
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            for city in self.POLISH_CITIES:
                for kw in keywords:
                    try:
                        url = "https://allegro.pl/listing"
                        resp = await client.get(url, params={"string": f"{kw} {city}"})
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("a[href*='/oferta/']")

                        for link_el in listings:
                            item_url = (link_el.get("href") or "").strip()
                            if not item_url:
                                continue
                            if item_url.startswith("/"):
                                item_url = "https://allegro.pl" + item_url

                            unique_id = self._make_id("allegro", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = (link_el.get_text(" ", strip=True) or "").strip()
                            if not title:
                                continue

                            card_text = ""
                            parent = link_el.parent
                            if parent is not None:
                                card_text = parent.get_text(" ", strip=True)

                            price = self._extract_price(card_text) or "N/A"

                            if self._is_excluded(title):
                                continue
                            if price != "N/A" and not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title):
                                continue

                            score = self._relevance_score(title, card_text)
                            if score < 1:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": f"Allegro ({city})",
                                "title": title,
                                "price": price,
                                "location": city,
                                "url": item_url,
                                "description": card_text[:300],
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Allegro {city} '{kw}' error: {e}")

        if not results:
            results = await self._fallback_search(seen_ids)
        if not results:
            results = await self._google_fallback(seen_ids)

        return results

    def _extract_price(self, text: str) -> str:
        import re
        m = re.search(r"\d[\d\s\.,]*\s?(?:zł|PLN)", text, re.IGNORECASE)
        return m.group(0).strip() if m else ""

    async def _fallback_search(self, seen_ids: set) -> list:
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        queries = [
            "site:allegro.pl zeta skrzypce",
            "site:allegro.pl skrzypce elektryczne zeta",
            "site:allegro.pl zeta violin",
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
                        if not item_url or "allegro.pl" not in item_url:
                            continue

                        unique_id = self._make_id("allegro", item_url)
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
                            "platform": "Allegro",
                            "title": title,
                            "price": "N/A",
                            "location": "Poland",
                            "url": item_url,
                            "description": "Found via search fallback",
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Allegro fallback '{q}' error: {e}")

        return results

    async def _google_fallback(self, seen_ids: set) -> list:
        if not Config.GOOGLE_API_KEY or not Config.GOOGLE_CSE_ID:
            return []

        results = []
        queries = [
            "site:allegro.pl zeta skrzypce",
            "site:allegrolokalnie.pl zeta skrzypce",
            "site:allegro.pl skrzypce elektryczne",
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
                        if not item_url:
                            continue
                        if "allegro.pl" not in item_url and "allegrolokalnie.pl" not in item_url:
                            continue

                        unique_id = self._make_id("allegro", item_url)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        if self._is_excluded(title):
                            continue

                        score = self._relevance_score(title, snippet)
                        if score < 1:
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": "Allegro",
                            "title": title,
                            "price": self._extract_price(snippet) or "N/A",
                            "location": "Poland",
                            "url": item_url,
                            "description": snippet[:300],
                            "relevance_score": score,
                        })
                except Exception as e:
                    log.warning(f"Allegro Google fallback '{q}' error: {e}")

        return results
