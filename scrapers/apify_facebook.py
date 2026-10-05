"""Two-stage Facebook collection, bounded before POST and durable across restarts.

Task input and run options are overridden explicitly: console caps are not API
caps. Never retry an uncertain run creation; keep its full budget reservation.
"""

import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode, urlsplit

import httpx

from config import Config
from database import connect
from filters import classify
from keywords import STRING_VARIANTS
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)
API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9]+$")
ITEM = re.compile(r"^/marketplace/item/(\d+)/?$")


def _money(value: float) -> int:
    return int(Decimal(str(value)) * 1_000_000)


def _text(value: object) -> str:
    return str(value.get("text", "")) if isinstance(value, dict) else str(value or "")


def _flag(row: dict, camel: str, snake: str) -> object:
    value = row.get(camel, row.get(snake))
    return value if isinstance(value, bool) else None


def normalize(row: dict) -> dict:
    """Accept only actual public Facebook item URLs; support both observed schemas."""
    if not isinstance(row, dict) or row.get("error"):
        return {}
    raw_url = row.get("itemUrl") or row.get("listingUrl") or ""
    if not isinstance(raw_url, str):
        return {}
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return {}
    match = ITEM.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.hostname not in ("facebook.com", "www.facebook.com", "m.facebook.com") or not match:
        return {}
    title = _text(row.get("listingTitle") or row.get("marketplace_listing_title"))
    if not title:
        return {}
    price = row.get("listingPrice") or row.get("listing_price") or {}
    if not isinstance(price, dict):
        price = {}
    location = _text(row.get("locationText"))
    if not location:
        raw_location = row.get("location")
        geo = raw_location.get("reverse_geocode", {}) if isinstance(raw_location, dict) else {}
        geo = geo if isinstance(geo, dict) else {}
        location = ", ".join(str(geo[k]) for k in ("city", "state") if geo.get(k))
    photo = row.get("primaryListingPhoto") or row.get("primary_listing_photo") or {}
    photos = row.get("listingPhotos") or []
    if not photo and isinstance(photos, list) and photos:
        photo = photos[0]
    photo = photo if isinstance(photo, dict) else {}
    raw_image = photo.get("image")
    raw_image = raw_image if isinstance(raw_image, dict) else {}
    image = photo.get("photo_image_url") or raw_image.get("uri", "")
    image = image if isinstance(image, str) else ""
    item_id = match.group(1)
    return {
        "id": BaseScraper()._make_id("facebook_marketplace", item_id),
        "platform": "Facebook Marketplace", "title": title,
        "price": f"{price['amount']} {price.get('currency', 'USD')}" if price.get("amount") is not None else "N/A",
        "location": location or "Unknown", "url": f"https://www.facebook.com/marketplace/item/{item_id}/",
        "description": _text(row.get("description")), "condition": _text(row.get("condition")),
        "image_url": image, "relevance_score": 8, "provider": "apify",
        "apify_is_live": _flag(row, "isLive", "is_live"),
        "apify_is_sold": _flag(row, "isSold", "is_sold"),
        "apify_is_pending": _flag(row, "isPending", "is_pending"),
        "apify_is_hidden": _flag(row, "isHidden", "is_hidden"),
    }


def available(listing: dict) -> bool:
    return (listing.get("apify_is_live") is True and listing.get("apify_is_sold") is False
            and listing.get("apify_is_pending") is False and listing.get("apify_is_hidden") is not True)


def regional_plan(cursor: int) -> list:
    """Keep one proven search each round and rotate a second region/term."""
    anchor = (("sanfrancisco", "zeta strados"), ("chicago", "zeta violin"))[cursor % 2]
    regions = ("losangeles", "seattle", "chicago", "sanfrancisco")
    terms = ("zeta violin", "zeta strados", "zeta jazz fusion", "zetta violin") + tuple(
        f"zeta {variant} violin" for variant in STRING_VARIANTS)
    secondary = (regions[cursor % len(regions)], terms[(cursor // len(regions)) % len(terms)])
    return [{"url": f"https://www.facebook.com/marketplace/{city}/search/?" +
             urlencode({"query": term, "exact": "false", "radius": 500})}
            for city, term in dict.fromkeys((anchor, secondary))]


@contextmanager
def store():
    conn = connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS apify_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("""CREATE TABLE IF NOT EXISTS apify_jobs (
            id TEXT PRIMARY KEY, phase TEXT, run_id TEXT, period TEXT,
            cap INTEGER, created REAL, done INTEGER DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS apify_items (
            url TEXT PRIMARY KEY, candidate TEXT, details TEXT, checked REAL DEFAULT 0)""")
        conn.commit()
        yield conn
    finally:
        conn.close()


class ApifyFacebookScraper(BaseScraper):
    name = "Facebook Marketplace"

    def is_configured(self) -> bool:
        return bool(Config.APIFY_ENABLED and Config.APIFY_TOKEN and
                    IDENTIFIER.fullmatch(Config.APIFY_DISCOVERY_TASK_ID) and
                    IDENTIFIER.fullmatch(Config.APIFY_DETAILS_TASK_ID))

    def _reserve(self, phase: str, cap: float) -> str:
        """Atomic, fail-closed reservation; never refund a potentially charged run."""
        with store() as conn:
            conn.execute("BEGIN IMMEDIATE")
            period = datetime.now(timezone.utc).strftime("%Y-%m")
            used = conn.execute("SELECT COALESCE(SUM(cap),0) FROM apify_jobs WHERE period=?", (period,)).fetchone()[0]
            units = _money(cap)
            if units <= 0 or used + units > _money(Config.APIFY_MONTHLY_LIMIT_USD):
                self.failures.append("Apify monthly budget reserved; collection skipped")
                conn.rollback()
                return ""
            if phase == "details" and conn.execute(
                    "SELECT 1 FROM apify_jobs WHERE phase='details' AND done=0 AND created>?",
                    (time.time() - 300,)).fetchone():
                # A lost POST response can still mean a running paid job.
                self.failures.append("Apify detail launch uncertain; retry deferred")
                conn.rollback()
                return ""
            if phase == "discovery":
                row = conn.execute("SELECT value FROM apify_state WHERE key='last_discovery'").fetchone()
                if row and time.time() - float(row[0]) < Config.APIFY_GUARD_HOURS * 3600:
                    conn.rollback()
                    return ""
                conn.execute("INSERT OR REPLACE INTO apify_state VALUES ('last_discovery',?)", (str(time.time()),))
                conn.execute("INSERT OR REPLACE INTO apify_state VALUES ('cursor',?)", (str(self._cursor + 1),))
            reservation = uuid.uuid4().hex
            conn.execute("INSERT INTO apify_jobs (id,phase,period,cap,created) VALUES (?,?,?,?,?)",
                         (reservation, phase, period, units, time.time()))
            conn.commit()
            return reservation

    async def _request(self, client: httpx.AsyncClient, method: str, path: str, **kwargs) -> object:
        response = await client.request(method, API + path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"Apify HTTP {response.status_code}")
        return response.json()

    async def read_run(self, client: httpx.AsyncClient, run_id: str) -> list:
        """Read an existing run, including partial output, without starting a new one."""
        if not IDENTIFIER.fullmatch(run_id):
            raise ValueError("Invalid Apify run ID")
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            result = await self._request(client, "GET", f"/actor-runs/{run_id}", params={"waitForFinish": 20})
            run = result["data"]
            if run.get("status") in TERMINAL:
                break
        else:
            raise TimeoutError("Apify run still pending; will resume its ID")
        if run["status"] != "SUCCEEDED":
            self.failures.append("Apify partial run: " + str(run["status"]))
        message = str(run.get("statusMessage", "")).lower()
        if "maximum" in message and any(word in message for word in ("charge", "cost", "budget")):
            self.failures.append("Apify run cost cap reached; partial coverage")
        dataset = run.get("defaultDatasetId", "")
        if not IDENTIFIER.fullmatch(dataset):
            raise ValueError("Apify run has no readable dataset")
        rows = await self._request(client, "GET", f"/datasets/{dataset}/items",
                                   params={"format": "json", "clean": "true", "limit": 100})
        if not isinstance(rows, list):
            raise ValueError("Apify dataset is not a list")
        if len(rows) == 100:
            self.failures.append("Apify dataset read limit reached; partial coverage")
        if any(isinstance(row, dict) and row.get("error") for row in rows):
            self.failures.append("Apify partial coverage: dataset contains collection errors")
        return rows

    async def _start(self, client: httpx.AsyncClient, phase: str, urls: list, cap: float) -> tuple:
        reservation = self._reserve(phase, cap)
        if not reservation:
            return "", []
        task = Config.APIFY_DISCOVERY_TASK_ID if phase == "discovery" else Config.APIFY_DETAILS_TASK_ID
        result = await self._request(client, "POST", f"/actor-tasks/{task}/runs",
            params={"maxTotalChargeUsd": cap, "timeout": 180, "memory": 4096, "restartOnError": "false"},
            json={"startUrls": urls, "resultsLimit": 5 if phase == "discovery" else len(urls),
                  "includeListingDetails": phase == "details"})
        run_id = result["data"]["id"]
        if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
            raise ValueError("Apify did not return a run ID; reservation retained")
        with store() as conn:
            conn.execute("UPDATE apify_jobs SET run_id=? WHERE id=?", (run_id, reservation))
            conn.commit()
        log.info("Apify %s run %s, reserved maximum $%.3f", phase, run_id, cap)
        return reservation, await self.read_run(client, run_id)

    def _consume(self, reservation: str, phase: str, rows: list) -> set:
        observed = set()
        with store() as conn:
            for row in rows:
                listing = normalize(row)
                if not listing:
                    continue
                self.fetched += 1
                if phase == "discovery":
                    reason = classify(listing)
                    if reason or listing["apify_is_sold"] is True or listing["apify_is_pending"] is True:
                        log.info("Apify discovery %s rejected: %s", listing["url"], reason or "sold/pending")
                        continue
                url = listing["url"]
                observed.add(url)
                conn.execute("INSERT INTO apify_items (url,candidate) VALUES (?,?) ON CONFLICT(url) DO UPDATE SET candidate=excluded.candidate",
                             (url, json.dumps(listing, ensure_ascii=False)))
                if phase == "details":
                    if any(listing[key] is None for key in ("apify_is_live", "apify_is_sold", "apify_is_pending")):
                        self.failures.append("Apify detail availability missing; candidate withheld")
                    listing["apify_checked_at"] = datetime.now(timezone.utc).isoformat()
                    conn.execute("UPDATE apify_items SET details=?, checked=? WHERE url=?",
                                 (json.dumps(listing, ensure_ascii=False), time.time(), url))
            conn.execute("UPDATE apify_jobs SET done=1 WHERE id=?", (reservation,))
            conn.commit()
        return observed

    async def search(self) -> list:
        if not hasattr(self, "failures"):
            self.reset_health()
        if not self.is_configured():
            self.skipped = True
            log.info("Apify Facebook not configured or disabled; skipping")
            return []
        cutoff = time.time() - Config.APIFY_DETAIL_TTL_HOURS * 3600
        try:
            with store() as conn:
                row = conn.execute("SELECT value FROM apify_state WHERE key='cursor'").fetchone()
                self._cursor = int(row[0]) if row else 0
                pending = conn.execute("SELECT id,phase,run_id FROM apify_jobs WHERE done=0 AND run_id IS NOT NULL").fetchall()
            async with self.make_client(timeout=30, follow_redirects=False,
                    headers={"Authorization": "Bearer " + Config.APIFY_TOKEN}) as client:
                if pending:
                    for reservation, phase, run_id in pending:
                        try:
                            rows = await self.read_run(client, run_id)
                        except RuntimeError as exc:
                            if str(exc) not in ("Apify HTTP 404", "Apify HTTP 410"):
                                raise
                            with store() as conn:
                                conn.execute("UPDATE apify_jobs SET done=1 WHERE id=?", (reservation,))
                                conn.commit()
                            self.failures.append("Apify expired run; reservation retained, results unavailable")
                            continue
                        self._consume(reservation, phase, rows)
                else:
                    reservation, rows = await self._start(client, "discovery", regional_plan(self._cursor), Config.APIFY_DISCOVERY_CAP_USD)
                    if reservation:
                        self._consume(reservation, "discovery", rows)
                    else:
                        self.skipped = True
                        log.info("Apify discovery skipped: guard or monthly reservation limit")
                with store() as conn:
                    queue = []
                    for url, candidate in conn.execute("SELECT url,candidate FROM apify_items WHERE checked<? ORDER BY checked,url", (cutoff,)):
                        listing = json.loads(candidate)
                        if classify(listing) or listing["apify_is_sold"] is True or listing["apify_is_pending"] is True:
                            continue
                        queue.append({"url": url})
                        if len(queue) == 2:
                            break
                if queue:
                    reservation, rows = await self._start(client, "details", queue, Config.APIFY_DETAILS_CAP_USD)
                    if reservation:
                        self._consume(reservation, "details", rows)
                        self.skipped = False
        except Exception as exc:
            # No credential-bearing exception strings or response bodies in logs.
            label = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            self.failures.append("Apify collection deferred: " + label)
            log.warning("Apify collection deferred (%s); reservations and known run IDs retained", label)
        ready = []
        try:
            with store() as conn:
                # Reapply current rules to every fresh verified record, including
                # previously rejected instruments. Main's delivery DB deduplicates.
                for (details,) in conn.execute("SELECT details FROM apify_items WHERE details IS NOT NULL AND checked>=? ORDER BY url", (cutoff,)):
                    listing = json.loads(details)
                    reason = classify(listing)
                    if available(listing) and not reason:
                        ready.append(listing)
                        log.info("Apify detail %s eligible", listing["url"])
                    else:
                        log.info("Apify detail %s withheld: %s", listing["url"], reason or "availability unconfirmed/sold/pending/hidden")
        except Exception as exc:
            self.failures.append("Apify cache unavailable: " + type(exc).__name__)
        log.info("Apify Facebook: %d fetched, %d verified candidates; %s", self.fetched, len(ready), self.health_error() or "OK")
        return ready
