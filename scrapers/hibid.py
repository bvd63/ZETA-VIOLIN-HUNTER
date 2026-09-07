"""
HiBid.com scraper — the largest US/CA online auction platform for estate,
consignment and business liquidation auctions (thousands of auction houses).

Verified 2026-09-07: hibid.com/graphql answers unauthenticated `lotSearch`
queries from EU IPs (the HTML search page is a JS shell). Lot URL:
https://hibid.com/lot/{id}
"""

import logging
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://hibid.com/graphql"

# Broad queries; description search + local Zeta-signal filter do the work.
QUERIES = ["zeta violin", "zeta", "electric violin", "strados"]
PAGE_LENGTH = 100

QUERY = """
query LotSearch($text: String!, $pageLength: Int!) {
  lotSearch(input: {searchText: $text, status: OPEN}, pageLength: $pageLength) {
    pagedResults {
      results {
        id lead description itemId lotNumber bidAmount pictureCount
        featuredPicture { fullSizeLocation thumbnailLocation }
        auction { id eventName eventDateEnd auctioneer { name state country city } }
        lotState { status bidCount highBid minBid timeLeftSeconds isArchived }
      }
    }
  }
}
"""

HEADERS = {
    "User-Agent": BROWSER_UA,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://hibid.com",
    "Referer": "https://hibid.com/lots",
}


class HiBidScraper(BaseScraper):
    name = "HiBid"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with self.make_client(headers=HEADERS) as client:
            for q in QUERIES:
                try:
                    resp = await client.post(GRAPHQL_URL, json={
                        "query": QUERY, "variables": {"text": q, "pageLength": PAGE_LENGTH},
                    })
                    if resp.status_code != 200:
                        log.warning(f"HiBid '{q}' HTTP {resp.status_code}: {resp.text[:150]!r}")
                        continue
                    data = resp.json()
                    if data.get("errors"):
                        log.warning(f"HiBid '{q}' GraphQL errors: {str(data['errors'])[:200]}")
                        continue
                    lots = (((data.get("data") or {}).get("lotSearch") or {}).get("pagedResults") or {}).get("results") or []
                    self.fetched += len(lots)

                    for lot in lots:
                        lot_id = str(lot.get("id") or "")
                        title = (lot.get("lead") or "").strip()
                        description = (lot.get("description") or "").strip()
                        if not lot_id or not title:
                            continue
                        if not has_zeta_signal(title + " " + description):
                            continue
                        state = lot.get("lotState") or {}
                        if state.get("isArchived") or str(state.get("status", "OPEN")).upper() not in ("OPEN", "BIDDING", ""):
                            continue
                        unique_id = self._make_id("hibid", lot_id)
                        if unique_id in seen_ids:
                            continue
                        seen_ids.add(unique_id)

                        # bidAmount is a placeholder (123.45) on lots without bids — ignore it
                        if (state.get("bidCount") or 0) > 0 and state.get("highBid"):
                            price = f"{state['highBid']} USD (current bid)"
                        elif state.get("minBid"):
                            price = f"{state['minBid']} USD (min bid)"
                        else:
                            price = "N/A"
                        auction = lot.get("auction") or {}
                        house = auction.get("auctioneer") or {}
                        location = ", ".join(p for p in [house.get("city", ""), house.get("state", ""), house.get("country", "")] if p)
                        pic = lot.get("featuredPicture") or {}
                        image_url = pic.get("fullSizeLocation") or pic.get("thumbnailLocation") or ""

                        if self._is_excluded(title):
                            continue
                        if not self._year_in_range(title + " " + description):
                            continue

                        results.append({
                            "id": unique_id,
                            "platform": f"HiBid ({house.get('name', 'auction')})"[:60],
                            "title": title,
                            "price": price,
                            "location": location or "USA",
                            "url": f"https://hibid.com/lot/{lot_id}",
                            "description": (f"{auction.get('eventName', '')} — ends {str(auction.get('eventDateEnd', ''))[:10]}. "
                                            f"{description}")[:300],
                            "date_posted": "",
                            "image_url": image_url,
                            "relevance_score": self._relevance_score(title, description),
                        })
                except Exception as e:
                    log.warning(f"HiBid '{q}' error: {e}")

        log.info(f"HiBid: {len(results)} listings found")
        return results
