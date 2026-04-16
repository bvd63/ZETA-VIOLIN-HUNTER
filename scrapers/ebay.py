"""
eBay Browse API scraper — OAuth2 client credentials + search endpoint.
Replaces the dead Finding API (decommissioned 2025-02-05).

Setup: EBAY_CLIENT_ID + EBAY_CLIENT_SECRET in env vars.
Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
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

# Major eBay marketplaces — covers US, Europe, UK, Australia, Japan
MARKETPLACES = [
    "EBAY_US",
    "EBAY_GB",
    "EBAY_DE",
    "EBAY_FR",
    "EBAY_IT",
    "EBAY_ES",
    "EBAY_AU",
    "EBAY_CA",
    "EBAY_AT",
    "EBAY_CH",
    "EBAY_NL",
    "EBAY_PL",
    "EBAY_IE",
]

# Focused keywords — specific enough to find Zeta violins, few enough
# to stay within Browse API rate limits (5000 calls/day free tier).
KEYWORDS = [
    "Zeta violin",
    "Zeta electric violin",
    "Zeta Strados",
    "Zeta Jazz Fusion",
    "Zeta JV44",
    "Zeta SV24",
    "Zeta JLP",
    "Zetta violin",
]


class EbayScraper(BaseScraper):
    name = "eBay"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str = ""
        self._token_expires: float = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """Get or refresh OAuth2 application token."""
        now = time.time()
        if self._token and now < self._token_expires:
            return self._token

        if not self.client_id or not self.client_secret:
            log.warning("eBay CLIENT_ID or CLIENT_SECRET not set — skipping.")
            return ""

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        try:
            resp = await client.post(
                OAUTH_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": OAUTH_SCOPE,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                log.error(f"eBay OAuth failed: {resp.status_code} — {resp.text[:300]}")
                return ""

            data = resp.json()
            self._token = data.get("access_token", "")
            expires_in = int(data.get("expires_in", 7200))
            # Refresh 30 minutes early to avoid mid-search expiry
            self._token_expires = now + expires_in - 1800
            log.info(f"eBay OAuth token acquired, expires in {expires_in}s")
            return self._token

        except Exception as e:
            log.error(f"eBay OAuth error: {e}")
            return ""

    async def search(self) -> list:
        if not self.client_id or not self.client_secret:
            log.warning("eBay CLIENT_ID or CLIENT_SECRET not set — skipping eBay.")
            return []

        results = []
        seen_ids = set()

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            token = await self._get_token(client)
            if not token:
                return []

            for marketplace in MARKETPLACES:
                for kw in KEYWORDS:
                    try:
                        resp = await client.get(
                            BROWSE_URL,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                                "X-EBAY-C-ENDUSERCTX": "affiliateCampaignId=<ePNCampaignId>,affiliateReferenceId=<referenceId>",
                            },
                            params={
                                "q": kw,
                                "limit": 50,
                                "filter": "buyingOptions:{FIXED_PRICE|AUCTION}",
                            },
                        )

                        if resp.status_code == 401:
                            # Token expired mid-search — refresh once
                            self._token = ""
                            token = await self._get_token(client)
                            if not token:
                                break
                            continue

                        if resp.status_code == 429:
                            log.warning(f"eBay rate limit hit on {marketplace}")
                            break

                        if resp.status_code != 200:
                            log.warning(
                                f"eBay {marketplace} '{kw}': "
                                f"HTTP {resp.status_code} — {resp.text[:200]}"
                            )
                            continue

                        data = resp.json()
                        items = data.get("itemSummaries", [])

                        for item in items:
                            url = item.get("itemWebUrl", "")
                            if not url:
                                continue

                            unique_id = self._make_id("ebay", url)
                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = item.get("title", "")
                            price_obj = item.get("price", {})
                            price = f"{price_obj.get('value', '?')} {price_obj.get('currency', '')}"
                            condition = item.get("condition", "")
                            location_obj = item.get("itemLocation", {})
                            country = location_obj.get("country", "")
                            postal = location_obj.get("postalCode", "")
                            location = f"{postal}, {country}".strip(", ")

                            # Get description snippet from short description
                            # or subtitle if available
                            description = item.get("shortDescription", "")
                            if not description:
                                description = item.get("subtitle", "")

                            # Apply standard filters
                            full_text = f"{title} {description} {condition}"
                            if self._is_excluded(full_text):
                                continue
                            if self._is_excluded_location(location + " " + country):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(full_text):
                                continue

                            score = self._relevance_score(title, description)
                            if score < 2:
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
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"eBay {marketplace} '{kw}' error: {e}")

        log.info(f"eBay Browse API: {len(results)} listings found across {len(MARKETPLACES)} marketplaces")
        return results
