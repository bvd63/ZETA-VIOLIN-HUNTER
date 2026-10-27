"""Verify purchase evidence, not just that a page returns HTTP 200. No AI/API fee."""

import html
import asyncio
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from filters import is_editorial_url, classify
from liveness import looks_dead, UA, REVERB_ITEM_RX
from source_policy import is_disabled_source

GOVDEALS_RX = re.compile(r"^https://(?:(?:www|prod-seo)\.)?(?:govdeals|allsurplus)\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?asset/(\d+)/(\d+)(?:[/?#]|$)", re.I)
ITEM_PATH = re.compile(r"/(?:item|items|product|products|itm|lot|lots|asset|listing|listings|ad|offer|offers|annuncio|anzeige|jp/auction|auction-lot)/|/l/\d+[-/]|/sale/\d+/\d+/|/lot-[\w-]+|/marketplace/item/|/d/[^/]+/\d+\.html", re.I)
ARCHIVE_PATH = re.compile(r"/(?:price-result|realized-prices|auction-results|cozio-archive)/", re.I)
SALE_WORDS = re.compile(r"\b(?:for sale|wts|selling|current bid|minimum bid|place bid|buy now|buy it now|add to cart|make an offer|auction lot|price|prix|preis|à vendre|zu verkaufen|te koop|in vendita)\b", re.I)
EDITORIAL_TYPES = {"article", "newsarticle", "blogposting", "review", "reportagearticle"}


def is_item_url(url: str) -> bool:
    parts = urlsplit(url)
    from collection_pages import mercatino_item
    if mercatino_item(url):
        return True
    if ((parts.hostname or '').removeprefix('www.') == 'thomann.de'
            and re.fullmatch(r'/[a-z]{2}/classified_show\.html', parts.path)
            and re.fullmatch(r'\d+', parse_qs(parts.query).get('id', [''])[0])):
        return True
    if ITEM_PATH.search(parts.path):
        return True
    # These short paths are only item evidence on their own marketplace host.
    # Do not let a generic /p/ or /d/ page on another site become an offer.
    if parts.hostname in ("willhaben.at", "www.willhaben.at"):
        return bool(re.fullmatch(r"/iad/kaufen-und-verkaufen/d/[^/]+-\d+/?", parts.path))
    if parts.hostname in ("gumtree.com", "www.gumtree.com"):
        return bool(re.fullmatch(r"/p/(?:[^/]+/)+\d+/?", parts.path))
    return False


def public_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port not in (None, 80, 443):
            return False
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.username or parts.password or not host:
        return False
    if host in ("localhost", "metadata.google.internal") or host.endswith((".internal", ".local")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


async def public_dns(url: str) -> bool:
    """Reject local destinations behind hostnames, including redirects."""
    try:
        parts = urlsplit(url)
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM), 3)
        return bool(addresses) and all(ipaddress.ip_address(row[4][0]).is_global for row in addresses)
    except (OSError, ValueError, asyncio.TimeoutError):
        return False


def _nodes(value):
    if isinstance(value, list):
        for entry in value:
            yield from _nodes(entry)
    elif isinstance(value, dict):
        yield value
        yield from _nodes(value.get("@graph", []))


def gardiner_sale_date(soup):
    for text in soup.find_all(string=re.compile(r'Sale Date:', re.I)):
        if text.parent.name in ('script', 'style') or any(p.name in ('aside', 'nav', 'footer') for p in text.parents):
            continue
        match = re.search(r'Sale Date:\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})', str(text), re.I)
        if match:
            try:
                return datetime.strptime(' '.join(match.groups()), '%d %B %Y').date()
            except ValueError:
                pass
    return None


def primary_closed_reason(soup, host: str) -> str:
    """Marketplace-specific primary status, never generic 'sold' in a sidebar."""
    if host == 'invaluable.com' or host.endswith('.invaluable.com'):
        if soup.select_one('.closed-bid-lot-detail') or (soup.title and
                soup.title.get_text(' ',strip=True).casefold().startswith('sold at auction:')):
            return 'Invaluable: lot încheiat'
    if host == 'auctions.gardinerhoulgate.co.uk':
        for status in soup.select('span.h4.text-warning, strong'):
            if status.get_text(' ', strip=True).casefold() == 'closed':
                return 'Gardiner Houlgate: lot încheiat'
    if host.removeprefix('www.') == 'musicalinstrument-auctions.co.uk':
        date = gardiner_sale_date(soup)
        if date and date < datetime.now(ZoneInfo('Europe/London')).date():
            return 'Gardiner Houlgate: data licitației a trecut'
    if host == 'ha.com' or host.endswith('.ha.com'):
        for node in soup.find_all(string=re.compile(r'^\s*Sold on [A-Za-z]+ \d{1,2}, \d{4} for\s*:', re.I)):
            if node.parent.name not in ('script', 'style'):
                return 'Heritage: lot vândut, cu dată explicită'
    if host in ('8notes.com', 'www.8notes.com'):
        posts = soup.select('div.mmessage')
        if posts:
            original = posts[0].get_text('\n', strip=True).splitlines()
            signature = original[-1].strip() if original else ''
            if re.fullmatch(r'[A-Za-z][A-Za-z .-]{1,35}', signature):
                closed_index = -1
                for index, post in enumerate(posts[1:], 1):
                    lines = post.get_text('\n', strip=True).splitlines()
                    if lines and lines[-1].strip() == signature and re.search(
                        r'\b(?:just sold them both|the Zeta.{0,10}have (?:long )?been sold)\b',
                        ' '.join(lines[:-1]), re.I):
                        closed_index = index
                # A later explicit new offer can reopen the thread. Merely
                # being old/archived or discussing another sale never closes it.
                if closed_index >= 0 and not any(re.search(r'\b(?:for sale|I am selling|I have .{0,50}to sell)\b',
                        post.get_text(' ', strip=True), re.I) for post in posts[closed_index+1:]):
                    return 'forum: autorul anunțului confirmă vânzarea'
    return ''


def inspect_page(listing: dict, text: str, status: int, final_url: str) -> tuple:
    """(state, reason). live/dead/non_sale/unknown; enrich only listing content."""
    reason = looks_dead(status, listing["url"], final_url, text)
    if reason:
        return "dead", reason
    if status >= 400:
        return "unknown", f"HTTP {status}; disponibilitatea nu poate fi verificată"
    if status not in (200, 203):
        return 'unknown', f'HTTP {status}; pagina ofertei nu poate fi verificată'
    if is_editorial_url(final_url):
        return "non_sale", "pagină editorială"
    if ARCHIVE_PATH.search(urlsplit(final_url).path):
        return "dead", "arhivă cu licitații încheiate"
    soup = BeautifulSoup(text[:500000], "lxml")
    page_title = soup.title.get_text(' ',strip=True).casefold() if soup.title else ''
    if (page_title in ('just a moment...', 'just a moment', 'access denied', 'security check',
                       'facebook – log in or sign up', 'facebook - log in or sign up', 'log in to facebook')
            or soup.select_one('[class*="gv-Skeleton"], #challenge-running, #cf-challenge-running')):
        return 'unknown', 'pagină de acces / autentificare; oferta nu poate fi verificată'
    item_page = is_item_url(final_url) or bool(listing.get('_direct_item_candidate'))
    from collection_pages import collection_leads
    leads = collection_leads(soup, final_url, item_page)
    if leads is not None:
        listing['_collection_leads'] = leads
        return 'collection', 'pagină de rezultate; ofertele individuale necesită verificare'
    host = (urlsplit(final_url).hostname or "").lower()
    closed = primary_closed_reason(soup, host)
    if closed:
        return 'dead', closed
    if host == "craigslist.org" or host.endswith(".craigslist.org"):
        # Estate/garage events can end while the posting still returns HTTP 200.
        from scrapers.craigslist import CraigslistScraper, SALE_EVENT_RX
        if SALE_EVENT_RX.search(str(listing.get("title", ""))):
            dates = CraigslistScraper._sale_dates(soup)
            if not dates:
                return "unknown", "Craigslist: data vânzării nu poate fi verificată"
            if max(dates) < CraigslistScraper._local_today(soup):
                return "dead", "Craigslist: vânzare locală încheiată"
            body = soup.select_one("#postingbody")
            if body:
                listing["description"] = body.get_text(" \n", strip=True)
                listing["estate_sale"] = True
                listing["sale_end"] = max(dates).isoformat()
                return "live", "Craigslist: inventar și dată de vânzare verificate"
    from product_metadata import primary_microdata, same_product_url
    products, editorial = [], False
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            for node in _nodes(json.loads(tag.string or tag.get_text())):
                types = node.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                types = {str(t).lower() for t in types}
                editorial |= bool(types & EDITORIAL_TYPES)
                if "product" in types:
                    products.append(node)
        except (ValueError, TypeError):
            continue
    products.extend(primary_microdata(soup, final_url))
    # Ignore footer/recommendations, scripts and navigation when checking sale intent.
    main = soup.find("main") or soup.find(id="main-content") or soup.find(id="postingbody") or soup
    h1 = main.find("h1")
    if listing.get("source") == "search":
        listing.setdefault("_discovery_description", listing.get("description", ""))
        listing["description"] = ""
    if h1 and h1.get_text(strip=True):
        listing["title"] = h1.get_text(" ", strip=True)[:300]
    for product in products:
        # Only the primary instrument, not a recommended item in a side bar.
        name = str(product.get("name") or "")
        product_url = str(product.get("url") or "")
        if product_url and not same_product_url(product_url, final_url):
            continue
        if name and not (h1 and name.casefold() in listing["title"].casefold()) and len(products) > 1:
            continue
        offers = product.get("offers") or []
        if isinstance(offers, dict):
            offers = [offers]
        for offer in offers:
            availability = str(offer.get("availability", "")).rsplit("/", 1)[-1].lower()
            if availability in ("soldout", "outofstock", "discontinued"):
                return "dead", f"primary offer {availability}"
            if name:
                listing["title"] = html.unescape(name)[:300]
            if product.get("description"):
                listing["description"] = BeautifulSoup(str(product["description"]), "lxml").get_text(" ", strip=True)
            brand = product.get("brand") or ""
            if isinstance(brand, dict):
                brand = brand.get("name") or ""
            listing["brand"] = brand if isinstance(brand, str) else ""
            listing["model"] = product.get("model") if isinstance(product.get("model"), str) else ""
            category = product.get("category") or []
            listing["categories"] = [category] if isinstance(category, str) else category
            listing["_primary_content_checked"] = True
            if offer.get("price") is not None:
                listing["price"] = f"{offer['price']} {offer.get('priceCurrency', '')}".strip()
            if offer.get("price") is not None or availability in ("instock", "limitedavailability", "preorder"):
                if editorial:
                    return "non_sale", "articol cu linkuri către produse"
                return "live", "instrument cu ofertă structurată"
    if editorial:
        return "non_sale", "articol / recenzie, fără ofertă proprie"
    for tag in main.find_all(["script", "style", "nav", "footer"]):
        tag.decompose()
    scoped = BeautifulSoup(str(main), "lxml")
    for related in scoped.select('aside, [class*="recommend"], [id*="recommend"], [class*="related"], [id*="related"]'):
        related.decompose()
    if host in ('offerup.com', 'www.offerup.com') and any(
            node.get_text(' ', strip=True) == 'SOLD' for node in scoped.find_all(['span','div','strong','p'])
            if not node.find(['span','div','strong','p'])):
        return 'dead', 'OfferUp: anunț marcat SOLD'
    body = scoped.get_text(" ", strip=True)
    # Read the item body, not just the search-engine snippet. Never borrow
    # footer/recommended-product text as identity evidence.
    description = scoped.select_one('[itemprop="description"], #description, #postingbody')
    if description:
        listing["description"] = description.get_text(" \n", strip=True)
        listing["_primary_content_checked"] = True
    elif (main is not soup or h1 is not None) and item_page:
        listing["description"] = scoped.get_text(" \n", strip=True)
        listing["_primary_content_checked"] = True
    if item_page and re.search(
            r"\b(?:auction ended|auction closed|sold for\s*:|sold amount\s*:|closed\s+auction date\s*:)", body, re.I):
        return "dead", "rezultat de licitație încheiată"
    if re.search(r"\b(?:closed|auction ended|sold amount)\s*[:(]", body, re.I) and listing.get("auction"):
        return "dead", "licitație încheiată"
    if host.removeprefix('www.') == 'musicalinstrument-auctions.co.uk' and item_page:
        date = gardiner_sale_date(scoped)
        if date and date >= datetime.now(ZoneInfo('Europe/London')).date() and re.search(r'Estimate:\s*£\s*\d',body,re.I):
            listing['auction'], listing['auction_sale_date'] = True, date.isoformat()
            return 'live', 'Gardiner Houlgate: lot publicat într-o licitație viitoare; ora închiderii neconfirmată'
    if SALE_WORDS.search(body) or (host.removeprefix('www.') == 'thomann.de' and item_page
                                  and re.search(r'\bBiete\b', body)):
        # Dedicated item URLs or classifieds sales text + monetary amount.
        if item_page or re.search(r"[€$£¥]\s*\d|\d\s*(?:USD|EUR|GBP|JPY)\b", body):
            return "live", "pagină de vânzare / licitație"
    if item_page:
        return "unknown", "pagină de anunț; ofertă neverificată"
    return "non_sale", "nu am găsit o ofertă de vânzare"


async def verify_offer(listing: dict, client) -> tuple:
    listing.pop("_primary_content_checked", None)
    url = listing.get("url", "")
    if (urlsplit(url).hostname or '').lower() == 'auctions.gardinerhoulgate.co.uk':
        from database import canonical_url
        url = listing['url'] = canonical_url(url)
    if is_disabled_source(url):
        return "non_sale", "sursă eliminată la cererea proprietarului"
    if not public_url(url):
        return "non_sale", "URL public invalid"
    from facebook_discovery import cached_offer
    facebook = cached_offer(url)
    if facebook and facebook['checked'] >= time.time() - 6 * 3600:
        verified = facebook['listing']
        if verified.get('apify_is_sold') is True:
            return 'dead', 'Facebook: vândut la verificarea detaliilor'
        from scrapers.apify_facebook import available
        if available(verified):
            origin = listing.get('source')
            listing.update(verified)
            if origin:
                listing['source'] = origin
            listing['_primary_content_checked'] = True
            return 'live', 'Facebook: detalii verificate prin Apify'
    if is_editorial_url(url):
        return "non_sale", "pagină editorială"
    if ARCHIVE_PATH.search(urlsplit(url).path):
        return "dead", "arhivă cu licitații încheiate"
    if not await public_dns(url):
        return "unknown", "destinația publică nu poate fi verificată"
    gov = GOVDEALS_RX.search(url)
    if gov:
        from scrapers.govdeals import GovDealsClient, parse_asset, asset_closed_reason
        try:
            is_surplus = (urlsplit(url).hostname or "").endswith("allsurplus.com")
            api = GovDealsClient(client, "AD" if is_surplus else "GD", 2 if is_surplus else 1)
            await api.initialize()
            data = await api.asset(*gov.groups())
            verified = parse_asset(data, platform="AllSurplus" if is_surplus else "GovDeals",
                                   host="www.allsurplus.com" if is_surplus else "www.govdeals.com")
            if not verified:
                closed = asset_closed_reason(data)
                return ("dead", "GovDeals: " + closed) if closed else (
                    "unknown", "GovDeals: date primare incomplete; necesită reverificare")
            listing.update({k: v for k, v in verified.items() if k not in ("id", "source")})
            listing["_primary_content_checked"] = True
            return "live", "GovDeals: licitație activă verificată prin API"
        except Exception:
            return "unknown", "GovDeals: API indisponibil"
    reverb = REVERB_ITEM_RX.search(url)
    host = (urlsplit(url).hostname or "").lower()
    if reverb and (host == "reverb.com" or host.endswith(".reverb.com")):
        try:
            response = await client.get(f"https://api.reverb.com/api/listings/{reverb.group(1)}",
                headers={"User-Agent": UA, "Accept": "application/hal+json", "Accept-Version": "3.0"})
            if response.status_code == 404:
                return "dead", "reverb: not found"
            if response.status_code == 200:
                data = response.json()
                state = (data.get("state") or {}).get("slug")
                if state and state != "live":
                    return "dead", f"reverb state={state}"
                if state == "live":
                    from scrapers.reverb import identity_fields
                    listing["title"] = data.get("title") or listing.get("title", "")
                    listing["description"] = BeautifulSoup(data.get("description") or "", "lxml").get_text(" ", strip=True)
                    listing.update(identity_fields(data))
                    listing["_primary_content_checked"] = True
                    listing["condition"] = (data.get("condition") or {}).get("display_name", "")
                    price = data.get("price") or {}
                    if price.get("amount") is not None:
                        listing["price"] = f"{price['amount']} {price.get('currency', '')}"
                    return "live", "Reverb: anunț activ verificat prin API"
        except Exception:
            pass
    try:
        current = url
        for _ in range(5):
            if is_disabled_source(current):
                return "non_sale", "redirecționare către o sursă eliminată"
            if not public_url(current):
                return "non_sale", "redirect către URL nepublic"
            if current != url and not await public_dns(current):
                return "non_sale", "redirect către destinație nepublică sau neverificată"
            response = await client.get(current, follow_redirects=False, headers={"User-Agent": UA})
            if response.is_redirect and response.headers.get("location"):
                from urllib.parse import urljoin
                current = urljoin(current, response.headers["location"])
                continue
            state, reason = inspect_page(listing, response.text, response.status_code, current)
            if state == 'unknown' and (urlsplit(current).hostname or '').removeprefix('www.') == 'musicalinstrument-auctions.co.uk':
                try:
                    from browser_fetch import PublicSearchBrowser
                    async with PublicSearchBrowser() as browser:
                        code, rendered = await browser.fetch(current)
                    state, reason = inspect_page(listing, rendered, code, current)
                    reason = 'Public Chromium: ' + reason
                except Exception:
                    pass  # failed access remains unknown, never proof of sold
            if state == 'unknown':
                from primary_page_reserve import fresh_primary_page
                recovered = await fresh_primary_page(current, client)
                if recovered:
                    state, reason = inspect_page(listing, recovered, 200, current)
                    reason = 'Exa live: ' + reason
            return state, reason
        return "unknown", "prea multe redirecționări"
    except Exception:
        return "unknown", "pagina nu a putut fi accesată"
