"""Two-stage Facebook collection, bounded before POST and durable across restarts.

Task input and run options are overridden explicitly: console caps are not API
caps. Never retry an uncertain run creation; keep its full budget reservation.
"""

import calendar
import json
import math
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
from filters import classify, has_zeta_signal, has_violin_word, has_primary_identity
from keywords import STRING_VARIANTS, preference_matches
from scrapers.base import BaseScraper
from apify_costs import ensure_table, spent_micro, settle

log = logging.getLogger(__name__)
API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9]+$")
ITEM = re.compile(r"^/marketplace/item/(\d+)/?$")


def _money(value: float) -> int:
    return int(Decimal(str(value)) * 1_000_000)


def _text(value: object) -> str:
    value = value.get('text') if isinstance(value,dict) else value
    return value if isinstance(value,str) else ''


def _flag(row: dict, camel: str, snake: str) -> object:
    value = row.get(camel, row.get(snake))
    return value if isinstance(value, bool) else None


def _price_amount(value: object):
    if isinstance(value,bool):
        return None
    if isinstance(value,(int,float)):
        try:
            return value if math.isfinite(value) and value >= 0 else None
        except OverflowError:
            return None
    if isinstance(value,str) and re.fullmatch(r'[0-9]+(?:[.,\s][0-9]+)*',value.strip()):
        return value.strip()
    return None


def normalize(row: dict) -> dict:
    """Accept only actual public Facebook item URLs; support both observed schemas."""
    if not isinstance(row, dict) or row.get("error"):
        return {}
    raw_url = row.get("itemUrl") or row.get("listingUrl") or row.get('url') or ""
    if not isinstance(raw_url, str):
        return {}
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return {}
    match = ITEM.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.hostname not in ("facebook.com", "www.facebook.com", "m.facebook.com") or not match:
        return {}
    title = _text(row.get("listingTitle") or row.get("marketplace_listing_title") or row.get('title'))
    if not title:
        return {}
    price = row.get("listingPrice") or row.get("listing_price") or {}
    if not isinstance(price, dict):
        price = {}
    amount = _price_amount(price.get('amount'))
    if amount is None and _price_amount(row.get('priceValue')) is not None:
        price = {'amount': row['priceValue'], 'currency': row.get('priceCurrency') or 'USD'}
        amount = _price_amount(row['priceValue'])
    location = _text(row.get("locationText")) or _text(row.get('location_text'))
    if not location:
        raw_location = row.get("location")
        geo = raw_location.get("reverse_geocode", {}) if isinstance(raw_location, dict) else {}
        geo = geo if isinstance(geo, dict) else {}
        location = ", ".join(str(geo[k]) for k in ("city", "state") if geo.get(k))
    photo = row.get("primaryListingPhoto") or row.get("primary_listing_photo") or {}
    photos = row.get("listingPhotos") or row.get('listing_photos') or []
    if not photo and isinstance(photos, list) and photos:
        photo = photos[0]
    photo = photo if isinstance(photo, dict) else {}
    raw_image = photo.get("image")
    raw_image = raw_image if isinstance(raw_image, dict) else {}
    image = (photo.get("photo_image_url") or raw_image.get("uri", "") or row.get('primary_listing_photo_url')
             or row.get('imageUrl') or row.get('image_url') or '')
    image = image if isinstance(image, str) else ""
    item_id = match.group(1)
    return {
        "id": BaseScraper()._make_id("facebook_marketplace", item_id),
        "platform": "Facebook Marketplace", "title": title,
        "price": f"{amount} {price.get('currency', 'USD')}" if amount is not None else "N/A",
        "location": location or "Unknown", "url": f"https://www.facebook.com/marketplace/item/{item_id}/",
        "description": (_text(row.get('description')) or _text(row.get('redacted_description')) or _text(
            row.get('moreDetails', {}).get('redacted_description') if isinstance(row.get('moreDetails'), dict) else '')),
        "condition": _text(row.get("condition")),
        "image_url": image, "relevance_score": 8, "provider": "apify",
        "apify_is_live": _flag(row, "isLive", "is_live"),
        "apify_is_sold": _flag(row, "isSold", "is_sold"),
        "apify_is_pending": _flag(row, "isPending", "is_pending"),
        "apify_is_hidden": _flag(row, "isHidden", "is_hidden"),
    }


def available(listing: dict) -> bool:
    return (listing.get("apify_is_live") is True and listing.get("apify_is_sold") is False
            and listing.get("apify_is_pending") is False and listing.get("apify_is_hidden") is not True)


def unavailable(listing: dict) -> bool:
    return (listing.get('apify_is_sold') is True or listing.get('apify_is_pending') is True
            or listing.get('apify_is_hidden') is True or listing.get('apify_is_live') is False)


def retain_known_negative_flags(listing: dict, *previous: dict) -> dict:
    """An omitted flag is not a reactivation of the same item."""
    listing = dict(listing)
    for old in previous:
        if not old or old.get('url') != listing.get('url'):
            continue
        for key,negative in (('apify_is_live',False),('apify_is_sold',True),
                             ('apify_is_pending',True),('apify_is_hidden',True)):
            if listing.get(key) is None and old.get(key) is negative:
                listing[key] = negative
    return listing


def unknown_candidate(candidate: dict, details: dict | None, checked: float, cutoff: float) -> dict | None:
    """Strong identity can reach the shared unknown-availability route.

    A saved discovery card never becomes verified detail. Explicit negative
    evidence wins, and a complete fresh primary result cannot borrow its title.
    """
    if details and details.get('url') != candidate.get('url'):
        details = None
    if unavailable(candidate) or (details and unavailable(details)):
        return None
    if details and details.get('url') == candidate.get('url'):
        if checked >= cutoff and all(details.get(k) is not None for k in
                ('apify_is_live','apify_is_sold','apify_is_pending')):
            return None
        if classify(details) in ('noise','other_brand','intent','new_stock'):
            return None
    if classify(candidate) or not has_primary_identity(candidate):
        return None
    result = {**candidate, 'source':'search', '_direct_item_candidate':True,
              '_facebook_unverified_details':True}
    for key in ('verification','liveness','_primary_content_checked','apify_checked_at'):
        result.pop(key,None)
    return result


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
        ensure_table(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS apify_jobs (
            id TEXT PRIMARY KEY, phase TEXT, run_id TEXT, period TEXT,
            cap INTEGER, created REAL, done INTEGER DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS apify_items (
            url TEXT PRIMARY KEY, candidate TEXT, details TEXT, checked REAL DEFAULT 0)""")
        conn.execute('''CREATE TABLE IF NOT EXISTS apify_item_attempts (
            url TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
            next_attempt REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS apify_batch_targets (
            reservation TEXT, url TEXT, PRIMARY KEY(reservation,url))''')
        conn.commit()
        yield conn
    finally:
        conn.close()


def discovery_reason(listing: dict) -> str:
    """A card is incomplete; plausible instruments need the existing detail stage."""
    reason = classify(listing)
    text = str(listing.get("title") or "") + " " + str(listing.get("description") or "")
    if reason == "non_zeta" and (has_zeta_signal(text) or has_violin_word(text)):
        return ""
    return reason


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
            used = spent_micro(conn, period)
            units = _money(cap)
            if units <= 0 or used + units > _money(Config.APIFY_MONTHLY_LIMIT_USD):
                self.failures.append("Apify monthly budget reserved; collection skipped")
                conn.rollback()
                return ""
            auxiliary = getattr(self, 'manual_fresh', False) or getattr(self, 'auxiliary_work', False)
            if auxiliary:
                now = datetime.now(timezone.utc)
                days_left = calendar.monthrange(now.year, now.month)[1] - now.day + 1
                pair_cap = Config.APIFY_DISCOVERY_CAP_USD + Config.APIFY_DETAILS_CAP_USD
                protected = _money(days_left * Config.RUNS_PER_DAY * pair_cap)
                account = {}
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='provider_state'").fetchone():
                    row = conn.execute("SELECT value FROM provider_state WHERE key='apify:account'").fetchone()
                    account = json.loads(row[0]) if row else {}
                account_safe = False
                try:
                    raw_end = account['usage_cycle']['endAt']
                    if not isinstance(raw_end, str):
                        raise ValueError('account cycle unavailable')
                    end = datetime.fromisoformat(raw_end.replace('Z', '+00:00'))
                    account_days = max(0, (end - now).total_seconds() / 86400)
                    account_protected = math.ceil(account_days) * Config.RUNS_PER_DAY * pair_cap
                    # The cached account meter cannot include later/uncertain jobs.
                    # Preserve their full cap until authenticated settlement exists.
                    since_meter = conn.execute('''SELECT coalesce(sum(coalesce(c.cost,j.cap)),0)
                        FROM apify_jobs j LEFT JOIN apify_costs c ON c.run_id=j.run_id
                        WHERE j.created>=? OR c.run_id IS NULL OR c.checked>=?''',
                        (account.get('checked', 0), account.get('checked', 0))).fetchone()[0]
                    account_safe = (not account.get('error') and account.get('checked', 0) > time.time() - 12 * 3600
                                    and _money(account['included_monthly_usd'] - account['usage_usd'])
                                    >= units + _money(account_protected) + since_meter)
                except (KeyError, TypeError, ValueError):
                    account_safe = False
                if used + units + protected > _money(Config.APIFY_MONTHLY_LIMIT_USD) or not account_safe:
                    self.failures.append('Apify extra work deferred: scheduled capacity/account credit protected')
                    conn.rollback()
                    return ''
            if phase == "details" and conn.execute(
                    "SELECT 1 FROM apify_jobs WHERE phase='details' AND done=0 AND created>?",
                    (time.time() - 300,)).fetchone():
                # A lost POST response can still mean a running paid job.
                self.failures.append("Apify detail launch uncertain; retry deferred")
                conn.rollback()
                return ""
            if phase == "discovery":
                guard_key = 'last_manual_discovery' if auxiliary else 'last_discovery'
                row = conn.execute("SELECT value FROM apify_state WHERE key=?", (guard_key,)).fetchone()
                if row and time.time() - float(row[0]) < Config.APIFY_GUARD_HOURS * 3600:
                    conn.rollback()
                    return ""
                conn.execute("INSERT OR REPLACE INTO apify_state VALUES (?,?)", (guard_key, str(time.time())))
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
        settle(run_id, run)
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
        if phase == 'details':
            # Persist the expected IDs before POST. An error row or missing
            # result must not leave an oldest candidate immediately due forever.
            with store() as conn:
                conn.executemany('INSERT OR IGNORE INTO apify_batch_targets VALUES (?,?)',
                                 [(reservation, target['url']) for target in urls])
                conn.executemany('''INSERT INTO apify_item_attempts (url,next_attempt,error) VALUES (?,?,?)
                    ON CONFLICT(url) DO UPDATE SET next_attempt=excluded.next_attempt,error=excluded.error''',
                    [(target['url'], time.time()+18*3600, 'detail POST may be billed; awaiting outcome') for target in urls])
                conn.commit()
        task = Config.APIFY_DISCOVERY_TASK_ID if phase == "discovery" else Config.APIFY_DETAILS_TASK_ID
        if phase == "discovery":
            for target in urls:
                self.record_discovery_query(target["url"])
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
            expected = {row[0] for row in conn.execute('SELECT url FROM apify_batch_targets WHERE reservation=?', (reservation,))}
            for row in rows:
                listing = normalize(row)
                if not listing:
                    continue
                self.fetched += 1
                observed_negative = unavailable(listing)
                previous = conn.execute('SELECT candidate,details,checked FROM apify_items WHERE url=?',
                                        (listing['url'],)).fetchone()
                if previous:
                    listing = retain_known_negative_flags(listing,json.loads(previous[0]),
                                json.loads(previous[1]) if previous[1] else {})
                if phase == "discovery":
                    # Keep new negative evidence for an existing URL even when
                    # it is no longer an eligible discovery result. Otherwise
                    # stale verified details/unknown cards could still alert.
                    if previous and unavailable(listing):
                        flags = {k:listing[k] for k in ('apify_is_live','apify_is_sold','apify_is_pending','apify_is_hidden')
                                 if listing.get(k) is not None}
                        candidate = {**json.loads(previous[0]), **flags}
                        old_detail = json.loads(previous[1]) if previous[1] else {}
                        if old_detail.get('url') != listing['url']:
                            old_detail = candidate
                        detail = {**old_detail, **flags}
                        conn.execute('UPDATE apify_items SET candidate=?,details=?,checked=? WHERE url=?',
                                     (json.dumps(candidate),json.dumps(detail),
                                      time.time() if observed_negative else previous[2],listing['url']))
                    elif previous and available(listing) and previous[1] and unavailable(json.loads(previous[1])):
                        # An explicitly reactivated card invalidates an old
                        # hold; full detail is checked again in the same slots.
                        conn.execute('UPDATE apify_items SET details=NULL,checked=0 WHERE url=?',(listing['url'],))
                    reason = discovery_reason(listing)
                    if reason or listing["apify_is_sold"] is True or listing["apify_is_pending"] is True:
                        log.info("Apify discovery %s rejected: %s", listing["url"], reason or "sold/pending")
                        continue
                url = listing["url"]
                if phase == 'details' and expected and url not in expected:
                    self.failures.append('Apify detail returned an unexpected item ID; requested candidates retained')
                    continue
                observed.add(url)
                if phase == 'discovery':
                    conn.execute("INSERT INTO apify_items (url,candidate) VALUES (?,?) ON CONFLICT(url) DO UPDATE SET candidate=excluded.candidate",
                                 (url, json.dumps(listing, ensure_ascii=False)))
                else:
                    # Detail output may be partial/generic. Never erase the
                    # original discovery evidence used to select later retries.
                    conn.execute("INSERT OR IGNORE INTO apify_items (url,candidate) VALUES (?,?)",
                                 (url, json.dumps(listing, ensure_ascii=False)))
                if phase == "details":
                    incomplete = any(listing[key] is None for key in ("apify_is_live", "apify_is_sold", "apify_is_pending"))
                    if incomplete:
                        self.failures.append("Apify detail availability missing; candidate withheld")
                        observed.discard(url)
                    listing["apify_checked_at"] = datetime.now(timezone.utc).isoformat()
                    conn.execute("UPDATE apify_items SET details=?, checked=? WHERE url=?",
                                 (json.dumps(listing, ensure_ascii=False), 0 if incomplete else time.time(), url))
                    if not incomplete:
                        current = conn.execute('SELECT candidate FROM apify_items WHERE url=?',(url,)).fetchone()
                        if current:
                            saved = json.loads(current[0])
                            saved.update({k:listing[k] for k in
                                ('apify_is_live','apify_is_sold','apify_is_pending','apify_is_hidden')
                                if listing.get(k) is not None})
                            conn.execute('UPDATE apify_items SET candidate=? WHERE url=?',(json.dumps(saved),url))
                        conn.execute('DELETE FROM apify_item_attempts WHERE url=?', (url,))
            if phase == 'details':
                if expected - observed:
                    self.failures.append('Apify requested detail missing/incomplete; retained with per-item retry')
                for url in expected - observed:
                    previous = conn.execute('SELECT failures FROM apify_item_attempts WHERE url=?', (url,)).fetchone()
                    failures = min(8, (previous[0] if previous else 0) + 1)
                    delay = min(72 * 3600, 18 * 3600 * 2 ** (failures - 1))
                    conn.execute('INSERT OR REPLACE INTO apify_item_attempts VALUES (?,?,?,?)',
                                 (url, failures, time.time() + delay, 'detail missing/no_items; not sold proof'))
                    log.warning('Apify detail %s deferred for %sh; candidate retained', url, delay // 3600)
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
                            self._consume(reservation, phase, [])
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
                    for url, candidate in conn.execute('''SELECT i.url,i.candidate FROM apify_items i
                            LEFT JOIN apify_item_attempts a ON a.url=i.url
                            WHERE i.checked<? AND coalesce(a.next_attempt,0)<=?
                            ORDER BY i.checked,i.url''', (cutoff, time.time())):
                        listing = json.loads(candidate)
                        # Temporary holds are not terminal. After the unchanged
                        # detail TTL they can use the same two retry slots even
                        # if discovery never returns their URL again.
                        identity = {**listing, **{k:None for k in
                            ('apify_is_live','apify_is_sold','apify_is_pending','apify_is_hidden')}}
                        if discovery_reason(identity) or listing["apify_is_sold"] is True:
                            continue
                        queue.append((bool(classify(identity)), not bool(preference_matches(listing)), {"url": url}))
                    # One preferred slot, one oldest eligible slot: old Strados
                    # and uncertain identities remain queued without starvation.
                    queue.sort(key=lambda entry: entry[0])
                    preferred = min(queue, key=lambda entry: entry[:2]) if queue else None
                    chosen = [preferred] if preferred else []
                    chosen += [entry for entry in queue if entry is not preferred][:1]
                    queue = [entry[2] for entry in chosen]
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
        verified_count = unknown_count = 0
        try:
            with store() as conn:
                # Reapply current rules to every fresh verified record, including
                # previously rejected instruments. Main's delivery DB deduplicates.
                for candidate, details, checked in conn.execute('SELECT candidate,details,checked FROM apify_items ORDER BY url'):
                    candidate = json.loads(candidate)
                    listing = json.loads(details) if details else None
                    if listing and listing.get('url') != candidate.get('url'):
                        self.failures.append('Apify cached detail ID mismatch; original candidate retained')
                        conn.execute('UPDATE apify_items SET checked=0 WHERE url=?',(candidate['url'],))
                        listing = None
                    fallback = unknown_candidate(candidate,listing,checked,cutoff)
                    if fallback:
                        ready.append(fallback)
                        unknown_count += 1
                        continue
                    if not listing or checked < cutoff:
                        continue
                    if unavailable(candidate):
                        continue
                    reason = classify(listing)
                    if available(listing) and not reason:
                        ready.append(listing)
                        verified_count += 1
                        log.info("Apify detail %s eligible", listing["url"])
                    else:
                        log.info("Apify detail %s withheld: %s", listing["url"], reason or "availability unconfirmed/sold/pending/hidden")
                conn.commit()
        except Exception as exc:
            self.failures.append("Apify cache unavailable: " + type(exc).__name__)
        self.coverage_stats = {'cached_verified_details':verified_count,'strong_unverified_candidates':unknown_count}
        log.info("Apify Facebook: %d fetched, %d verified, %d strong unverified candidates; %s",
                 self.fetched,verified_count,unknown_count,self.health_error() or "OK")
        return ready
