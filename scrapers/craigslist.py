"""
Craigslist scraper — internal JSON search API ("sapi") across ALL areas.

Why (verified 2026-09-07):
  * RSS is gone (403 "blocked"), geo.craigslist.org/iso/us lists only "www",
    search-page HTML is JS-rendered and its JSON-LD items have no URL.
  * The frontend itself calls
      https://sapi.craigslist.org/web/v8/postings/search/full
        ?batch={AreaID}-0-360-0-0&cc=US&lang=en&query=...&searchPath=msa|sss
    which answers plain JSON, ~15 ms per call, no blocking observed at
    concurrency 10 (826 calls in 13 s).
  * Area IDs come from the official list https://reference.craigslist.org/Areas
    (413 US + 100+ CA areas).

Item format (compact arrays):
  [postingIdOffset, postedOffset, categoryId, price, "locIdx:descIdx:nbIdx~lat~lon",
   imgKey, [13, token], [4, "3:imgKey", ...], [6, slug], [10, "$price"], title]
  postingId  = decode.minPostingId  + item[0]
  posted_ts  = decode.minPostedDate + item[1]
  canonical URL = https://www.craigslist.org/view/d/{slug}/{token}
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, BROWSER_UA
from config import Config
from filters import has_zeta_signal

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
# Broad single-word queries + local Zeta-signal filter beat many narrow queries:
# fewer requests, and titles like "Zeta Strados 5-string" are still caught.
QUERIES = [
    ("zeta", "msa"),
    ("strados", "msa"),
    ("zeta violin", "sss"),
]

# Max posting pages fetched per cycle for full descriptions
MAX_ENRICH = 20

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.craigslist.org/",
}


class CraigslistScraper(BaseScraper):
    name = "Craigslist"

    async def search(self) -> list:
        results: list = []
        seen_ids: set = set()

        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=HEADERS) as client:
            areas = await self._load_areas(client)
            if Config.CRAIGSLIST_MAX_US_CITIES > 0:
                areas = areas[:Config.CRAIGSLIST_MAX_US_CITIES]

            sem = asyncio.Semaphore(max(4, min(16, Config.CRAIGSLIST_CONCURRENCY)))
            work = [(area, q, path) for area in areas for q, path in QUERIES]
            log.info(f"Craigslist: {len(areas)} areas × {len(QUERIES)} queries = {len(work)} requests")

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
                return self._decode(data if isinstance(data, dict) else {}, host)

            batch_size = 200
            for i in range(0, len(work), batch_size):
                chunk = work[i:i + batch_size]
                for decoded in await asyncio.gather(*[fetch(a, q, p) for a, q, p in chunk]):
                    self.fetched += len(decoded)
                    for item in decoded:
                        if item["id"] in seen_ids:
                            continue
                        seen_ids.add(item["id"])
                        if not has_zeta_signal(item["title"]):
                            continue
                        if self._is_excluded(item["title"]):
                            continue
                        if item["price"] != "N/A" and not self._price_in_range(item["price"]):
                            continue
                        if not self._year_in_range(item["title"]):
                            continue
                        results.append(item)

            # The search API returns titles only. Fetch the posting page for the
            # few Zeta-signal hits so filters can see the full description.
            await asyncio.gather(*[self._enrich(client, item) for item in results[:MAX_ENRICH]])
            for item in results:
                item["relevance_score"] = self._relevance_score(item["title"], item.get("description", ""))

        log.info(f"Craigslist: {len(results)} listings found")
        return results

    async def _enrich(self, client: httpx.AsyncClient, item: dict) -> None:
        try:
            resp = await client.get(item["url"], headers={**HEADERS, "Accept": "text/html,*/*;q=0.8"})
            if resp.status_code != 200:
                return
            soup = BeautifulSoup(resp.text, "lxml")
            body = soup.select_one("#postingbody")
            if body:
                for junk in body.select(".print-information, .print-qrcode-container"):
                    junk.decompose()
                text = body.get_text(" ", strip=True).replace("QR Code Link to This Post", "").strip()
                item["description"] = text[:300]
            if not item.get("image_url"):
                img = soup.select_one(".gallery img, .slide img, #thumbs img")
                if img and img.get("src"):
                    item["image_url"] = img["src"]
        except Exception as e:
            log.debug(f"Craigslist enrich error for {item.get('url', '')}: {e}")

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
            if not isinstance(it, list) or len(it) < 3:
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
            if not price and isinstance(it[3], (int, float)) and it[3]:
                price = f"${it[3]}"

            if token and slug:
                url = f"https://www.craigslist.org/view/d/{slug}/{token}"
            else:
                continue

            # Location: "locIdx:descIdx:nbIdx~lat~lon"
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
                "url": url,
                "description": "",
                "date_posted": posted,
                "image_url": image,
                "relevance_score": 1,
            })
        return out

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
