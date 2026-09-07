"""
Brave Search API scraper — web-wide search, the planned replacement for
Google CSE (which retires 2027-01-01). Reuses Google's keyword × site-group
matrix so both engines cover the same platforms.

Setup: https://api-dashboard.search.brave.com → API key → BRAVE_API_KEY.
No free plan since Feb 2026, but a $5 monthly credit ≈ 1000 queries/month;
BRAVE_QUERIES_PER_RUN=16 × 2 runs/day ≈ 960/month. Skips gracefully if unset.
"""

import asyncio
import httpx
import logging
from datetime import datetime, timedelta
from scrapers.base import BaseScraper
from scrapers.google import GLOBAL_QUERIES, MATRIX_KEYWORDS, SITE_GROUPS
from config import Config
from database import connect

log = logging.getLogger(__name__)

BRAVE_API = "https://api.search.brave.com/res/v1/web/search"


class BraveScraper(BaseScraper):
    name = "Brave Search"

    def is_configured(self) -> bool:
        return bool(Config.BRAVE_API_KEY)

    # --- guard + cursor (same scraper_runs table as Google) -----------------
    def _kv_get(self, key: str) -> str:
        try:
            conn = connect()
            conn.execute("CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY, last_run TEXT)")
            row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = ?", (key,)).fetchone()
            conn.close()
            return row[0] if row else ""
        except Exception as e:
            log.warning(f"Brave state read failed: {e}")
            return ""

    def _kv_set(self, key: str, value: str) -> None:
        try:
            conn = connect()
            conn.execute("CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY, last_run TEXT)")
            conn.execute("INSERT OR REPLACE INTO scraper_runs VALUES (?, ?)", (key, value))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Brave state write failed: {e}")

    def _should_run(self) -> bool:
        last = self._kv_get("brave")
        if last:
            try:
                if datetime.utcnow() - datetime.fromisoformat(last) < timedelta(hours=Config.BRAVE_GUARD_HOURS):
                    log.info(f"Brave: skipping — last run < {Config.BRAVE_GUARD_HOURS}h ago")
                    return False
            except ValueError:
                pass
        return True

    def plan_queries(self, cursor: int) -> tuple:
        """(list of (q, params), next_cursor). Global fresh queries first,
        then a rotating slice of the site matrix."""
        budget = max(1, Config.BRAVE_QUERIES_PER_RUN)
        plan = [(q, {"freshness": "pm"}) for q in GLOBAL_QUERIES][:budget]
        matrix = [f"{kw} {group}" for kw in MATRIX_KEYWORDS for group in SITE_GROUPS]
        remaining = budget - len(plan)
        n = len(matrix)
        take = min(remaining, n)
        for i in range(take):
            plan.append((matrix[(cursor + i) % n], {}))
        return plan, ((cursor + take) % n if n else 0)

    async def search(self) -> list:
        if not self.is_configured():
            log.info("BRAVE_API_KEY not set — skipping Brave Search.")
            return []
        if not self._should_run():
            return []

        results = []
        seen_ids = set()
        cursor = int(self._kv_get("brave_cursor") or 0)
        plan, next_cursor = self.plan_queries(cursor)
        log.info(f"Brave: {len(plan)} queries this run (matrix cursor {cursor} → {next_cursor})")

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": Config.BRAVE_API_KEY,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            for q, extra in plan:
                try:
                    params = {"q": q, "count": 20, "text_decorations": "false", "safesearch": "off", **extra}
                    resp = await client.get(BRAVE_API, headers=headers, params=params)
                    if resp.status_code in (401, 402, 403, 429):
                        log.warning(f"Brave HTTP {resp.status_code} (key/credit/rate?) — stopping: {resp.text[:200]!r}")
                        break
                    if resp.status_code != 200:
                        log.warning(f"Brave HTTP {resp.status_code} for '{q[:60]}': {resp.text[:200]!r}")
                        continue

                    items = (resp.json().get("web") or {}).get("results") or []
                    self.fetched += len(items)
                    for item in items:
                        url = item.get("url", "")
                        unique_id = self._make_id("brave", url)
                        if not url or unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        title = item.get("title", "")
                        snippet = item.get("description", "") or ""
                        if self._is_excluded(title):
                            continue
                        if not self._year_in_range(title + " " + snippet):
                            continue
                        image_url = ((item.get("thumbnail") or {}).get("src")) or ""

                        results.append({
                            "id": unique_id,
                            "platform": self._extract_platform(url),
                            "title": title,
                            "price": "See listing",
                            "location": "Unknown",
                            "url": url,
                            "description": snippet[:300],
                            "date_posted": str(item.get("page_age") or item.get("age") or "")[:10],
                            "image_url": image_url,
                            "relevance_score": self._relevance_score(title, snippet),
                        })
                except Exception as e:
                    log.warning(f"Brave search '{q[:60]}' error: {e}")
                # free/credit tier is 1 request per second
                await asyncio.sleep(1.1)

        self._kv_set("brave_cursor", str(next_cursor))
        self._kv_set("brave", datetime.utcnow().isoformat())
        log.info(f"Brave: {len(results)} listings found")
        return results

    def _extract_platform(self, url: str) -> str:
        import re
        m = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
        return m.group(1) if m else "Unknown"
