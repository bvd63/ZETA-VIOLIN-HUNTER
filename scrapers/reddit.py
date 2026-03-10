"""
Reddit scraper — r/violins, r/classifieds, r/musicgear.
"""

import httpx
import logging
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)


class RedditScraper(BaseScraper):
    name = "Reddit"

    SUBREDDITS = [
        "violins", "classifieds", "musicgear", "ForSale", "BuyItForLife",
    ]

    async def search(self) -> list:
        results = []
        seen_ids = set()

        keywords = [
            "Zeta violin", "Zeta electric violin",
            "electric violin", "MIDI violin",
        ]

        async with httpx.AsyncClient(timeout=15) as client:
            for subreddit in self.SUBREDDITS:
                for kw in keywords:
                    try:
                        url = f"https://www.reddit.com/r/{subreddit}/search"
                        params = {
                            "q": kw,
                            "restrict_sr": "on",
                            "sort": "new",
                        }
                        resp = await client.get(url, params=params, headers={
                            "User-Agent": "Mozilla/5.0 (compatible; ZetaHunter/1.0)"
                        })
                        if resp.status_code != 200:
                            continue

                        soup = BeautifulSoup(resp.text, "html.parser")
                        listings = soup.select("div.Post")

                        for item in listings:
                            link_el = item.select_one("a")
                            if not link_el:
                                continue
                            
                            item_url = link_el.get("href", "")
                            unique_id = self._make_id("reddit", item_url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = link_el.text.strip()
                            content = item.select_one(".Post__content")
                            description = content.text.strip() if content else ""

                            if self._is_excluded(title + " " + description):
                                continue
                            if not self._year_in_range(title + " " + description):
                                continue

                            score = self._relevance_score(title, description)
                            if score < 3:
                                continue

                            price = self._extract_price(description)

                            results.append({
                                "id": unique_id,
                                "platform": f"Reddit (r/{subreddit})",
                                "title": title,
                                "price": price or "See listing",
                                "location": "Global",
                                "url": item_url,
                                "description": description[:300],
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"Reddit r/{subreddit} '{kw}' error: {e}")

        return results
