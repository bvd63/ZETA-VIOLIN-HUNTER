"""
ShopGoodwill.com scraper — Goodwill's US-wide online auction site.

Donated instruments are auctioned here daily and descriptions are searchable.
Verified 2026-09-07: the buyer API answers without authentication:
  POST https://buyerapi.shopgoodwill.com/api/Search/ItemListing  (JSON body)
Item URL: https://shopgoodwill.com/item/{itemId}
"""

from keywords import market_queries

import httpx
import asyncio
import hashlib
import json
import logging
import time
from scrapers.base import BaseScraper, BROWSER_UA
from native_candidates import card_hints
from provider_state import store
from http_limits import retry_after_seconds
from discovery_queue import DiscoveryQueue

log = logging.getLogger(__name__)

API_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"

# Broad queries; the Zeta-signal filter on title + description does the work.
PAGE_SIZE = 40
MAX_PAGES = 3
REPLAY_QUERIES = 2  # Additional failed work; never replaces an original query.
RETRY_PREFIX = 'shopgoodwill:pending_query:'


def _query_key(query):
    return RETRY_PREFIX + hashlib.sha256(query.encode()).hexdigest()


def _pending_queries(planned):
    """Stage BEFORE HTTP so unfinished rotated terms survive cancellation."""
    now = time.time()
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for query in planned:
            value = {'query':query, 'created':now, 'due':0, 'attempts':0}
            conn.execute('INSERT OR IGNORE INTO provider_state VALUES (?,?)',
                         (_query_key(query), json.dumps(value)))
        rows = conn.execute('SELECT value FROM provider_state WHERE key LIKE ?',
                            (RETRY_PREFIX + '%',)).fetchall()
        conn.commit()
    pending = []
    for row in rows:
        value = json.loads(row[0])
        if isinstance(value, dict) and isinstance(value.get('query'), str):
            pending.append(value)
    return sorted(pending, key=lambda v: (v.get('last_attempt', v.get('created', 0)), v['query']))


def _finish_query(query, error='', page=1, delay=3600):
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        key = _query_key(query)
        row = conn.execute('SELECT value FROM provider_state WHERE key=?', (key,)).fetchone()
        if error:
            value = json.loads(row[0]) if row else {'query':query, 'created':time.time(), 'attempts':0}
            value.update(due=time.time() + delay, attempts=value.get('attempts', 0) + 1,
                         error=error[:200], failed_page=page, last_attempt=time.time())
            conn.execute('INSERT OR REPLACE INTO provider_state VALUES (?,?)', (key, json.dumps(value)))
        else:
            conn.execute('DELETE FROM provider_state WHERE key=?', (key,))
        conn.commit()


class SearchFailure(ValueError):
    def __init__(self, reason, delay=3600):
        super().__init__(reason)
        self.delay = delay

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://shopgoodwill.com",
    "Referer": "https://shopgoodwill.com/",
}


def _body(text: str, page: int) -> dict:
    return {
        "isSize": False, "isWeddingCatalog": False, "mainCategoryId": None, "catIds": "",
        "categoryId": None, "categoryLevel": 1, "categoryLevelNo": "1",
        "closedAuctionDaysBack": "7", "closedAuctionEndingDate": "",
        "highPrice": "999999", "lowPrice": "0", "page": page, "pageSize": PAGE_SIZE,
        "partNumber": "", "savedSearchId": 0, "searchBuyNowOnly": "",
        "searchCanadaShipping": "false", "searchClosedAuctions": "false",
        "searchDescriptions": "true", "searchInternationalShippingOnly": "false",
        "searchNoPickupOnly": "false", "searchOneCentShippingOnly": "false",
        "searchPickupOnly": "false", "searchText": text, "searchUSOnlyShipping": "false",
        "selectedCategoryIds": "", "selectedGroup": "", "selectedSellerIds": "",
        "sortColumn": "1", "sortDescending": "false", "useBuyerPrefs": "true",
    }


class ShopGoodwillScraper(BaseScraper):
    name = "ShopGoodwill"

    async def _page(self, client, query, page):
        # This fixed anonymous endpoint only searches; it neither buys/bids nor
        # invokes a paid extraction. Do not apply this POST retry to other APIs.
        first_errors = len(getattr(self, 'failures', []))
        for attempt in range(2):
            self.record_discovery_query(f'{query!r} country=USA page={page} attempt={attempt + 1}')
            try:
                resp = await client.post(API_URL, json=_body(query, page))
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt:
                    raise
                await asyncio.sleep(1.5)
                continue
            if resp.status_code in (408, 500, 502, 503, 504) and not attempt:
                await asyncio.sleep(1.5)
                continue
            if resp.status_code != 200:
                delay = retry_after_seconds(resp.headers.get('Retry-After')) if resp.status_code == 429 else 3600
                raise SearchFailure(f'HTTP {resp.status_code}', delay)
            payload = resp.json()
            search = payload.get('searchResults') if isinstance(payload, dict) else None
            if not isinstance(search, dict) or not isinstance(search.get('items'), list):
                raise SearchFailure('invalid searchResults/items schema')
            if attempt:
                # Only this request's failed HTTP attempt was recovered. Keep
                # unresolved failures from all the other query/page requests.
                del self.failures[first_errors:]
                self.requests_recovered = getattr(self, 'requests_recovered', 0) + 1
            return search['items']

    async def search(self) -> list:
        if not hasattr(self, 'failures'):
            self.reset_health()
        results = []
        seen_ids = set()
        planned = list(market_queries("en", broad=True, limit=8, extra=("electric violin",)))
        pending = _pending_queries(planned)
        due = {v['query']:v for v in pending}
        extras = [v['query'] for v in pending if v['query'] not in planned
                  and v.get('due', 0) <= time.time()][:REPLAY_QUERIES]
        self.coverage_stats = {'planned_queries':planned, 'resumed_queries':extras,
                               'query_responses':[], 'queries_deferred':[]}

        async with self.make_client(timeout=20, follow_redirects=True, headers=HEADERS) as client:
            for q in planned + extras:
                if due.get(q, {}).get('due', 0) > time.time():
                    self.coverage_stats['queries_deferred'].append(q)
                    self.content_failures.append('query retained until retry is due')
                    continue
                complete = True
                for page in range(1, MAX_PAGES + 1):
                    try:
                        items = await self._page(client, q, page)
                        self.coverage_stats['query_responses'].append({'query':q, 'page':page, 'rows':len(items)})
                        if not items:
                            break
                        self.fetched += len(items)
                        page_cards = []
                        for item in items:
                            if not isinstance(item, dict):
                                complete = False
                                _finish_query(q, 'invalid item record', page)
                                self.content_failures.append('invalid item retained for query retry')
                                continue
                            item_id = str(item.get("itemId") or "")
                            title = (item.get("title") or "").strip()
                            description = (item.get("description") or "").strip()
                            if not item_id or not title:
                                continue
                            hints = card_hints(title, description, q)
                            if hints is None:
                                continue
                            unique_id = self._make_id("shopgoodwill", item_id)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            price_val = item.get("buyNowPrice") or item.get("currentPrice") or item.get("minimumBid")
                            price = f"{price_val} USD" if price_val else "N/A"
                            if self._is_excluded(title):
                                continue
                            if price != "N/A" and not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title + " " + description):
                                continue

                            listing = {
                                **hints,
                                "id": unique_id,
                                "platform": "ShopGoodwill", "auction": True,
                                "title": title,
                                "price": price,
                                "location": "USA",
                                "url": f"https://shopgoodwill.com/item/{item_id}",
                                "description": description,
                                "date_posted": str(item.get("startTime") or "")[:10],
                                "image_url": item.get("imageURL") or "",
                                "relevance_score": self._relevance_score(title, description),
                            }
                            results.append(listing)
                            page_cards.append(listing)

                        # Persist useful cards before completing their query.
                        # If another page/query times out, the existing bounded
                        # free-detail worker can still verify these exact URLs.
                        if page_cards:
                            with DiscoveryQueue('search:ShopGoodwill') as queue:
                                queue.put(page_cards)

                        if len(items) < PAGE_SIZE:
                            break
                    except Exception as e:
                        complete = False
                        reason = f'{type(e).__name__}: {e}'.strip()[:200]
                        _finish_query(q, reason, page, getattr(e, 'delay', 3600))
                        self.content_failures.append(f'query {q!r} p{page} retained: {reason}')
                        log.warning("ShopGoodwill %r p%s failed (%s); query retained", q, page, reason)
                        break
                if complete:
                    _finish_query(q)

        self.coverage_stats['queries_pending'] = len(_pending_queries([]))

        log.info(f"ShopGoodwill: {len(results)} listings found")
        return results
