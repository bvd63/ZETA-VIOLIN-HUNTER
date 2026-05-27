"""
Facebook Marketplace scraper — Playwright headless Chromium.

FB Marketplace shows listings publicly without login. A login modal
appears after ~2s but listings are already rendered in the DOM.
Strategy: navigate → close modal → extract listing cards.

Anti-bot: Facebook uses fingerprinting. Scraper fails gracefully (returns [])
if blocked. On Railway, Chromium must already be installed (Dockerfile).

URL: facebook.com/marketplace/search/?query=QUERY
Listings: a[href*="/marketplace/item/"] anchors in search results.
"""

import asyncio
import logging
import re
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

FB_SEARCH_URL = "https://www.facebook.com/marketplace/search/"

KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta JV44",
    "Zeta SV24",
    "Zetta violin",
]

ZETA_SIGNALS = [
    "zeta", "zetta", "strados", "jv44", "jv45",
    "sv24", "sv25", "sv43", "cv44", "ev25", "ev44",
    "jean-luc ponty", "jlp", "jazz fusion",
]

# Selectors for dismissing login / cookie modals
CLOSE_SELECTORS = [
    "[aria-label='Close']",
    "[aria-label='close']",
    "div[role='dialog'] [aria-label='Close']",
    "div[role='dialog'] button[type='button']",
    # Cookie banner
    "button[title='Allow all cookies']",
    "button[data-cookiebanner='accept_button']",
    "[data-testid='cookie-policy-manage-dialog-accept-button']",
]


class FacebookMarketplaceScraper(BaseScraper):
    name = "Facebook Marketplace"

    async def search(self) -> list:
        results = []
        seen_ids: set = set()

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.warning("Playwright not installed — skipping Facebook Marketplace")
            return []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                    ],
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    timezone_id="America/New_York",
                    viewport={"width": 1920, "height": 1080},
                )
                # Hide webdriver fingerprint
                await context.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                )
                page = await context.new_page()

                for kw in KEYWORDS:
                    try:
                        url = f"{FB_SEARCH_URL}?query={kw.replace(' ', '+')}"
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        # Allow listings to render before modal blocks
                        await asyncio.sleep(3)

                        # Dismiss modals / cookie banners
                        await self._dismiss_overlays(page)
                        await asyncio.sleep(1)

                        html = await page.content()
                        new_results = self._parse_listings(html, seen_ids)
                        results.extend(new_results)
                        log.debug(f"Facebook Marketplace '{kw}': {len(new_results)} raw listings")

                        # Small delay between keywords to avoid rate limiting
                        await asyncio.sleep(2)

                    except Exception as e:
                        log.warning(f"Facebook Marketplace '{kw}' error: {e}")

                await browser.close()

        except Exception as e:
            log.error(f"Facebook Marketplace Playwright error: {e}")

        # Apply Zeta filter and other checks
        filtered = []
        for listing in results:
            combined = (listing["title"] + " " + listing["description"]).lower()
            if not any(sig in combined for sig in ZETA_SIGNALS):
                continue
            if self._is_excluded(listing["title"]):
                continue
            if not self._price_in_range(listing["price"]):
                continue
            if not self._year_in_range(listing["title"]):
                continue
            filtered.append(listing)

        log.info(f"Facebook Marketplace: {len(filtered)} listings found")
        return filtered

    async def _dismiss_overlays(self, page) -> None:
        """Try multiple strategies to close login modal and cookie banners."""
        # Strategy 1: Press Escape (closes most modals)
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Strategy 2: Click known close/accept buttons
        for selector in CLOSE_SELECTORS:
            try:
                btn = page.locator(selector)
                if await btn.count() > 0:
                    await btn.first.click(timeout=2000)
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        # Strategy 3: Click outside modal (top-left corner background)
        try:
            overlay = page.locator("div[role='dialog']")
            if await overlay.count() > 0:
                await page.mouse.click(10, 10)
                await asyncio.sleep(0.5)
        except Exception:
            pass

    def _parse_listings(self, html: str, seen_ids: set) -> list:
        """Extract listings from FB Marketplace HTML.

        Key insight: even without login, listing card anchors with
        href="/marketplace/item/ITEMID/" are present in the DOM.
        Price and title are in text nodes within those anchors.
        """
        parsed = []
        try:
            soup = BeautifulSoup(html, "lxml")

            # Find all listing item links
            listing_anchors = soup.select(
                'a[href*="/marketplace/item/"]'
            )

            for anchor in listing_anchors:
                href = anchor.get("href", "")
                if not href or "/marketplace/item/" not in href:
                    continue

                # Build full URL
                if href.startswith("/"):
                    url = "https://www.facebook.com" + href
                else:
                    url = href

                # Remove query params from URL for cleaner dedup
                url_clean = url.split("?")[0].rstrip("/")

                # Extract item ID from URL
                m = re.search(r"/marketplace/item/(\d+)", url_clean)
                item_id = m.group(1) if m else url_clean

                unique_id = self._make_id("facebook_marketplace", item_id)
                if unique_id in seen_ids:
                    continue
                seen_ids.add(unique_id)

                # Extract text content from the card
                # FB cards typically have: image alt, title, price, location
                # In order in the DOM: title (span), price (span), location (span)
                spans = anchor.find_all("span", recursive=True)
                text_nodes = [
                    s.get_text(strip=True)
                    for s in spans
                    if s.get_text(strip=True)
                ]

                # Remove duplicates while preserving order
                seen_text: set = set()
                unique_texts = []
                for t in text_nodes:
                    if t not in seen_text and len(t) > 1:
                        seen_text.add(t)
                        unique_texts.append(t)

                if not unique_texts:
                    # Fallback: just use all text from anchor
                    raw = anchor.get_text(" ", strip=True)
                    if raw:
                        unique_texts = raw.split()

                # Heuristic: title is the longest text, price starts with $
                title = ""
                price = ""
                location = ""

                for text in unique_texts:
                    if re.match(r"^\$[\d,]+", text) or re.match(r"^[\d,]+\s*USD", text):
                        price = text
                    elif not title and len(text) > 5:
                        title = text
                    elif title and not location and len(text) > 2 and "·" not in text:
                        location = text

                if not title:
                    continue

                score = self._relevance_score(title)
                parsed.append({
                    "id": unique_id,
                    "platform": "Facebook Marketplace",
                    "title": title,
                    "price": price or "N/A",
                    "location": location or "Unknown",
                    "url": url_clean,
                    "description": " | ".join(unique_texts[:5]),
                    "relevance_score": score,
                })

        except Exception as e:
            log.warning(f"Facebook Marketplace parse error: {e}")

        return parsed
