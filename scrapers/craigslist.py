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

The search API returns TITLES only. Titles like "Electric violin 5 string"
often hide a Zeta in the body, so every violin-titled posting (cap MAX_ENRICH)
gets its page fetched and its description searched for Zeta signals.

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
from filters import has_zeta_signal, has_violin_word, is_other_brand

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
QUERIES = [
    ("zeta", "msa"),
    ("strados", "msa"),
    ("zeta violin", "sss"),
    ("electric violin", "msa"),   # unbranded titles — body is checked for Zeta
]

# Max posting pages fetched per cycle (full descriptions)
MAX_ENRICH = 150

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
        definite: list = []    # Zeta signal already in the title
        potential: list = []   # violin in the title, no brand — body must be checked

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
                        title = item["title"]
                        if has_zeta_signal(title):
                            definite.append(item)
                        elif has_violin_word(title) and not is_other_brand(title):
                            potential.append(item)

            # Fetch posting pages: all definite hits + as many unbranded violin
            # postings as the budget allows (newest first).
            potential.sort(key=lambda x: x.get("date_posted", ""), reverse=True)
            to_enrich = definite + potential[:max(0, MAX_ENRICH - len(definite))]
            log.info(f"Craigslist: {len(definite)} Zeta-titled, {len(potential)} unbranded violin postings; "
                     f"fetching {len(to_enrich)} pages")
            enrich_sem = asyncio.Semaphore(8)

            async def enrich(item: dict) -> None:
                async with enrich_sem:
                    await self._enrich(client, item)

            await asyncio.gather(*[enrich(item) for item in to_enrich])

            for item in definite + potential[:max(0, MAX_ENRICH - len(definite))]:
                text = f"{item['title']} {item.get('description', '')}"
                if item not in definite and not has_zeta_signal(text):
                    continue
                if self._is_excluded(item["title"]):
                    continue
                if item["price"] != "N/A" and not self._price_in_range(item["price"]):
                    continue
                if not self._year_in_range(text):
                    continue
                item["relevance_score"] = self._relevance_score(item["title"], item.get("description", ""))
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

    async def _enrich(self, client: httpx.AsyncClient, item: dict) -> None:
        """Fetch the posting page for the full description and first image."""
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
                item["description"] = text[:600]
            if not item.get("image_url"):
                img = soup.select_one(".gallery img, .slide img, #thumbs img")
                if img and img.get("src"):
                    item["image_url"] = img["src"]
        except Exception as e:
            log.debug(f"Craigslist enrich error for {item.get('url', '')}: {e}")

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
