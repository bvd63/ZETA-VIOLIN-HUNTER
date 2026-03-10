"""
eBay scraper — uses eBay Finding API (free, covers all eBay country sites).
Register at: https://developer.ebay.com (free)
"""

import httpx
import logging
import xml.etree.ElementTree as ET
from scrapers.base import BaseScraper
from config import Config

log = logging.getLogger(__name__)

EBAY_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

GLOBAL_IDS = [
    "EBAY-US", "EBAY-GB", "EBAY-DE", "EBAY-AU", "EBAY-AT",
    "EBAY-BE-FR", "EBAY-BE-NL", "EBAY-CA", "EBAY-FR", "EBAY-HK",
    "EBAY-IT", "EBAY-MOTOR", "EBAY-NL", "EBAY-PL", "EBAY-SG",
    "EBAY-ES", "EBAY-CH", "EBAY-IE",
]

NS = "http://www.ebay.com/marketplace/search/v1/services"


class EbayScraper(BaseScraper):
    name = "eBay"

    def __init__(self, app_id: str):
        self.app_id = app_id

    async def search(self) -> list:
        if not self.app_id:
            log.warning("eBay APP_ID not set — skipping eBay search.")
            return []

        results = []
        seen_ids = set()

        keywords = [
            "Zeta violin", "Zeta electric violin", "Zeta Strados",
            "Zeta Jazz Fusion", "Zeta JV44", "Zeta SV24", "Zeta EV44",
            "Zetta violin", "Zeta Geige", "violino elettrico Zeta",
        ]

        async with httpx.AsyncClient(timeout=20) as client:
            for global_id in GLOBAL_IDS:
                for kw in keywords:
                    try:
                        params = {
                            "OPERATION-NAME": "findItemsByKeywords",
                            "SERVICE-VERSION": "1.0.0",
                            "SECURITY-APPNAME": self.app_id,
                            "RESPONSE-DATA-FORMAT": "XML",
                            "REST-PAYLOAD": "",
                            "keywords": kw,
                            "paginationInput.entriesPerPage": "50",
                            "itemFilter(0).name": "ListingStatus",
                            "itemFilter(0).value": "Active",
                            "GLOBAL-ID": global_id,
                        }
                        resp = await client.get(EBAY_API_URL, params=params)
                        if resp.status_code != 200:
                            continue

                        root = ET.fromstring(resp.text)
                        items = root.findall(f".//{{{NS}}}item")

                        for item in items:
                            def g(tag):
                                el = item.find(f".//{{{NS}}}{tag}")
                                return el.text if el is not None else ""

                            item_id = g("itemId")
                            url = g("viewItemURL")
                            unique_id = self._make_id("ebay", url or item_id)

                            if unique_id in seen_ids:
                                continue
                            seen_ids.add(unique_id)

                            title = g("title")
                            price = f"{g('currentPrice')} {g('currencyId')}"
                            location = g("location")
                            country = g("country")
                            end_time = g("endTime")
                            condition = g("conditionDisplayName")

                            if self._is_excluded(title):
                                continue
                            if self._is_excluded_location(location + " " + country):
                                continue
                            if not self._price_in_range(price):
                                continue
                            if not self._year_in_range(title):
                                continue

                            score = self._relevance_score(title)
                            if score < 2:
                                continue

                            results.append({
                                "id": unique_id,
                                "platform": f"eBay ({global_id})",
                                "title": title,
                                "price": price,
                                "location": f"{location}, {country}",
                                "url": url,
                                "description": f"Condition: {condition}",
                                "date_posted": end_time[:10] if end_time else "",
                                "relevance_score": score,
                            })

                    except Exception as e:
                        log.warning(f"eBay {global_id} '{kw}' error: {e}")

        return results
