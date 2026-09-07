"""
Shopify-based violin dealers with second-hand / consignment sections.

Shopify stores expose public JSON without auth:
  /collections/{handle}/products.json?limit=250   — a whole collection
  /search/suggest.json?q=zeta&resources[type]=product — store-wide search
Verified 2026-09-07 for electricviolinshop.com, stringworks.com, guitarchimp.com.

Products are treated as NEW unless the title, tags or collection say
used / pre-owned / consignment, so CONDITION=used drops the dealer's new stock
and keeps their trade-ins.
"""

import logging
import re
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal

log = logging.getLogger(__name__)

# (label, base url, country, used-collection handles)
STORES = [
    ("Electric Violin Shop (used)", "https://electricviolinshop.com", "USA", ["used"]),
    ("StringWorks (pre-owned)", "https://www.stringworks.com", "USA", ["outlet-preowned-and-trade-in-deals"]),
    ("Guitar Chimp", "https://guitarchimp.com", "USA", []),
]
SEARCH_TERMS = ["zeta", "strados"]
USED_RX = re.compile(r"\b(used|pre[\s\-]?owned|consign\w*|trade[\s\-]?in|b[\s\-]?stock|vintage|second[\s\-]?hand)\b", re.I)
TAG_RX = re.compile(r"<[^>]+>")

HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json, */*;q=0.8"}


class ShopifyDealersScraper(BaseScraper):
    name = "Violin dealers (Shopify)"

    async def search(self) -> list:
        results = []
        seen_ids = set()

        async with self.make_client(headers=HEADERS) as client:
            for label, base, country, collections in STORES:
                products = []
                for handle in collections:
                    try:
                        resp = await client.get(f"{base}/collections/{handle}/products.json", params={"limit": 250})
                        if resp.status_code == 200:
                            for p in resp.json().get("products", []) or []:
                                p["_collection"] = handle
                                products.append(p)
                        else:
                            log.warning(f"{label} collection '{handle}' HTTP {resp.status_code}")
                    except Exception as e:
                        log.warning(f"{label} collection '{handle}' error: {e}")
                for term in SEARCH_TERMS:
                    try:
                        resp = await client.get(f"{base}/search/suggest.json", params={
                            "q": term, "resources[type]": "product", "resources[limit]": 10,
                        })
                        if resp.status_code == 200:
                            products.extend(((resp.json().get("resources") or {}).get("results") or {}).get("products") or [])
                    except Exception as e:
                        log.warning(f"{label} suggest '{term}' error: {e}")
                self.fetched += len(products)

                for p in products:
                    pid = str(p.get("id") or "")
                    title = (p.get("title") or "").strip()
                    handle = p.get("handle") or ""
                    if not pid or not title or not handle:
                        continue
                    tags = p.get("tags") or []
                    tags_text = " ".join(tags) if isinstance(tags, list) else str(tags)
                    body = TAG_RX.sub(" ", p.get("body_html") or "")
                    body = re.sub(r"\s+", " ", body).strip()
                    if not has_zeta_signal(f"{title} {tags_text} {body}"):
                        continue
                    unique_id = self._make_id("shopify", f"{base}:{pid}")
                    if unique_id in seen_ids:
                        continue
                    seen_ids.add(unique_id)

                    variants = p.get("variants") or []
                    if variants and not any(v.get("available", True) for v in variants):
                        continue  # sold
                    if p.get("available") is False:
                        continue
                    price = ""
                    if variants and variants[0].get("price"):
                        price = f"{variants[0]['price']} USD"
                    elif p.get("price"):
                        price = f"{p['price']} USD"
                    used_hint = f"{title} {tags_text} {p.get('_collection', '')}"
                    condition = "Used" if USED_RX.search(used_hint) else "New"
                    images = p.get("images") or []
                    image_url = ""
                    if images and isinstance(images[0], dict):
                        image_url = images[0].get("src", "")
                    elif p.get("featured_image"):
                        fi = p["featured_image"]
                        image_url = fi if isinstance(fi, str) else ""
                    if image_url.startswith("//"):
                        image_url = "https:" + image_url

                    if self._is_excluded(title):
                        continue
                    if price and not self._price_in_range(price):
                        continue

                    results.append({
                        "id": unique_id,
                        "platform": label,
                        "title": title,
                        "price": price or "N/A",
                        "location": country,
                        "url": f"{base}/products/{handle}",
                        "description": body[:300],
                        "condition": condition,
                        "seller": label,
                        "date_posted": str(p.get("published_at") or p.get("created_at") or "")[:10],
                        "image_url": image_url,
                        "relevance_score": self._relevance_score(title, body),
                    })

        log.info(f"Violin dealers (Shopify): {len(results)} listings found")
        return results
