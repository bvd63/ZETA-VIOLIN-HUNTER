"""
Kleinanzeigen.de scraper — Playwright headless Chromium.
Germany's largest classifieds. No login required.
"""

import asyncio
import logging
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

KEYWORDS = [
    "Zeta Violine",
    "Zeta Geige",
    "Zeta Strados",
    "Zeta electric violin",
    "Zeta Jazz Fusion",
]

SEARCH_URL = "https://www.kleinanzeigen.de/s-{keyword}/k0"


class KleinanzeigenScraper(BaseScraper):
    name = "Kleinanzeigen"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.warning("Playwright not available — skipping Kleinanzeigen")
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
                    locale="de-DE",
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                for kw in KEYWORDS:
                    try:
                        url = SEARCH_URL.format(keyword=kw.replace(" ", "-"))
                        await page.goto(url, wait_until="networkidle", timeout=30000)
                        await asyncio.sleep(2)

                        # Dismiss GDPR / cookie banner
                        for selector in [
                            "#gdpr-banner-accept",
                            "button#didomi-notice-agree-button",
                            "[data-testid='gdpr-banner-accept']",
                            "button:has-text('Alle akzeptieren')",
                            "button:has-text('Accept All')",
                        ]:
                            try:
                                btn = page.locator(selector)
                                if await btn.count() > 0:
                                    await btn.first.click()
                                    await asyncio.sleep(1)
                                    break
                            except Exception:
                                continue

                        # Get page content and parse with BS4
                        content = await page.content()

                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(content, "html.parser")

                        # Try multiple selector patterns
                        listing_links = soup.select(
                            'a[href*="/s-anzeige/"]'
                        )

                        for link in listing_links:
                            href = link.get("href", "")
                            if not href or "/s-anzeige/" not in href:
                                continue

                            if not href.startswith("http"):
                                href = "https://www.kleinanzeigen.de" + href

                            unique_id = self._make_id("kleinanzeigen", href)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            # Extract title from the link text
                            title = link.get_text(strip=True)
                            if not title:
                                continue

                            # Try to find price in parent/sibling elements
                            parent = link.find_parent("article") or link.find_parent("li") or link.find_parent("div")
                            price = "N/A"
                            location = ""
                            description = ""

                            if parent:
                                price_el = parent.select_one(
                                    "[class*='price'], [class*='Price'], "
                                    "[data-testid='price']"
                                )
                                if price_el:
                                    price = price_el.get_text(strip=True)

                                loc_el = parent.select_one(
                                    "[class*='location'], [class*='Location'], "
                                    "[data-testid*='location']"
                                )
                                if loc_el:
                                    location = loc_el.get_text(strip=True)

                                desc_el = parent.select_one(
                                    "[class*='description'], [class*='Description']"
                                )
                                if desc_el:
                                    description = desc_el.get_text(strip=True)

                            full_text = f"{title} {description}"
                            if self._is_excluded(full_text):
                                continue
                            if self._is_excluded_location(location):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(full_text):
                                continue

                            score = self._relevance_score(title, description)
                            if score < 2:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": "Kleinanzeigen",
                                "title": title,
                                "price": price,
                                "location": location,
                                "url": href,
                                "description": description[:300],
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Kleinanzeigen '{kw}' error: {e}")

                await browser.close()

        except Exception as e:
            log.error(f"Kleinanzeigen Playwright error: {e}")

        log.info(f"Kleinanzeigen: {len(results)} listings found")
        return results
