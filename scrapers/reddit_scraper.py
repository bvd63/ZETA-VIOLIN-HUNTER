"""
Reddit scraper — uses praw (official Reddit API library).
Searches violin-related subreddits for Zeta listings.

Setup required (MANUAL STEP — env vars in Railway):
  REDDIT_CLIENT_ID — from reddit.com/prefs/apps
  REDDIT_CLIENT_SECRET — from reddit.com/prefs/apps
  REDDIT_USER_AGENT — e.g. "ZetaViolinHunter/1.0 by u/yourusername"

If these vars are not set, the scraper skips gracefully.
"""

import logging
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

SUBREDDITS = [
    "Gear4Sale",
    "violinist",
    "ElectricViolin",
    "Luthier",
    "classicalmusic",
]

KEYWORDS = [
    "zeta",
    "strados",
    "jv44",
    "sv24",
    "jean-luc ponty",
    "jlp",
    "jazz fusion violin",
]


class RedditScraper(BaseScraper):
    name = "Reddit"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        client_id = getattr(Config, 'REDDIT_CLIENT_ID', '') or ''
        client_secret = getattr(Config, 'REDDIT_CLIENT_SECRET', '') or ''
        user_agent = getattr(Config, 'REDDIT_USER_AGENT', '') or 'ZetaViolinHunter/1.0'

        if not client_id or not client_secret:
            log.info("Reddit credentials not set — skipping Reddit. "
                     "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in env.")
            return []

        try:
            import praw
            import asyncio
        except ImportError:
            log.warning("praw not installed — skipping Reddit")
            return []

        try:
            # praw is synchronous — run in thread to avoid blocking
            import asyncio

            def _sync_search():
                sync_results = []
                try:
                    reddit = praw.Reddit(
                        client_id=client_id,
                        client_secret=client_secret,
                        user_agent=user_agent,
                    )

                    for sub_name in SUBREDDITS:
                        try:
                            subreddit = reddit.subreddit(sub_name)
                            for kw in KEYWORDS:
                                try:
                                    for post in subreddit.search(
                                        kw,
                                        sort="new",
                                        time_filter="month",
                                        limit=25,
                                    ):
                                        post_id = str(post.id)
                                        unique_id = f"reddit_{post_id}"

                                        if unique_id in seen_ids:
                                            continue
                                        seen_ids.add(unique_id)

                                        title = post.title or ""
                                        body = (post.selftext or "")[:500]
                                        url = f"https://reddit.com{post.permalink}"
                                        flair = post.link_flair_text or ""

                                        # Skip non-sale posts
                                        full_text = f"{title} {body} {flair}".lower()
                                        if any(skip in full_text for skip in [
                                            "sold", "expired", "closed",
                                            "no longer available",
                                        ]):
                                            continue

                                        # Zeta check already done by keyword
                                        # search, but verify in title/body
                                        zeta_signals = [
                                            "zeta", "zetta", "strados",
                                            "jv44", "sv24", "ev44",
                                            "jean-luc ponty", "jlp",
                                        ]
                                        if not any(sig in full_text for sig in zeta_signals):
                                            continue

                                        score = self._relevance_score(title, body)
                                        if score < 2:
                                            continue

                                        sync_results.append({
                                            "id": unique_id,
                                            "platform": f"Reddit r/{sub_name}",
                                            "title": title,
                                            "price": "See post",
                                            "location": "Online",
                                            "url": url,
                                            "description": body[:300],
                                            "relevance_score": score,
                                        })

                                except Exception as e:
                                    log.warning(f"Reddit r/{sub_name} '{kw}' error: {e}")

                        except Exception as e:
                            log.warning(f"Reddit r/{sub_name} error: {e}")

                except Exception as e:
                    log.error(f"Reddit praw init error: {e}")

                return sync_results

            results = await asyncio.to_thread(_sync_search)

        except Exception as e:
            log.error(f"Reddit error: {e}")

        log.info(f"Reddit: {len(results)} listings found")
        return results
