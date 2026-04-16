"""
Leboncoin.fr scraper — France's largest classifieds.
Uses Playwright to bypass anti-bot, then extracts __NEXT_DATA__ JSON.
"""

import asyncio
import json
import logging
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

KEYWORDS = [
    "Zeta violon",
    "Zeta violin",
    "Zeta Strados",
    "violon electrique Zeta",
]

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
]

SEARCH_URL = "https://www.leboncoin.fr/recherche?text={keyword}&category=26"


class LeboncoinScraper(BaseScraper):
    name = "Leboncoin"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.warning("Playwright not available — skipping Leboncoin")
            return []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/131.0.0.0 Safari/537.36",
                    locale="fr-FR",
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                for kw in KEYWORDS:
                    try:
                        url = SEARCH_URL.format(keyword=kw.replace(" ", "+"))
                        await page.goto(url, wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(2)

                        # Dismiss cookie/GDPR banner
                        for selector in [
                            "button#didomi-notice-agree-button",
                            "button:has-text('Tout accepter')",
                            "button:has-text('Accept')",
                            "[aria-label='Tout accepter']",
                        ]:
                            try:
                                btn = page.locator(selector)
                                if await btn.count() > 0:
                                    await btn.first.click()
                                    await asyncio.sleep(1)
                                    break
                            except Exception:
                                continue

                        # Extract __NEXT_DATA__ JSON
                        next_data = await page.evaluate("""() => {
                            const el = document.getElementById('__NEXT_DATA__');
                            return el ? el.textContent : null;
                        }""")

                        if not next_data:
                            # Fallback: parse visible listing links
                            content = await page.content()
                            results.extend(
                                self._parse_html_fallback(content, seen_ids)
                            )
                            continue

                        try:
                            data = json.loads(next_data)
                        except json.JSONDecodeError:
                            log.warning(f"Leboncoin '{kw}': invalid JSON")
                            continue

                        ads = self._extract_ads(data)
                        for ad in ads:
                            title = (ad.get("subject") or ad.get("title") or "").strip()
                            body = (ad.get("body") or ad.get("description") or "").strip()

                            ad_url = ""
                            list_id = ad.get("list_id") or ad.get("id")
                            slug = ad.get("url") or ""
                            if slug:
                                ad_url = slug if slug.startswith("http") else f"https://www.leboncoin.fr{slug}"
                            elif list_id:
                                ad_url = f"https://www.leboncoin.fr/ad/instruments_de_musique/{list_id}.htm"

                            if not ad_url or not title:
                                continue

                            # Strict Zeta check
                            ad_text = (title + " " + body).lower()
                            if not any(sig in ad_text for sig in ZETA_SIGNALS):
                                continue

                            unique_id = self._make_id("leboncoin", ad_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            price = self._get_price(ad)
                            location = self._get_location(ad)

                            full_text = f"{title} {body}"
                            if self._is_excluded(full_text):
                                continue
                            if self._is_excluded_location(location):
                                continue
                            if price != "N/A" and not self._price_in_range(price):
                                continue
                            if not self._year_in_range(full_text):
                                continue

                            score = self._relevance_score(title, body)
                            if score < 2:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": "Leboncoin",
                                "title": title,
                                "price": price,
                                "location": location,
                                "url": ad_url,
                                "description": body[:300],
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Leboncoin '{kw}' error: {e}")

                await browser.close()

        except Exception as e:
            log.error(f"Leboncoin Playwright error: {e}")

        log.info(f"Leboncoin: {len(results)} listings found")
        return results

    def _extract_ads(self, data: dict) -> list:
        """Navigate Leboncoin __NEXT_DATA__ JSON to find ads."""
        ads = []
        try:
            props = data.get("props", {}).get("pageProps", {})
            for key_path in [
                lambda p: p.get("searchData", {}).get("ads", []),
                lambda p: p.get("initialProps", {}).get("searchData", {}).get("ads", []),
                lambda p: p.get("ads", []),
                lambda p: p.get("results", {}).get("ads", []),
                lambda p: p.get("searchResults", {}).get("ads", []),
            ]:
                try:
                    found = key_path(props)
                    if isinstance(found, list) and found:
                        ads.extend(found)
                        break
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"Leboncoin JSON nav error: {e}")
        return ads

    def _parse_html_fallback(self, html_content: str, seen_ids: set) -> list:
        """Fallback: parse listing links from HTML if no __NEXT_DATA__."""
        results = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            links = soup.select('a[href*="/ad/"]')
            for link in links:
                href = link.get("href", "")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = "https://www.leboncoin.fr" + href

                title = link.get_text(strip=True)
                if not title:
                    continue

                ad_text = title.lower()
                if not any(sig in ad_text for sig in ZETA_SIGNALS):
                    continue

                unique_id = self._make_id("leboncoin", href)
                if unique_id in seen_ids:
                    continue
                seen_ids.add(unique_id)

                score = self._relevance_score(title)
                if score < 2:
                    continue

                results.append({
                    "id": unique_id,
                    "platform": "Leboncoin",
                    "title": title,
                    "price": "N/A",
                    "location": "France",
                    "url": href,
                    "description": "",
                    "relevance_score": score,
                })
        except Exception as e:
            log.warning(f"Leboncoin HTML fallback error: {e}")
        return results

    def _get_price(self, ad: dict) -> str:
        try:
            p = ad.get("price", [])
            if isinstance(p, list) and p:
                return f"{p[0]} EUR"
            if isinstance(p, (int, float)):
                return f"{p} EUR"
            attrs = ad.get("attributes", [])
            if isinstance(attrs, list):
                for a in attrs:
                    if isinstance(a, dict) and a.get("key") == "price":
                        return f"{a.get('value', '?')} EUR"
        except Exception:
            pass
        return "N/A"

    def _get_location(self, ad: dict) -> str:
        try:
            loc = ad.get("location", {})
            if isinstance(loc, dict):
                parts = [loc.get("city", ""), loc.get("department_name", "")]
                return ", ".join(p for p in parts if p)
        except Exception:
            pass
        return "France"
