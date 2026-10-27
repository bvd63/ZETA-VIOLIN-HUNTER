"""Reverb listing discovery using the official HAL API."""

from keywords import market_queries

import asyncio
import httpx
import logging
import re
from scrapers.base import BaseScraper
from reverb_api import build_headers, remember_availability, CLOSED
from offer_verifier import _reverb_item_id

log = logging.getLogger(__name__)

REVERB_API = "https://api.reverb.com/api/listings"

MAX_PAGES = 2
TAG_RX = re.compile(r"<[^>]+>")


def _build_headers() -> dict:
    return build_headers()


def identity_fields(item: dict) -> dict:
    """Keep the primary listing's structured make/model/category evidence."""
    raw_categories = item.get("categories") or []
    categories = []
    if isinstance(raw_categories, list):
        for category in raw_categories:
            if isinstance(category, dict):
                category = category.get("full_name") or category.get("name") or ""
            if isinstance(category, str):
                categories.append(category)
    return {"brand": item.get("make") if isinstance(item.get("make"), str) else "",
            "model": item.get("model") if isinstance(item.get("model"), str) else "",
            "finish": item.get("finish") if isinstance(item.get("finish"), str) else "",
            "categories": categories}


class ReverbScraper(BaseScraper):
    name = "Reverb"

    async def _search_page(self, client, headers, params, label):
        # Keep the original one retry on 5xx/transport errors, recording each
        # actual HTTP attempt rather than hiding retries in the page count.
        for attempt in range(2):
            self.record_discovery_query(label)
            try:
                response = await client.get(REVERB_API, headers=headers, params=params)
                if response.status_code < 500 or attempt == 1:
                    return response
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == 1:
                    raise
            await asyncio.sleep(1.5)

    async def search(self) -> list:
        results = []
        seen_ids = {}
        closed_ids = set()
        headers = _build_headers()

        # Keyword searches plus Reverb's structured brand filter (make=Zeta),
        # which catches odd titles like "Very fine rare 5 strings Zeta Midi".
        searches = [{"query": kw} for kw in market_queries(broad=True, limit=10)] + [{"make": "Zeta"}]

        async with self.make_client(timeout=15, follow_redirects=False) as client:
            for base_params in searches:
                kw = base_params.get("query") or f"make={base_params.get('make')}"
                for page in range(1, MAX_PAGES + 1):
                    try:
                        params = {**base_params, "per_page": 50, "page": page}
                        resp = await self._search_page(client, headers, params, f'{kw} page={page}')
                        if resp.status_code != 200:
                            self.content_failures.append(f'Reverb API HTTP {resp.status_code}')
                            log.warning("Reverb API HTTP %s for %r p%s",resp.status_code,kw,page)
                            break
                        data = resp.json()
                        if not isinstance(data, dict) or not isinstance(data.get('listings'),list):
                            raise ValueError('invalid Reverb search response')
                        listings = data['listings']
                        if not listings:
                            break
                        self.fetched += len(listings)

                        for item in listings:
                            try:
                                if not isinstance(item,dict):
                                    raise ValueError('invalid row')
                                status = item.get('state')
                                state = status.get('slug') if isinstance(status,dict) else None
                                state = state.lower() if isinstance(state,str) and state else None
                                confirmed = remember_availability(item)
                                url = item.get("_links", {}).get("web", {}).get("href", "")
                                listing_id = self._make_id("reverb", str(item.get("id") or url))
                                if not isinstance(url,str) or not url:
                                    continue
                                previous = seen_ids.get(listing_id)
                                if confirmed in CLOSED:
                                    closed_ids.add(listing_id)
                                    if previous is not None:
                                        results[previous] = None
                                    continue
                                if state in CLOSED:
                                    # An unbound negative is not written to the
                                    # shared registry, but must not alert alone.
                                    continue
                                if confirmed == 'live':
                                    closed_ids.discard(listing_id)
                                elif listing_id in closed_ids:
                                    continue
                                if (previous is not None and results[previous] is not None
                                        and (confirmed != 'live' or results[previous].get('source') != 'search')):
                                    continue

                                title = item.get("title", "")
                                price_obj = item.get("price", {}) or {}
                                price = f"{price_obj.get('amount', '?')} {price_obj.get('currency', '')}".strip()
                                condition = (item.get("condition") or {}).get("display_name", "")
                                location = self._location(item)
                                description = TAG_RX.sub(" ", item.get("description", "") or "")
                                description = re.sub(r"\s+", " ", description).strip()

                                image_url = ""
                                photos = item.get("photos") or []
                                if photos and isinstance(photos[0], dict):
                                    links = photos[0].get("_links", {})
                                    image_url = (links.get("large_crop") or links.get("thumbnail") or {}).get("href", "")

                                if self._is_excluded(title):
                                    continue
                                if self._is_excluded_location(location):
                                    continue
                                if not self._price_in_range(price):
                                    continue
                                if not self._year_in_range(title + " " + description):
                                    continue

                                listing = {
                                    **identity_fields(item),
                                    "id": listing_id,
                                    "platform": "Reverb",
                                    "title": title,
                                    "price": price,
                                    "location": location or "Unknown",
                                    "url": url,
                                    "description": description,
                                    "condition": condition,
                                    "seller": str((item.get("shop") or {}).get("slug") or item.get("shop_name") or ""),
                                    "ships_to_ro": self._ships_to_ro(item),
                                    "date_posted": (item.get("published_at") or "")[:10],
                                    "relevance_score": self._relevance_score(title, description),
                                    "image_url": image_url,
                                }
                                if confirmed != 'live':
                                    listing.update({'source':'search','_direct_item_candidate':True})
                                    raw_id = item.get('id')
                                    if raw_id is not None and str(raw_id) != _reverb_item_id(url):
                                        # Strong words from one ID cannot identify
                                        # a different URL when primary is blocked.
                                        listing['_query_identity_unverified'] = True
                                else:
                                    listing['_reverb_api_live'] = True
                                if previous is None:
                                    seen_ids[listing_id] = len(results)
                                    results.append(listing)
                                else:
                                    results[previous] = listing
                            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                                self.content_failures.append('Reverb invalid listing row; remaining rows retained')
                                continue
                    except Exception as e:
                        self.content_failures.append('Reverb search response unresolved: ' + type(e).__name__)
                        log.warning("Reverb keyword %r page %s error: %s",kw,page,type(e).__name__)
                        break

        results = [item for item in results if item is not None]
        log.info(f"Reverb: {len(results)} listings found")
        return results

    @staticmethod
    def _ships_to_ro(item: dict):
        """True/False from Reverb shipping regions (XX = worldwide, EUR_EU = EU,
        RO = Romania); None when the listing has no rate table."""
        rates = (item.get("shipping") or {}).get("rates") or []
        if not rates:
            return None
        codes = {str((r or {}).get("region_code", "")).upper() for r in rates}
        return bool(codes & {"XX", "EUR_EU", "EU", "RO", "EUROPE"})

    @staticmethod
    def _location(item: dict) -> str:
        """The listings API no longer returns a shop address. Derive the seller
        region from the first shipping rate (e.g. US_CON → US, GB → GB,
        XX → Worldwide); fall back to the shop address if present."""
        address = (item.get("shop") or {}).get("address") or {}
        parts = [address.get("locality", ""), address.get("region", ""), address.get("country_code", "")]
        joined = ", ".join(p for p in parts if p)
        if joined:
            return joined
        rates = (item.get("shipping") or {}).get("rates") or []
        for rate in rates:
            code = str((rate or {}).get("region_code", "")).upper()
            if code:
                return "Worldwide" if code == "XX" else code.split("_")[0]
        return "Unknown"
