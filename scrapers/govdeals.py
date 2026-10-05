"""GovDeals public auction search, using the anonymous website API (no paid API)."""

import html
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from filters import has_zeta_signal, has_violin_word
from keywords import market_queries
from scrapers.base import BaseScraper, BROWSER_HEADERS

log = logging.getLogger(__name__)
HOME = "https://www.govdeals.com/"
API = "https://maestro.lqdt1.com"


def auction_end(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


class GovDealsClient:
    """Only public search/detail routes; key read from public JS, never committed."""
    def __init__(self, client, business_id: str = "GD", site_id: int = 1):
        self.client = client
        self.headers = {}
        self.business_id = business_id
        self.site_id = site_id

    async def initialize(self) -> None:
        response = await self.client.get(HOME)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        script = soup.find("script", src=re.compile(r"(?:^|/)main\.[^/]+\.js$"))
        if not script:
            raise ValueError("GovDeals public frontend script not found")
        js = await self.client.get(urljoin(HOME, script["src"]))
        js.raise_for_status()
        key = re.search(r'maestroApiKey\s*:\s*"([^"\s]+)"', js.text)
        if not key:
            raise ValueError("GovDeals anonymous frontend configuration changed")
        self.headers = {"x-api-key": key.group(1), "x-user-id": "-1",
                        "x-api-correlation-id": str(uuid4())}

    async def search(self, query: str, page: int = 1) -> list:
        response = await self.client.post(f"{API}/search/list", headers=self.headers, json={
            "businessId": self.business_id, "siteId": self.site_id, "searchText": query, "page": page,
            "displayRows": 100, "sortField": "latestonline", "sortOrder": "desc",
            "requestType": "search", "responseStyle": "", "facets": [], "facetsFilter": [],
            "timeType": "atauction", "isQAL": False, "accountIds": [], "categoryIds": "",
        })
        response.raise_for_status()
        data = response.json()
        if data.get("isAPIFailureActive"):
            raise ValueError("GovDeals reports a search failure")
        if not isinstance(data.get("assetSearchResults"), list):
            raise ValueError("GovDeals search response changed")
        return data["assetSearchResults"]

    async def asset(self, asset_id: str, account_id: str) -> dict:
        response = await self.client.post(f"{API}/assets/{asset_id}/{account_id}/false",
            headers=self.headers, json={"businessId": self.business_id, "siteId": self.site_id})
        response.raise_for_status()
        return response.json()


def parse_asset(asset: dict, row: dict = None, *, now: datetime = None,
                platform: str = "GovDeals", host: str = "www.govdeals.com") -> dict:
    row = row or {}
    title = html.unescape(asset.get("assetShortDesc") or row.get("assetShortDescription") or "")
    end = auction_end(row.get("assetAuctionEndDateUtc") or asset.get("assetAuctionEndDate") or "")
    if not end or datetime.fromisoformat(end) <= (now or datetime.now(timezone.utc)):
        return {}
    if str(asset.get("assetStatusCd", "")).upper() in ("SOA", "SOLD", "CLO", "CLOSED", "CAN"):
        return {}
    asset_id = asset.get("assetId") or row.get("assetId")
    account_id = asset.get("accountId") or row.get("accountId")
    if not asset_id or not account_id or not title:
        return {}
    description = BeautifulSoup(asset.get("assetLongDesc") or "", "lxml").get_text(" ", strip=True)
    currency = asset.get("currencyCode") or row.get("currencyCode") or "USD"
    bid = row.get("currentBid")
    photos = asset.get("assetPhotos") or []
    photo = urljoin("https://files.lqdt1.com", photos[0]) if photos and isinstance(photos[0], str) else ""
    return {
        "id": BaseScraper()._make_id(platform.lower(), f"{account_id}/{asset_id}/{asset.get('auctionId', '')}"),
        "platform": platform, "title": title,
        "price": f"{bid} {currency} (current bid)" if bid is not None else "See listing",
        "url": f"https://{host}/en/asset/{asset_id}/{account_id}",
        "location": ", ".join(str(asset.get(k) or row.get(k) or "") for k in ("city", "state", "country")).strip(", "),
        "description": description[:1200], "condition": "Brand New" if asset.get("assetConditionCd") in ("NW", "NEW") else "Used/See Description",
        "seller": asset.get("companyName", ""), "auction": True, "auction_end": end,
        "pickup_only": asset.get("willShip") is False, "ships_to_ro": None,
        "image_url": photo, "relevance_score": BaseScraper()._relevance_score(title, description),
        "mixed_lot": bool(re.search(r"\bviolas?\b", title, re.I)),
    }


class GovDealsScraper(BaseScraper):
    name = "GovDeals"
    business_id = "GD"
    site_id = 1
    host = "www.govdeals.com"

    async def search(self) -> list:
        results, seen = [], set()
        enriched = 0
        async with self.make_client(headers=BROWSER_HEADERS) as http:
            api = GovDealsClient(http, self.business_id, self.site_id)
            await api.initialize()
            for query in market_queries(broad=True, limit=6, extra=("electric violin",)):
                for page in (1, 2):
                    rows = await api.search(query, page)
                    self.fetched += len(rows)
                    for row in rows:
                        identity = (row.get("accountId"), row.get("assetId"))
                        if None in identity or identity in seen:
                            continue
                        seen.add(identity)
                        title = row.get("assetShortDescription", "")
                        if not (has_zeta_signal(title) or has_violin_word(title) or query in ("zeta", "strados")):
                            continue
                        if enriched >= 30:
                            log.warning("GovDeals detail budget reached; remaining candidates deferred")
                            break
                        enriched += 1
                        detail = await api.asset(str(identity[1]), str(identity[0]))
                        listing = parse_asset(detail, row, platform=self.name, host=self.host)
                        if listing and has_zeta_signal(listing["title"] + " " + listing["description"]):
                            results.append(listing)
                    if len(rows) < 100 or enriched >= 30:
                        break
        return results


class AllSurplusScraper(GovDealsScraper):
    name = "AllSurplus"
    business_id = "AD"
    site_id = 2
    host = "www.allsurplus.com"
