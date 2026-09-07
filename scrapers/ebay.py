"""
eBay Browse API scraper — OAuth2 client credentials + search endpoint.
Replaces the dead Finding API (decommissioned 2025-02-05).

Setup: EBAY_CLIENT_ID + EBAY_CLIENT_SECRET in env vars.
Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search

Prompt 15:
  * fieldgroups=EXTENDED → shortDescription (condition + item aspects), so the
    Zeta filter sees more than the title
  * X-EBAY-C-ENDUSERCTX contextualLocation=country=RO → shippingOptions tell us
    whether the seller ships to Romania
  * bare "zeta" query restricted to Musical Instruments & Gear (category 619)
  * 19 marketplaces; unsupported ones answer 400 once and are skipped
"""

import httpx
import logging
import base64
import time
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"

MARKETPLACES = [
    "EBAY_US", "EBAY_GB", "EBAY_DE", "EBAY_FR", "EBAY_IT", "EBAY_ES", "EBAY_AU", "EBAY_CA",
    "EBAY_AT", "EBAY_CH", "EBAY_NL", "EBAY_PL", "EBAY_IE", "EBAY_BE",
    "EBAY_SG", "EBAY_HK", "EBAY_MY", "EBAY_TW", "EBAY_PH",
]

# (query, category_ids or "")
QUERIES = [
    ("Zeta violin", ""),
    ("Zeta electric violin", ""),
    ("Zeta Strados", ""),
    ("Zeta Jazz Fusion", ""),
    ("Zeta JV44", ""),
    ("Zeta SV24", ""),
    ("Zeta JLP", ""),
    ("Zetta violin", ""),
    ("zeta", "619"),   # Musical Instruments & Gear — catches titles without "violin"
]


class EbayScraper(BaseScraper):
    name = "eBay"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str = ""
        self._token_expires: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        if self._token and now < self._token_expires:
            return self._token
        if not self.is_configured():
            return ""
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        try:
            resp = await client.post(
                OAUTH_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Authorization": f"Basic {credentials}"},
                data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
                timeout=15,
            )
            if resp.status_code != 200:
                log.error(f"eBay OAuth failed: {resp.status_code} — {resp.text[:300]}")
                return ""
            data = resp.json()
            self._token = data.get("access_token", "")
            self._token_expires = now + int(data.get("expires_in", 7200)) - 1800
            log.info("eBay OAuth token acquired")
            return self._token
        except Exception as e:
            log.error(f"eBay OAuth error: {e}")
            return ""

    async def search(self) -> list:
        if not self.is_configured():
            log.warning("eBay CLIENT_ID or CLIENT_SECRET not set — skipping eBay.")
            return []

        results = []
        seen_ids = set()

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            token = await self._get_token(client)
            if not token:
                return []

            for marketplace in MARKETPLACES:
                marketplace_ok = True
                for kw, category in QUERIES:
                    if not marketplace_ok:
                        break
                    try:
                        params = {"q": kw, "limit": 50, "fieldgroups": "EXTENDED",
                                  "filter": "buyingOptions:{FIXED_PRICE|AUCTION}"}
                        if category:
                            params["category_ids"] = category
                        resp = await client.get(
                            BROWSE_URL,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                                "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=RO",
                            },
                            params=params,
                        )

                        if resp.status_code == 401:
                            self._token = ""
                            token = await self._get_token(client)
                            if not token:
                                return results
                            continue
                        if resp.status_code == 429:
                            log.warning(f"eBay rate limit hit on {marketplace}")
                            break
                        if resp.status_code == 400 and "marketplace" in resp.text.lower():
                            log.info(f"eBay {marketplace}: not supported by Browse API — skipping")
                            marketplace_ok = False
                            break
                        if resp.status_code != 200:
                            log.warning(f"eBay {marketplace} '{kw}': HTTP {resp.status_code} — {resp.text[:200]}")
                            continue

                        items = resp.json().get("itemSummaries", []) or []
                        self.fetched += len(items)

                        for item in items:
                            ebay_item_id = item.get("itemId", "")
                            url = item.get("itemWebUrl", "")
                            if not url or not ebay_item_id:
                                continue
                            # Dedup on eBay's itemId — the same item has different URLs per marketplace
                            unique_id = self._make_id("ebay", ebay_item_id)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = item.get("title", "")
                            price_obj = item.get("price", {}) or {}
                            price = f"{price_obj.get('value', '?')} {price_obj.get('currency', '')}".strip()
                            condition = item.get("condition", "")
                            loc = item.get("itemLocation", {}) or {}
                            location = ", ".join(p for p in [loc.get("city", ""), loc.get("country", "")] if p)
                            description = item.get("shortDescription") or item.get("subtitle") or ""
                            image_url = ((item.get("image") or {}).get("imageUrl")
                                         or ((item.get("thumbnailImages") or [{}])[0]).get("imageUrl", ""))
                            # With contextualLocation=country=RO, shippingOptions are only
                            # returned when the seller ships to Romania.
                            ships_to_ro = bool(item.get("shippingOptions"))

                            if self._is_excluded(title):
                                continue
                            if self._is_excluded_location(location + " " + loc.get("country", "")):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(f"{title} {description}"):
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": f"eBay ({marketplace})",
                                "title": title,
                                "price": price,
                                "location": location,
                                "url": url,
                                "description": description[:300],
                                "condition": condition,
                                "seller": str((item.get("seller") or {}).get("username") or ""),
                                "ships_to_ro": ships_to_ro,
                                "date_posted": str(item.get("itemCreationDate") or "")[:10],
                                "relevance_score": self._relevance_score(title, description),
                                "image_url": image_url,
                            })
                    except Exception as e:
                        log.warning(f"eBay {marketplace} '{kw}' error: {e}")

        log.info(f"eBay Browse API: {len(results)} listings found across {len(MARKETPLACES)} marketplaces")
        return results
