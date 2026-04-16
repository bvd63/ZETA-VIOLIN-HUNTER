"""
Google Custom Search scraper.
Catches listings on ALL platforms not covered by direct APIs:
Kleinanzeigen, Leboncoin, Subito, Wallapop, Marktplaats, Catawiki,
Tarisio, Mercari, Yahoo Japan, Gumtree, Craigslist, Reddit, forums, etc.

Setup: https://programmablesearchengine.google.com (free: 100 queries/day)
"""

import httpx
import logging
import sqlite3
from datetime import datetime, timedelta
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

GOOGLE_API = "https://www.googleapis.com/customsearch/v1"

# Sites to search via Google (covers your full list)
SITE_GROUPS = [
    # Global marketplaces
    "site:reverb.com OR site:ebay.com OR site:ebay.de OR site:ebay.co.uk OR site:ebay.fr OR site:ebay.it OR site:ebay.es OR site:ebay.com.au OR site:ebay.pl",
    "site:facebook.com/marketplace OR site:craigslist.org OR site:mercari.com OR site:offerup.com",
    "site:gumtree.com OR site:kijiji.ca OR site:carousell.com",
    "site:yahoo.co.jp OR site:rakuten.co.jp",
    # European classifieds
    "site:kleinanzeigen.de OR site:leboncoin.fr OR site:subito.it OR site:wallapop.com OR site:marktplaats.nl",
    "site:willhaben.at OR site:ricardo.ch OR site:blocket.se OR site:finn.no OR site:tori.fi OR site:allegro.pl",
    # Music marketplaces
    "site:audiofanzine.com OR site:zikinf.com OR site:mercatinomusicale.com OR site:sweetwater.com",
    "site:guitarcenter.com OR site:thomann.de OR site:gear4music.com OR site:chicagomusicexchange.com",
    # Auctions
    "site:catawiki.com OR site:invaluable.com OR site:hibid.com OR site:bonhams.com OR site:sothebys.com",
    "site:tarisio.com",
    # Forums & communities
    "site:maestronet.com OR site:violinist.com OR site:reddit.com OR site:thegearpage.net OR site:talkbass.com",
]


class GoogleScraper(BaseScraper):
    name = "Google Search"

    def __init__(self, api_key: str, cse_id: str):
        self.api_key = api_key
        self.cse_id = cse_id

    def _should_run(self) -> bool:
        """Check if enough time has passed since last Google run."""
        try:
            conn = sqlite3.connect("zeta_listings.db")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraper_runs (
                    scraper TEXT PRIMARY KEY,
                    last_run TEXT
                )
            """)
            cur = conn.execute(
                "SELECT last_run FROM scraper_runs WHERE scraper = 'google'"
            )
            row = cur.fetchone()
            conn.close()
            if row:
                last_run = datetime.fromisoformat(row[0])
                if datetime.utcnow() - last_run < timedelta(hours=20):
                    log.info("Google CSE: skipping — last run was less than 20h ago")
                    return False
        except Exception as e:
            log.warning(f"Google quota guard check failed: {e}")
        return True

    def _mark_run(self) -> None:
        """Record that Google search ran successfully."""
        try:
            conn = sqlite3.connect("zeta_listings.db")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraper_runs (
                    scraper TEXT PRIMARY KEY,
                    last_run TEXT
                )
            """)
            conn.execute(
                "INSERT OR REPLACE INTO scraper_runs (scraper, last_run) VALUES (?, ?)",
                ("google", datetime.utcnow().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Google quota guard mark failed: {e}")

    async def search(self) -> list:
        if not self._should_run():
            return []

        if not self.api_key or not self.cse_id:
            log.warning("Google API key or CSE ID not set — skipping Google search.")
            return []

        results = []
        seen_ids = set()

        # Primary keywords (most specific first to save API quota)
        primary_keywords = [
            "Zeta electric violin",
            "Zeta Strados violin",
            "Zeta JV44 violin",
            "Zeta SV24 violin",
            "Zeta Jazz Fusion violin",
            "Zeta EV44 violin",
            "Zeta violin for sale",
            "Zetta violin for sale",
            "violino elettrico Zeta",
            "violon électrique Zeta",
            "violín eléctrico Zeta",
            "Zeta Geige kaufen",
            "Zeta violin achat",
        ]

        async with httpx.AsyncClient(timeout=20) as client:
            # Prioritize groups that include blocked/hard-to-scrape platforms.
            prioritized_groups = [
                SITE_GROUPS[1],  # facebook/craigslist/mercari
                SITE_GROUPS[4],  # kleinanzeigen/leboncoin/subito/wallapop/marktplaats
                SITE_GROUPS[5],  # willhaben/ricardo/blocket/finn/tori/allegro
                SITE_GROUPS[0],
                SITE_GROUPS[2],
            ]

            for kw in primary_keywords:
                for site_group in prioritized_groups:
                    try:
                        query = f"{kw} {site_group}"
                        params = {
                            "key": self.api_key,
                            "cx": self.cse_id,
                            "q": query,
                            "num": 10,
                        }
                        resp = await client.get(GOOGLE_API, params=params)
                        if resp.status_code == 429:
                            log.warning("Google quota exceeded for today.")
                            return results
                        if resp.status_code != 200:
                            continue

                        data = resp.json()
                        items = data.get("items", [])

                        for item in items:
                            url = item.get("link", "")
                            unique_id = self._make_id("google", url)

                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = item.get("title", "")
                            snippet = item.get("snippet", "")
                            full_text = title + " " + snippet

                            if self._is_excluded(full_text):
                                continue
                            if not self._year_in_range(full_text):
                                continue

                            # Extract price from snippet if present
                            price = self._extract_price(snippet)
                            if price and not self._price_in_range(price):
                                continue

                            # Extract location from snippet
                            location = self._extract_location(snippet)
                            if self._is_excluded_location(location):
                                continue

                            platform = self._extract_platform(url)
                            score = self._relevance_score(title, snippet)
                            if score < 2:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": platform,
                                "title": title,
                                "price": price or "See listing",
                                "location": location or "Unknown",
                                "url": url,
                                "description": snippet[:300],
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Google search '{kw}' error: {e}")

        self._mark_run()
        return results

    def _extract_price(self, text: str) -> str:
        import re
        patterns = [
            r"[\$€£¥]\s?[\d,]+(?:\.\d{2})?",
            r"[\d,]+(?:\.\d{2})?\s?(?:USD|EUR|GBP|JPY|CHF|SEK|NOK|PLN)",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(0)
        return ""

    def _extract_location(self, text: str) -> str:
        # Simple heuristic — look for country/city mentions
        common = ["USA", "UK", "Germany", "France", "Italy", "Japan", "Australia",
                  "Canada", "Netherlands", "Spain", "Austria", "Switzerland", "Poland"]
        for loc in common:
            if loc.lower() in text.lower():
                return loc
        return ""

    def _extract_platform(self, url: str) -> str:
        import re
        m = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
        if m:
            return m.group(1).replace("www.", "")
        return "Unknown"
