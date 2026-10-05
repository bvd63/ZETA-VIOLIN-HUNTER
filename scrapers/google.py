"""
Google Custom Search scraper — catches listings on every platform we cannot
reach directly (anti-bot / geo-blocked from a European datacenter: Facebook
Marketplace, OfferUp, Mercari US, Etsy, Guitar Center, Kleinanzeigen, ...).

Quota: 96 queries/day, reset at midnight Pacific. Scheduled runs are
10:00 and 22:00 Europe/Bucharest; each run spends at most
Config.GOOGLE_QUERIES_PER_RUN (default 48).

Per run:
  * GLOBAL_QUERIES — no site filter, dateRestrict=w2, sorted by date: catches
    anything fresh anywhere on the web.
  * a rotating slice of the (keyword × site-group) MATRIX; a cursor stored in
    SQLite makes consecutive runs continue where the previous stopped, so the
    whole matrix is covered over successive runs within the quota.

NOTE: Google retires this API on 2027-01-01 (already closed to new customers).
"""

import httpx
import logging
from datetime import datetime, timedelta
from scrapers.base import BaseScraper
from config import Config
from database import connect
from keywords import WEB_KEYWORDS, MODEL_CODES
from search_budget import reserve_request

log = logging.getLogger(__name__)

GOOGLE_API = "https://www.googleapis.com/customsearch/v1"

AUCTION_GROUPS = [
    # Confirmed Zeta auction history: Gardiner Houlgate, Tarisio, Heritage,
    # and the owner's mixed GovDeals lot. See docs/auction-sources.md.
    "site:gardinerhoulgate.co.uk OR site:musicalinstrument-auctions.co.uk OR site:tarisio.com OR site:ha.com OR site:govdeals.com",
    "site:govdeals.com OR site:allsurplus.com OR site:publicsurplus.com OR site:hibid.com OR site:shopgoodwill.com",
    "site:proxibid.com OR site:liveauctioneers.com OR site:invaluable.com OR site:the-saleroom.com OR site:auctionzip.com",
    "site:catawiki.com OR site:tarisio.com OR site:bonhams.com OR site:bidspotter.com OR site:easyliveauction.com",
    "site:interencheres.com OR site:drouot.com OR site:lot-tissimo.com OR site:ha.com OR site:dorotheum.com",
]
AUCTION_SIGNALS = 'Zeta OR Strados OR ' + ' OR '.join(MODEL_CODES) + ' OR "Jean-Luc Ponty"'
US_MARKET_GROUPS = [
    "site:craigslist.org OR site:offerup.com OR site:mercari.com OR site:facebook.com/marketplace",
    "site:musicgoround.com OR site:guitarcenter.com OR site:elderly.com OR site:chicagomusicexchange.com OR site:sweetwater.com/used",
    "site:estatesales.net OR site:estatesales.org OR site:auctionninja.com OR site:maxsold.com",
]
US_PRIORITY_QUERIES = [f'({group}) (Zeta OR Zetta OR Strados) (violin OR fiddle OR "Jazz Fusion")'
                       for group in US_MARKET_GROUPS]
PRIORITY_QUERIES = ([f"({group}) ({AUCTION_SIGNALS})" for group in AUCTION_GROUPS]
                    + US_PRIORITY_QUERIES)
GLOBAL_QUERIES = PRIORITY_QUERIES + WEB_KEYWORDS[:2]
MATRIX_KEYWORDS = WEB_KEYWORDS

# Search-engine results back up direct scrapers; main verifies each item page.
SITE_GROUPS = [
    # USA — consumer marketplaces blocked from datacenter IPs
    "site:offerup.com OR site:mercari.com OR site:facebook.com/marketplace OR site:etsy.com "
    "OR site:bonanza.com OR site:5miles.com OR site:ebid.net OR site:varagesale.com",
    # USA — music retail (used gear); electricviolinshop/fiddlershop absent: new-stock dealers
    "site:guitarcenter.com OR site:sweetwater.com OR site:samash.com OR site:musicgoround.com "
    "OR site:chicagomusicexchange.com OR site:elderly.com OR site:musiciansfriend.com OR site:zzounds.com",
    # Auctions & estate sales (the ones we cannot reach directly)
    "site:liveauctioneers.com OR site:invaluable.com OR site:proxibid.com OR site:estatesales.net "
    "OR site:catawiki.com OR site:tarisio.com OR site:bonhams.com OR site:the-saleroom.com "
    "OR site:govdeals.com OR site:allsurplus.com OR site:publicsurplus.com OR site:auctionzip.com",
    # Backup coverage: verified item pages survive even when direct search fails.
    "site:reverb.com OR site:ebay.com OR site:craigslist.org OR site:subito.it "
    "OR site:marktplaats.nl OR site:jp.mercari.com",
    # UK / IE / AU / NZ / SEA
    "site:gumtree.com.au OR site:preloved.co.uk OR site:carousell.com OR site:trademe.co.nz "
    "OR site:donedeal.ie OR site:adverts.ie OR site:shpock.com OR site:depop.com",
    # DE / CH / NL / BE
    "site:kleinanzeigen.de OR site:quoka.de OR site:ricardo.ch OR site:tutti.ch "
    "OR site:anibis.ch OR site:2ememain.be OR site:musiker-board.de OR site:kleinanzeigen.de/s-musikinstrumente",
    # FR / IT / ES / PT
    "site:leboncoin.fr OR site:wallapop.com OR site:milanuncios.com OR site:bakeca.it "
    "OR site:mercatinomusicale.com OR site:zikinf.com OR site:audiofanzine.com OR site:vinted.com",
    # Nordics / PL / CZ / SK
    "site:tradera.com OR site:sprzedajemy.pl OR site:allegro.pl OR site:bazos.cz "
    "OR site:sbazar.cz OR site:aukro.cz OR site:bazos.sk OR site:gumtree.pl",
    # Japan / Asia
    "site:auctions.yahoo.co.jp OR site:paypayfleamarket.yahoo.co.jp OR site:rakuten.co.jp OR site:jmty.jp "
    "OR site:digimart.net OR site:j-guitar.com OR site:carousell.sg OR site:bunjang.co.kr",
    # Forums & communities
    "site:maestronet.com OR site:violinist.com OR site:reddit.com OR site:thegearpage.net "
    "OR site:talkbass.com OR site:fiddlehangout.com OR site:gearspace.com OR site:vi-control.net",
    # Central / Eastern Europe & Turkey (Romania deliberately excluded)
    "site:jofogas.hu OR site:hardverapro.hu OR site:bazar.bg OR site:kupujemprodajem.com "
    "OR site:njuskalo.hr OR site:bolha.com OR site:avito.ru OR site:sahibinden.com",
    # Latin America / Africa / India
    "site:mercadolibre.com.ar OR site:mercadolibre.com.mx OR site:mercadolivre.com.br "
    "OR site:olx.com.br OR site:gumtree.co.za OR site:mudah.my OR site:olx.in OR site:yapo.cl",
]

def _db():
    conn = connect()
    conn.execute("CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY, last_run TEXT)")
    return conn


class GoogleScraper(BaseScraper):
    name = "Google Search"

    def __init__(self, api_key: str, cse_id: str):
        self.api_key = api_key
        self.cse_id = cse_id

    def is_configured(self) -> bool:
        return bool(self.api_key and self.cse_id)

    # --- quota guard ------------------------------------------------------
    def _should_run(self) -> bool:
        try:
            conn = _db()
            row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = 'google'").fetchone()
            conn.close()
            if row:
                last_run = datetime.fromisoformat(row[0])
                if datetime.utcnow() - last_run < timedelta(hours=Config.GOOGLE_GUARD_HOURS):
                    log.info(f"Google CSE: skipping — last run < {Config.GOOGLE_GUARD_HOURS}h ago")
                    return False
        except Exception as e:
            log.warning(f"Google quota guard check failed: {e}")
        return True

    def _mark_run(self) -> None:
        try:
            conn = _db()
            conn.execute("INSERT OR REPLACE INTO scraper_runs VALUES ('google', ?)",
                         (datetime.utcnow().isoformat(),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Google quota guard mark failed: {e}")

    # --- quota-day accounting (Google resets at midnight Pacific) ---------------
    @staticmethod
    def _quota_day() -> str:
        from datetime import timezone as _tz
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
        except Exception:
            return (datetime.now(_tz.utc) - timedelta(hours=7)).strftime("%Y-%m-%d")

    def _quota_used_today(self) -> int:
        try:
            conn = _db()
            row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = ?",
                               (f"google_quota:{self._quota_day()}",)).fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as e:
            log.warning(f"Google quota read failed: {e}")
            return 0

    def _add_quota_used(self, n: int) -> None:
        try:
            conn = _db()
            key = f"google_quota:{self._quota_day()}"
            row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = ?", (key,)).fetchone()
            used = (int(row[0]) if row else 0) + n
            conn.execute("INSERT OR REPLACE INTO scraper_runs VALUES (?, ?)", (key, str(used)))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Google quota write failed: {e}")

    def _get_cursor(self) -> int:
        try:
            conn = _db()
            row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = 'google_cursor'").fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as e:
            log.warning(f"Google cursor read failed: {e}")
            return 0

    def _set_cursor(self, value: int) -> None:
        try:
            conn = _db()
            conn.execute("INSERT OR REPLACE INTO scraper_runs VALUES ('google_cursor', ?)", (str(value),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Google cursor write failed: {e}")

    # --- planning ----------------------------------------------------------
    def plan_queries(self, cursor: int, budget: int = None) -> tuple:
        """Return (list of (q, extra_params), next_cursor)."""
        budget = max(1, budget if budget is not None else Config.GOOGLE_QUERIES_PER_RUN)
        plan = [(q, {"gl": "us"} if q in US_PRIORITY_QUERIES else
                 {} if q in PRIORITY_QUERIES else {"dateRestrict": "w2", "sort": "date"})
                for q in GLOBAL_QUERIES][:budget]
        matrix = [f"{kw} {group}" for kw in MATRIX_KEYWORDS for group in SITE_GROUPS]
        remaining = budget - len(plan)
        n = len(matrix)
        for i in range(min(remaining, n)):
            # pages indexed within the last year — older hits are almost always ended
            plan.append((f"({MATRIX_KEYWORDS[((cursor + i) % n) // len(SITE_GROUPS)]}) "
                         f"({SITE_GROUPS[(cursor + i) % len(SITE_GROUPS)]})", {"dateRestrict": "y1"}))
        next_cursor = (cursor + min(remaining, n)) % n if n else 0
        return plan, next_cursor

    # --- search ------------------------------------------------------------
    async def search(self) -> list:
        if not self._should_run():
            self.skipped = True
            return []
        if not self.api_key or not self.cse_id:
            log.warning("Google API key or CSE ID not set — skipping Google search.")
            self.skipped = True
            return []

        results = []
        seen_ids = set()
        cursor = self._get_cursor()
        # Never exceed the 100/day free quota even if the container restarts:
        # spend at most what is left for today's Pacific quota day.
        used = self._quota_used_today()
        budget = min(Config.GOOGLE_QUERIES_PER_RUN, max(0, Config.GOOGLE_DAILY_QUOTA - used))
        if budget <= 0:
            log.info(f"Google CSE: daily quota already spent ({used}) — skipping")
            self.skipped = True
            return []
        plan, next_cursor = self.plan_queries(cursor, budget)
        log.info(f"Google CSE: {len(plan)} queries this run (matrix cursor {cursor} → {next_cursor}; "
                 f"{used} already used today)")
        spent = 0

        async with self.make_client(timeout=20) as client:
            for q, extra in plan:
                try:
                    if not reserve_request("google", min(96, Config.GOOGLE_DAILY_QUOTA)):
                        log.info("Google daily budget exhausted — no further HTTP requests")
                        break
                    spent += 1
                    params = {"key": self.api_key, "cx": self.cse_id, "q": q, "num": 10, **extra}
                    resp = await client.get(GOOGLE_API, params=params)
                    if resp.status_code in (429, 403):
                        log.warning(f"Google CSE HTTP {resp.status_code} (quota?) — stopping: {resp.text[:200]!r}")
                        break
                    if resp.status_code != 200:
                        log.warning(f"Google CSE HTTP {resp.status_code} for '{q[:60]}': {resp.text[:200]!r}")
                        continue

                    items = resp.json().get("items", [])
                    self.fetched += len(items)
                    for item in items:
                        url = item.get("link", "")
                        unique_id = self._make_id("google", url)
                        if not url or unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        pagemap = item.get("pagemap", {}) or {}

                        if self._is_excluded(title):
                            continue
                        if not self._year_in_range(title + " " + snippet):
                            continue

                        price = self._extract_price(pagemap, snippet)
                        if price and not self._price_in_range(price):
                            continue
                        location = self._extract_location(snippet)
                        if self._is_excluded_location(location):
                            continue

                        image_url = ""
                        for key in ("cse_image", "cse_thumbnail"):
                            imgs = pagemap.get(key) or []
                            if imgs and isinstance(imgs[0], dict) and imgs[0].get("src"):
                                image_url = imgs[0]["src"]
                                break

                        results.append({
                            "id": unique_id,
                            "platform": self._extract_platform(url),
                            "title": title,
                            "price": price or "See listing",
                            "location": location or "Unknown",
                            "url": url,
                            "description": snippet[:1000],
                            "image_url": image_url,
                            "relevance_score": self._relevance_score(title, snippet),
                            "source": "search",  # main.py verifies the page is still live
                        })
                except Exception as e:
                    log.warning(f"Google search '{q[:60]}' error: {e}")

        consumed_matrix = max(0, spent - min(len(GLOBAL_QUERIES), len(plan)))
        self._set_cursor((cursor + consumed_matrix) % (len(MATRIX_KEYWORDS) * len(SITE_GROUPS)))
        self._mark_run()
        log.info(f"Google CSE: {len(results)} listings found ({spent} queries spent)")
        return results

    # --- extraction helpers -------------------------------------------------
    def _extract_price(self, pagemap: dict, text: str) -> str:
        import re
        for key in ("offer", "product"):
            for entry in pagemap.get(key) or []:
                if isinstance(entry, dict) and entry.get("price"):
                    cur = entry.get("pricecurrency") or entry.get("priceCurrency") or ""
                    return f"{entry['price']} {cur}".strip()
        patterns = [
            r"[\$€£¥]\s?[\d,]+(?:\.\d{2})?",
            r"[\d,]+(?:\.\d{2})?\s?(?:USD|EUR|GBP|JPY|CHF|SEK|NOK|PLN|CAD|AUD)\b",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(0)
        return ""

    def _extract_location(self, text: str) -> str:
        common = ["USA", "UK", "Germany", "France", "Italy", "Japan", "Australia",
                  "Canada", "Netherlands", "Spain", "Austria", "Switzerland", "Poland"]
        low = text.lower()
        for loc in common:
            if loc.lower() in low:
                return loc
        return ""

    def _extract_platform(self, url: str) -> str:
        import re
        m = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
        return m.group(1) if m else "Unknown"
