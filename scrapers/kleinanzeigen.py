"""
Kleinanzeigen.de scraper — uses Playwright headless Chromium.
Germany's largest classifieds site. Public search, no login required.
"""

import asyncio
import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

KEYWORDS = [
    "Zeta Violine",
    "Zeta Geige",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "elektrische Geige Zeta",
]

BASE_URL = "https://www.kleinanzeigen.de/s-musikinstrumente/{keyword}/k0c74"


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
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale="de-DE",
                )
                page = await context.new_page()

                for kw in KEYWORDS:
                    try:
                        url = BASE_URL.format(keyword=kw.replace(" ", "-"))
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                        # Accept cookie banner if present
                        try:
                            accept_btn = page.locator("#gdpr-banner-accept, [data-testid='gdpr-banner-accept']")
                            if await accept_btn.count() > 0:
                                await accept_btn.first.click()
                                await asyncio.sleep(1)
                        except Exception:
                            pass

                        # Wait for listings to load
                        await page.wait_for_selector(
                            "article.aditem, li.ad-listitem, [data-testid='aditem']",
                            timeout=10000,
                        )

                        # Extract listing data
                        listings = await page.evaluate("""() => {
                            const items = document.querySelectorAll(
                                'article.aditem, li.ad-listitem, [data-testid="aditem"]'
                            );
                            return Array.from(items).map(item => {
                                const linkEl = item.querySelector('a[href*="/s-anzeige/"]');
                                const titleEl = item.querySelector(
                                    'a[href*="/s-anzeige/"] h2, a[href*="/s-anzeige/"] .text-module-begin, .aditem-main--middle--title'
                                );
                                const priceEl = item.querySelector(
                                    '.aditem-main--middle--price-shipping--price, [data-testid="price"]'
                                );
                                const locationEl = item.querySelector(
                                    '.aditem-main--top--left, [data-testid="cardlocation"]'
                                );
                                const descEl = item.querySelector(
                                    '.aditem-main--middle--description, [data-testid="carddescription"]'
                                );
                                return {
                                    url: linkEl ? linkEl.href : '',
                                    title: titleEl ? titleEl.innerText.trim() : '',
                                    price: priceEl ? priceEl.innerText.trim() : 'N/A',
                                    location: locationEl ? locationEl.innerText.trim() : '',
                                    description: descEl ? descEl.innerText.trim() : '',
                                };
                            }).filter(item => item.url && item.title);
                        }""")

                        for item in listings:
                            url = item.get("url", "")
                            if not url.startswith("http"):
                                url = "https://www.kleinanzeigen.de" + url

                            unique_id = self._make_id("kleinanzeigen", url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = item.get("title", "")
                            description = item.get("description", "")
                            price = item.get("price", "N/A")
                            location = item.get("location", "")

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
                                "url": url,
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
