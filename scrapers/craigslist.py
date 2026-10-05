"""
Craigslist scraper — internal JSON search API ("sapi") across ALL areas.

Why (verified 2026-09-07):
  * RSS is gone (403 "blocked"), geo.craigslist.org/iso/us lists only "www",
    search-page HTML is JS-rendered and its JSON-LD items have no URL.
  * The frontend itself calls
      https://sapi.craigslist.org/web/v8/postings/search/full
        ?batch={AreaID}-0-360-0-0&cc=US&lang=en&query=...&searchPath=msa|sss
    which answers plain JSON, ~15 ms per call, no blocking observed at
    concurrency 10 (1400 calls in 28 s).
  * Area IDs come from the official list https://reference.craigslist.org/Areas
    (413 US + ~55 CA areas).

Search responses contain titles, not full descriptions. Brand/model queries
search all for-sale categories, including estate sales. Their unbranded titles
and generic electric-violin hits need a verified posting page (cap MAX_ENRICH).

Item format (compact arrays):
  [postingIdOffset, postedOffset, categoryId, price, "locIdx:descIdx:nbIdx~lat~lon",
   imgKey, [13, token], [4, "3:imgKey", ...], [6, slug], [10, "$price"], title]
  postingId  = decode.minPostingId  + item[0]
  posted_ts  = decode.minPostedDate + item[1]
  canonical URL = https://www.craigslist.org/view/d/{slug}/{token}
"""

from keywords import MODEL_CODES, ARTISTS

import asyncio
import httpx
import logging
import re
from datetime import date, datetime, timezone
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_UA
from config import Config
from filters import has_zeta_signal, has_violin_word, is_zeta_violin, is_other_brand, has_noise, classify
from liveness import looks_dead

log = logging.getLogger(__name__)

SAPI_URL = "https://sapi.craigslist.org/web/v8/postings/search/full"
AREAS_URL = "https://reference.craigslist.org/Areas"

# (AreaID, hostname, country) fallback if the reference list is unreachable.
STATIC_AREAS = [
    (1, "sfbay", "US"), (2, "seattle", "US"), (3, "newyork", "US"), (4, "boston", "US"),
    (7, "losangeles", "US"), (11, "chicago", "US"), (12, "sacramento", "US"),
    (43, "fresno", "US"), (96, "modesto", "US"),
]

# (query, searchPath). "msa" = musical instruments (all), "sss" = all for sale.

# Max posting pages fetched per cycle (full descriptions)
MAX_ENRICH = 150
SALE_EVENT_RX = re.compile(r"\b(?:estate|garage|yard|moving|rummage)\s+sales?\b", re.I)

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.craigslist.org/",
}


class CraigslistScraper(BaseScraper):
    name = "Craigslist"

    @staticmethod
    def plan_queries() -> list:
        """Four requests per area; brand/model discovery spans every sale category."""
        aliases = [*MODEL_CODES, *[f'("{artist}" violin)' for artist in ARTISTS]]
        # Bare Zeta outside instruments mostly finds tyres/clothes. The API's
        # default full-text search still finds a brand in estate-sale inventories.
        return [('(zeta | zetta) (violin | fiddle | midi | strados | "jazz fusion" | "4 string" | "5 string")', "sss"),
                ('strados | "jazz fusion"', "sss"),
                ('"electric violin" | "midi violin" | "electric fiddle" | "5-string violin" | "4-string violin"', "msa"),
                (" | ".join(aliases), "sss")]

    async def search(self) -> list:
        results: list = []
        seen_ids: set = set()
        candidates: list = []
        self.coverage_stats = {"detail_checked": 0, "detail_unverified": 0,
                               "detail_dead": 0, "detail_deferred": 0}

        async with self.make_client(timeout=15, follow_redirects=True, headers=HEADERS) as client:
            areas = await self._load_areas(client)
            if Config.CRAIGSLIST_MAX_US_CITIES > 0:
                areas = areas[:Config.CRAIGSLIST_MAX_US_CITIES]

            sem = asyncio.Semaphore(max(4, min(16, Config.CRAIGSLIST_CONCURRENCY)))
            # Craigslist documents the '|' OR operator: all codes in one query,
            # keeping exactly four requests per area rather than multiplying traffic.
            queries = self.plan_queries()
            work = [(area, q, path) for area in areas for q, path in queries]
            log.info(f"Craigslist: {len(areas)} areas × {len(queries)} queries = {len(work)} requests")

            async def fetch(area: tuple, query: str, path: str) -> list:
                area_id, host, country = area
                async with sem:
                    try:
                        resp = await client.get(SAPI_URL, params={
                            "batch": f"{area_id}-0-360-0-0",
                            "cc": country,
                            "lang": "en",
                            "query": query,
                            "searchPath": path,
                        })
                    except Exception as e:
                        log.warning(f"Craigslist {host} '{query}' error: {e}")
                        return []
                if resp.status_code != 200:
                    log.warning(f"Craigslist {host} '{query}' HTTP {resp.status_code}")
                    return []
                try:
                    data = resp.json().get("data", {})
                except Exception as e:
                    log.warning(f"Craigslist {host} '{query}' bad JSON: {e}")
                    return []
                if (not isinstance(data, dict) or "items" not in data
                        or (data["items"] is not None and not isinstance(data["items"], list))):
                    self.failures.append("invalid Craigslist search response")
                    return []
                decoded = self._decode(data, host)
                for item in decoded:
                    item["discovery_path"] = path
                return decoded

            batch_size = 200
            for i in range(0, len(work), batch_size):
                chunk = work[i:i + batch_size]
                for decoded in await asyncio.gather(*[fetch(a, q, p) for a, q, p in chunk],
                                                    return_exceptions=True):
                    if isinstance(decoded, BaseException):
                        log.warning(f"Craigslist batch item failed: {decoded}")
                        continue
                    self.fetched += len(decoded)
                    for item in decoded:
                        if item["id"] in seen_ids:
                            continue
                        seen_ids.add(item["id"])
                        title = item["title"]
                        if is_other_brand(title) or has_noise(title):
                            continue
                        # A full-text brand query can return 'ESTATE SALE' whose
                        # Zeta is only in its inventory, not in its title.
                        if (has_zeta_signal(title) or item["discovery_path"] == "sss"
                                or has_violin_word(title)):
                            candidates.append(item)

            # Proven title signals first, then estate inventory and unbranded
            # violins. The cap also applies to strong titles, not just the tail.
            candidates.sort(key=lambda x: x.get("date_posted", ""), reverse=True)
            candidates.sort(key=lambda x: 0 if is_zeta_violin(x["title"]) else
                            1 if SALE_EVENT_RX.search(x["title"]) else
                            2 if has_violin_word(x["title"]) else 3)
            to_enrich = candidates[:MAX_ENRICH]
            self.coverage_stats["detail_deferred"] = max(0, len(candidates) - len(to_enrich))
            self.coverage_stats["areas"] = len(areas)
            self.coverage_stats["candidates"] = len(candidates)
            log.info(f"Craigslist: {len(candidates)} candidates; fetching {len(to_enrich)} pages; "
                     f"{self.coverage_stats['detail_deferred']} deferred by detail cap")
            enrich_sem = asyncio.Semaphore(8)

            async def enrich(item: dict) -> bool:
                async with enrich_sem:
                    return await self._enrich(client, item)

            verified = await asyncio.gather(*[enrich(item) for item in to_enrich], return_exceptions=True)

            for item, success in zip(to_enrich, verified):
                if success is not True:
                    if isinstance(success, BaseException):
                        log.warning("Craigslist detail failed for %s: %s", item["url"], type(success).__name__)
                    continue
                text = f"{item['title']} {item.get('description', '')}"
                reason = classify(item)
                if reason:
                    log.info("Craigslist detail %s withheld: %s (%s)", item["url"], reason, item["title"][:100])
                    continue
                if item["price"] != "N/A" and not self._price_in_range(item["price"]):
                    continue
                if not self._year_in_range(text):
                    continue
                item["relevance_score"] = self._relevance_score(item["title"], item.get("description", ""))
                log.info("Craigslist detail %s eligible (%s)", item["url"], item["title"][:100])
                results.append(item)

        log.info(f"Craigslist: {len(results)} listings found")
        return results

    def _decode(self, data: dict, host: str) -> list:
        """Turn sapi compact arrays into listing dicts (no filtering)."""
        out = []
        decode = data.get("decode")
        if not isinstance(decode, dict):
            return out
        min_posting_id = decode.get("minPostingId", 0) or 0
        min_posted = decode.get("minPostedDate", 0) or 0
        locations = decode.get("locations") or []
        descriptions = decode.get("locationDescriptions") or []

        for it in data.get("items", []) or []:
            if not isinstance(it, list) or len(it) < 5:
                continue
            title = it[-1] if isinstance(it[-1], str) else ""
            if not title or not isinstance(it[0], int):
                continue
            posting_id = min_posting_id + it[0]
            token = slug = price = image = ""
            for x in it:
                if isinstance(x, list) and x:
                    if x[0] == 13 and len(x) > 1:
                        token = str(x[1])
                    elif x[0] == 6 and len(x) > 1:
                        slug = str(x[1])
                    elif x[0] == 10 and len(x) > 1:
                        price = str(x[1])
                    elif x[0] == 4 and len(x) > 1 and isinstance(x[1], str):
                        key = x[1].split(":", 1)[-1]
                        image = f"https://images.craigslist.org/{key}_600x450.jpg"
            if not price and len(it) > 3 and isinstance(it[3], (int, float)) and it[3]:
                price = f"${it[3]}"
            if not (token and slug):
                continue

            area_host, place = host, ""
            try:
                loc_idx, desc_idx = str(it[4]).split("~")[0].split(":")[:2]
                loc = locations[int(loc_idx)]
                if isinstance(loc, list) and len(loc) > 1:
                    area_host = str(loc[1])
                place = str(descriptions[int(desc_idx)]) if int(desc_idx) < len(descriptions) else ""
            except Exception:
                pass

            posted = ""
            if isinstance(it[1], int) and min_posted:
                posted = datetime.fromtimestamp(min_posted + it[1], tz=timezone.utc).strftime("%Y-%m-%d")

            out.append({
                "id": self._make_id("craigslist", str(posting_id)),
                "platform": f"Craigslist ({area_host})",
                "title": title,
                "price": price or "N/A",
                "location": ", ".join(p for p in [place.title() if place else "", area_host] if p),
                "url": f"https://www.craigslist.org/view/d/{slug}/{token}",
                "description": "",
                "date_posted": posted,
                "image_url": image,
                "relevance_score": 1,
            })
        return out

    async def _enrich(self, client: httpx.AsyncClient, item: dict) -> bool:
        """Fetch the posting page for the full description and first image."""
        if not hasattr(self, "coverage_stats"):
            self.coverage_stats = {"detail_checked": 0, "detail_unverified": 0,
                                   "detail_dead": 0, "detail_deferred": 0}
        try:
            resp = await client.get(item["url"], headers={**HEADERS, "Accept": "text/html,*/*;q=0.8"})
            if resp.status_code != 200:
                self.coverage_stats["detail_unverified"] += 1
                log.info("Craigslist detail %s withheld: HTTP %s", item["url"], resp.status_code)
                return False
            dead = looks_dead(resp.status_code, item["url"], str(resp.url), resp.text)
            if dead:
                self.coverage_stats["detail_dead"] += 1
                log.info("Craigslist detail %s withheld: %s", item["url"], dead)
                return False
            soup = BeautifulSoup(resp.text, "lxml")
            body = soup.select_one("#postingbody")
            if body is None:
                self.coverage_stats["detail_unverified"] += 1
                self.failures.append("Craigslist posting body missing")
                return False
            for junk in body.select(".print-information, .print-qrcode-container"):
                junk.decompose()
            text = body.get_text(" \n", strip=True).replace("QR Code Link to This Post", "").strip()
            if not text:
                self.coverage_stats["detail_unverified"] += 1
                self.failures.append("Craigslist posting body empty")
                return False
            item["description"] = text
            if SALE_EVENT_RX.search(item["title"]):
                sale_dates = self._sale_dates(soup)
                if not sale_dates:
                    self.coverage_stats["detail_unverified"] += 1
                    log.info("Craigslist detail %s withheld: sale dates unavailable", item["url"])
                    return False
                if max(sale_dates) < self._local_today(soup):
                    self.coverage_stats["detail_dead"] += 1
                    log.info("Craigslist detail %s withheld: estate/garage sale ended", item["url"])
                    return False
                item["sale_end"] = max(sale_dates).isoformat()
                item["estate_sale"] = True
            self.coverage_stats["detail_checked"] += 1
            item["verification"] = "live"
            if not item.get("image_url"):
                img = soup.select_one(".gallery img, .slide img, #thumbs img")
                if img and img.get("src"):
                    item["image_url"] = img["src"]
            return True
        except Exception as e:
            self.coverage_stats["detail_unverified"] += 1
            log.warning("Craigslist detail %s withheld: %s", item.get("url", ""), type(e).__name__)
            return False

    @staticmethod
    def _sale_dates(soup: BeautifulSoup) -> list[date]:
        """Advertised event dates, never the posting/publication timestamp."""
        dates = []
        for group in soup.select(".attrgroup"):
            text = group.get_text(" ", strip=True)
            if not re.match(r"(?:sale\s+)?dates?\s*:", text, re.I):
                continue
            for value in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text):
                try:
                    dates.append(datetime.fromisoformat(value).date())
                except ValueError:
                    continue
        return dates

    @staticmethod
    def _local_today(soup: BeautifulSoup) -> date:
        """Posting timestamps carry the area's UTC offset, not Romania's date."""
        for tag in soup.select("time.date[datetime]"):
            try:
                posted = datetime.fromisoformat(tag["datetime"])
                if posted.tzinfo is not None:
                    return datetime.now(posted.tzinfo).date()
            except ValueError:
                continue
        return datetime.now(timezone.utc).date()

    async def _load_areas(self, client: httpx.AsyncClient) -> list:
        wanted = {c.strip().upper() for c in Config.CRAIGSLIST_COUNTRIES.split(",") if c.strip()}
        try:
            resp = await client.get(AREAS_URL)
            if resp.status_code != 200:
                log.warning(f"Craigslist areas list HTTP {resp.status_code} — using static fallback")
                return STATIC_AREAS
            areas = []
            for a in resp.json():
                country = str(a.get("Country", "")).upper()
                if wanted and country not in wanted:
                    continue
                if a.get("AreaID") and a.get("Hostname"):
                    areas.append((int(a["AreaID"]), str(a["Hostname"]), country))
            return areas or STATIC_AREAS
        except Exception as e:
            log.warning(f"Craigslist areas list error: {e} — using static fallback")
            return STATIC_AREAS
