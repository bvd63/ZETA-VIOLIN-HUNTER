"""Independent provider access, bounded searches and shared offer verification.

No per-query automatic HTTP retries: an uncertain response may have been billed.
Reservations and cursors survive restarts. A 31-day ledger is deliberately more
conservative than either provider's monthly free allowance.
"""

import logging
import re
import time
from urllib.parse import urlsplit

import httpx
from config import Config
from provider_state import read, write, reserve_work, claim_window
from scrapers.base import BaseScraper
from scrapers.google import SITE_GROUPS, AUCTION_GROUPS, US_MARKET_GROUPS
from keywords import WEB_KEYWORDS, ADDITIONAL_WEB_KEYWORDS, MODEL_CODES, MODEL_NAMES
from source_policy import is_disabled_source
from facebook_discovery import queries as facebook_queries
from http_limits import retry_after_seconds

log = logging.getLogger(__name__)
GROUPS = US_MARKET_GROUPS + AUCTION_GROUPS + SITE_GROUPS


def domains(group: str) -> list:
    # Domain filters accept hosts, not site: paths. The page verifier retains
    # the responsibility for distinguishing Marketplace items from FB pages.
    return list(dict.fromkeys(re.findall(r"site:([^ /]+)", group)))


def plan(cursor: int, count: int, offset: int = 0) -> list:
    """Interleave broad terms and rotating geographic/auction/site coverage.

    Both independent cursors visit the entire shared vocabulary and all groups;
    no freshness/date cutoff can hide an older, still-active vintage listing.
    """
    result = []
    for i in range(count):
        slot = cursor + i + offset
        if slot % 3 == 0:
            result.append((WEB_KEYWORDS[(slot // 3) % len(WEB_KEYWORDS)], []))
        else:
            matrix_slot = slot - slot // 3 - 1
            group = GROUPS[matrix_slot % len(GROUPS)]
            keyword = WEB_KEYWORDS[(matrix_slot // len(GROUPS)) % len(WEB_KEYWORDS)]
            result.append((f"{keyword} for sale auction", domains(group)))
    return result


class ReserveSearch(BaseScraper):
    search_engine = True
    def is_configured(self) -> bool:
        return bool(getattr(Config, self.key_setting))

    def extra_queries(self, cursor: int) -> list:
        return []

    async def search(self) -> list:
        if not self.is_configured():
            self.skipped = True
            return []
        if not hasattr(self, "content_failures"):
            self.reset_health()
        if not getattr(self, "manual_fresh", False):
            auxiliary = getattr(self, 'auxiliary_work', False)
            key = self.engine + (':aux:last_run' if auxiliary else ':last_run')
            if (auxiliary and time.time() - float(read(self.engine + ':last_run', 0)) < 10 * 3600
                    or not claim_window(key, 10 * 3600)):
                self.skipped = True
                return []
        now = time.time()
        if (read(self.engine + ":retry_after", 0) or 0) > now:
            self.content_failures.append("provider cooldown / quota; other sources continue")
            return []
        cursor = int(read(self.engine + ":cursor", 0))
        count = getattr(Config, self.count_setting)
        extra_cursor = int(read(self.engine + ":extra_cursor", 0))
        queries = plan(cursor, count, self.offset) + self.extra_queries(extra_cursor)
        results, seen = [], set()
        async with self.make_client(timeout=30) as client:
            for index, (query, hosts) in enumerate(queries):
                planned = len(queries) * Config.RUNS_PER_DAY * 31 * self.units
                if self.engine == 'exa':
                    planned += 1426000  # all existing primary/Willhaben/Gumtree page allowances
                if not reserve_work(self.engine, self.units, self.rolling_limit(), self.daily_limit,
                                    planned, auxiliary=getattr(self, 'auxiliary_work', False) or getattr(self, 'manual_fresh', False)):
                    self.content_failures.append("bot reservation limit (scheduled capacity protected); other sources continue")
                    self.skipped = getattr(self, 'discovery_queries_attempted', 0) == 0
                    break
                # Advance BEFORE HTTP. A restart cannot re-spend the same slot.
                if index < count:
                    write(self.engine + ":cursor", cursor + index + 1)
                else:
                    write(self.engine + ":extra_cursor", extra_cursor + index - count + 1)
                try:
                    self.record_discovery_query(query)
                    response = await client.post(self.api, headers=self.headers(), json=self.payload(query, hosts))
                    if response.status_code != 200:
                        write(self.engine + ":health", {"error": f"HTTP {response.status_code}", "checked": time.time()})
                        if response.status_code in (401, 402, 403, 429):
                            delay = response.headers.get("retry-after", "")
                            seconds = retry_after_seconds(delay)
                            write(self.engine + ":retry_after", time.time() + seconds)
                            break
                        continue
                    data = response.json()
                    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                        raise ValueError("invalid search response")
                    write(self.engine + ":health", {"error": "", "checked": time.time()})
                    for item in data["results"]:
                        if not isinstance(item, dict):
                            continue
                        self.fetched += 1
                        url, title = item.get("url"), item.get("title")
                        if not isinstance(url, str) or not isinstance(title, str):
                            continue
                        if urlsplit(url).scheme not in ("http", "https") or is_disabled_source(url) or url in seen:
                            continue
                        seen.add(url)
                        content = item.get("content") or item.get("text") or "\n".join(
                            s for s in item.get("highlights", []) if isinstance(s, str))
                        if not isinstance(content, str):
                            content = ""
                        results.append({"id": self._make_id(self.engine, url),
                            "platform": urlsplit(url).hostname or self.name, "title": title,
                            "description": content, "url": url, "source": "search",
                            "price": "See listing", "location": "Unknown",
                            "date_posted": str(item.get("publishedDate") or "")[:10],
                            "image_url": item.get("image") if isinstance(item.get("image"), str) else ""})
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    # Exception strings can contain token-bearing request URLs.
                    error = type(exc).__name__
                    self.content_failures.append(error)
                    write(self.engine + ":health", {"error": error, "checked": time.time()})
                    log.warning("%s request failed (%s); reservation retained", self.name, error)
        return results


class TavilyScraper(ReserveSearch):
    name, engine = "Tavily Search", "tavily"
    api = "https://api.tavily.com/search"
    key_setting, count_setting = "TAVILY_API_KEY", "TAVILY_QUERIES_PER_RUN"
    units, daily_limit, offset = 1, 32, 0

    def extra_queries(self, cursor: int) -> list:
        # Additional slots, never replacements for the eight original queries.
        # 16/cycle * 62 = 992 Basic credits; original fourteen calls preserved.
        count = min(Config.TAVILY_EXTRA_QUERIES_PER_RUN,
                    max(0, self.daily_limit // Config.RUNS_PER_DAY - Config.TAVILY_QUERIES_PER_RUN))
        aliases = list(MODEL_CODES) + ["Zeta " + name for name in MODEL_NAMES]
        result = []
        for i in range(count):
            slot = cursor + i
            phase = slot % 8
            if phase < 2:
                result.append((ADDITIONAL_WEB_KEYWORDS[phase] + " for sale auction", []))
            elif phase < 4:
                term = aliases[((slot // 8) * 2 + phase - 2) % len(aliases)]
                result.append((term + " used violin sale auction", []))
            elif phase < 6:
                # Extra index coverage while direct fetches are blocked. This
                # does not claim access to unindexed/private listings, and it
                # does not remove a single direct collector query.
                query = 'Zeta OR Zetta OR Strados OR ' + ' OR '.join(MODEL_CODES)
                result.append((query, ['willhaben.at' if phase == 4 else 'gumtree.com']))
            else:
                result.extend(facebook_queries((slot // 8) * 2 + phase - 6, 1))
        return result

    def rolling_limit(self):
        return Config.TAVILY_ROLLING_CREDITS

    def headers(self):
        return {"Authorization": "Bearer " + Config.TAVILY_API_KEY}

    def payload(self, query, hosts):
        return {"query": query, "search_depth": "basic", "auto_parameters": False,
                "max_results": 20, "include_answer": False, "include_raw_content": False,
                "include_domains": hosts, "include_usage": True}


class ExaScraper(ReserveSearch):
    name, engine = "Exa Search", "exa"
    api = "https://api.exa.ai/search"
    key_setting, count_setting = "EXA_API_KEY", "EXA_QUERIES_PER_RUN"
    # Micro-USD reservations: $0.007 search + up to ten $0.001 highlights.
    units, daily_limit, offset = 17000, 250000, 7

    def extra_queries(self, cursor: int) -> list:
        # Existing four source/model queries keep their cursor and cadence.
        return facebook_queries(cursor, Config.EXA_EXTRA_QUERIES_PER_RUN, offset=7)

    def rolling_limit(self):
        return int(Config.EXA_ROLLING_BUDGET_USD * 1000000)

    def headers(self):
        return {"x-api-key": Config.EXA_API_KEY}

    def payload(self, query, hosts):
        # Exa's build-with-exa skill recommends natural-language intent and
        # source preferences, rather than turning example sites into a hard
        # allowlist. Preserve every alias and every rotating source preference.
        terms = query.removesuffix(" for sale auction").replace(" OR ", ", ")
        intent = "Vintage and used Zeta electric violin sale and auction listings: " + terms + "."
        if hosts:
            intent += " Source preference: " + ", ".join(hosts) + "."
        return {"query": intent, "type": "auto",
                # Intentional product limit: the $0.017 reservation covers ten
                # results/highlights under the owner's $25 total monthly budget.
                "numResults": 10, "contents": {"highlights": True}}
