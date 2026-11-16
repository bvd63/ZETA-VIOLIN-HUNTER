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

import asyncio
import logging
import re
from scrapers.base import BaseScraper, BROWSER_UA
from filters import has_zeta_signal, has_violin_word
from native_candidates import card_hints
from keywords import market_queries

log = logging.getLogger(__name__)

# (label, base url, country, used-collection handles, default condition)
# Guitar Chimp sells ONLY used/vintage gear, so its products default to Used;
# the other two mix new stock (default New) with trade-in collections.
STORES = [
    ("Electric Violin Shop (used)", "https://electricviolinshop.com", "USA", ["used"], "New"),
    ("StringWorks (pre-owned)", "https://www.stringworks.com", "USA", ["outlet-preowned-and-trade-in-deals"], "New"),
    ("Guitar Chimp", "https://guitarchimp.com", "USA", [], "Used"),
]
USED_RX = re.compile(r"\b(used|pre[\s\-]?owned|consign\w*|trade[\s\-]?in|b[\s\-]?stock|vintage|second[\s\-]?hand)\b", re.I)
TAG_RX = re.compile(r"<[^>]+>")

HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/json, */*;q=0.8"}
# Conservative spacing for public storefront abuse-prevention, not an Admin
# API rate limit. All existing collection and keyword searches are retained.
REQUEST_GAP_SECONDS = 1.5


class ShopifyDealersScraper(BaseScraper):
    name = "Violin dealers (Shopify)"

    async def search(self) -> list:
        if not hasattr(self,'failures'):
            self.reset_health()
        results = []
        seen_ids = set()
        self.coverage_stats = {'collections':[], 'private_us_attempts':0}

        async with self.make_client(headers=HEADERS) as client:
            for label, base, country, collections, default_condition in STORES:
                products = []
                last_request = None

                async def fetch(url: str, params: dict):
                    nonlocal last_request
                    loop = asyncio.get_running_loop()
                    if last_request is not None:
                        wait = REQUEST_GAP_SECONDS - (loop.time() - last_request)
                        if wait > 0:
                            await asyncio.sleep(wait)
                    last_request = loop.time()
                    self.record_discovery_query(f'{label} {url} {params!r}')
                    return await client.get(url, params=params)

                for handle in collections:
                    previous_failures = len(self.failures)
                    failed_http = False
                    recovered = False
                    try:
                        resp = await fetch(f"{base}/collections/{handle}/products.json", {"limit": 250})
                        failed_http = resp.status_code != 200
                        payload = resp.json() if resp.status_code==200 else None
                        if not isinstance(payload,dict) or not isinstance(payload.get('products'),list):
                            raise ValueError(f'collection HTTP {resp.status_code} or invalid products schema')
                        rows = payload['products']
                    except Exception as e:
                        # A transport exception has no response to count as OK.
                        failed_http = failed_http or isinstance(e,Exception) and not isinstance(e,ValueError)
                        from shopify_reader import fetch_collection
                        try:
                            def attempted():
                                self.coverage_stats['private_us_attempts'] += 1
                                self.record_discovery_query(f'{label} collection={handle} private US recovery')
                            rows = await fetch_collection(base,handle,client=client,on_attempt=attempted)
                            if rows is None:raise ValueError('private reader not configured')
                            recovered = True
                            if failed_http:
                                del self.failures[previous_failures:]
                                self.requests_recovered += 1
                        except Exception as recovery:
                            self.content_failures.append(f'{label} collection unverified: {type(recovery).__name__}')
                            log.warning('%s collection %r unverified (%s / %s)',label,handle,type(e).__name__,type(recovery).__name__)
                            continue
                    self.coverage_stats['collections'].append({'store':label,'handle':handle,
                        'route':'private US' if recovered else 'native','products':len(rows)})
                    for p in rows:
                        if not isinstance(p,dict):
                            self.content_failures.append(f'{label} invalid product record')
                            continue
                        products.append({**p,'_collection':handle})
                for term in market_queries(broad=True, limit=6):
                    try:
                        resp = await fetch(f"{base}/search/suggest.json", {
                            "q": term, "resources[type]": "product", "resources[limit]": 10,
                        })
                        if resp.status_code == 200:
                            for p in (((resp.json().get("resources") or {}).get("results") or {}).get("products") or []):
                                products.append({**p, "_suggest_queries": [term]})
                        else:
                            log.warning("%s suggest '%s' HTTP %s", label, term, resp.status_code)
                    except Exception as e:
                        log.warning(f"{label} suggest '{term}' error: {e}")
                self.fetched += len(products)

                # A suggestion's query belongs only to that returned product.
                # Merge duplicate IDs before filtering so a sparse suggestion
                # cannot erase the collection's full body or used marker.
                merged = {}
                for p in products:
                    pid = str(p.get("id") or "")
                    if not pid:
                        continue
                    if pid not in merged:
                        merged[pid] = dict(p)
                        continue
                    existing = merged[pid]
                    queries = list(dict.fromkeys(existing.get("_suggest_queries", []) + p.get("_suggest_queries", [])))
                    for key, value in p.items():
                        if key != "_suggest_queries" and value and not existing.get(key):
                            existing[key] = value
                    if queries:
                        existing["_suggest_queries"] = queries

                for p in merged.values():
                    pid = str(p.get("id") or "")
                    title = (p.get("title") or "").strip()
                    handle = p.get("handle") or ""
                    if not pid or not title or not handle:
                        continue
                    tags = p.get("tags") or []
                    tags_text = " ".join(tags) if isinstance(tags, list) else str(tags)
                    # Predictive search supplies `body`; collection JSON uses
                    # `body_html`. Keep the entire description for later filters.
                    body = TAG_RX.sub(" ", p.get("body_html") or p.get("body") or "")
                    body = re.sub(r"\s+", " ", body).strip()
                    queries = p.get("_suggest_queries", [])
                    hints = next((hint for query in queries if (hint := card_hints(title, body, query)) is not None), None)
                    if (hints is None and has_zeta_signal(tags_text)
                            and not has_zeta_signal(f"{title} {body}")
                            and has_violin_word(f"{title} {body}")):
                        hints = {"_native_identity_hint": "product tags",
                                 "_query_identity_unverified": True}
                    if not has_zeta_signal(f"{title} {tags_text} {body}") and hints is None:
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
                    used_hint = f"{title} {tags_text} {p.get('type', '')} {p.get('_collection', '')}"
                    condition = "Used" if USED_RX.search(used_hint) else default_condition
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
                        "description": body,
                        "condition": condition,
                        "seller": label,
                        "date_posted": str(p.get("published_at") or p.get("created_at") or "")[:10],
                        "image_url": image_url,
                        "relevance_score": self._relevance_score(title, body),
                        **(hints or {}),
                        **({"_native_search_queries": queries} if queries else {}),
                    })

        log.info(f"Violin dealers (Shopify): {len(results)} listings found")
        return results
