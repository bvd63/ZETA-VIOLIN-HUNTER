"""Verify purchase evidence, not just that a page returns HTTP 200. No AI/API fee."""

import html
import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from filters import is_editorial_url, classify
from liveness import looks_dead, UA, REVERB_ITEM_RX

GOVDEALS_RX = re.compile(r"^https://(?:www\.)?(?:govdeals|allsurplus)\.com/(?:en/)?asset/(\d+)/(\d+)(?:[/?#]|$)", re.I)
ITEM_PATH = re.compile(r"/(?:item|items|itm|lot|lots|asset|listing|listings|ad|offer|offers|annuncio|anzeige|jp/auction|auction-lot)/|/l/\d+[-/]|/sale/\d+/\d+/|/lot-[\w-]+|/marketplace/item/|/d/[^/]+/\d+\.html", re.I)
ARCHIVE_PATH = re.compile(r"/(?:price-result|realized-prices|auction-results|cozio-archive)/", re.I)
SALE_WORDS = re.compile(r"\b(?:for sale|wts|selling|current bid|minimum bid|place bid|buy now|buy it now|add to cart|make an offer|auction lot|price|prix|preis|à vendre|zu verkaufen|te koop|in vendita)\b", re.I)
EDITORIAL_TYPES = {"article", "newsarticle", "blogposting", "review", "reportagearticle"}


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


def inspect_page(listing: dict, text: str, status: int, final_url: str) -> tuple:
    """(state, reason). live/dead/non_sale/unknown; enrich only listing content."""
    reason = looks_dead(status, listing["url"], final_url, text)
    if reason:
        return "dead", reason
    if status >= 400:
        return "unknown", f"HTTP {status}; disponibilitatea nu poate fi verificată"
    if is_editorial_url(final_url):
        return "non_sale", "pagină editorială"
    if ARCHIVE_PATH.search(urlsplit(final_url).path):
        return "dead", "arhivă cu licitații încheiate"
    soup = BeautifulSoup(text[:500000], "lxml")
    host = (urlsplit(final_url).hostname or "").lower()
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
    # Ignore footer/recommendations, scripts and navigation when checking sale intent.
    main = soup.find("main") or soup.find(id="main-content") or soup.find(id="postingbody") or soup
    h1 = main.find("h1")
    if h1 and h1.get_text(strip=True):
        listing["title"] = h1.get_text(" ", strip=True)[:300]
    for product in products:
        # Only the primary instrument, not a recommended item in a side bar.
        name = str(product.get("name") or "")
        product_url = str(product.get("url") or "")
        if product_url and urlsplit(product_url).path.rstrip("/") != urlsplit(final_url).path.rstrip("/"):
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
                listing["description"] = BeautifulSoup(str(product["description"]), "lxml").get_text(" ", strip=True)[:1200]
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
    body = main.get_text(" ", strip=True)
    if ITEM_PATH.search(urlsplit(final_url).path) and re.search(
            r"\b(?:auction ended|auction closed|sold for\s*:|sold amount\s*:|closed\s+auction date\s*:)", body, re.I):
        return "dead", "rezultat de licitație încheiată"
    if re.search(r"\b(?:closed|auction ended|sold amount)\s*[:(]", body, re.I) and listing.get("auction"):
        return "dead", "licitație încheiată"
    if SALE_WORDS.search(body):
        # Dedicated item URLs or classifieds sales text + monetary amount.
        if ITEM_PATH.search(urlsplit(final_url).path) or re.search(r"[€$£¥]\s*\d|\d\s*(?:USD|EUR|GBP|JPY)\b", body):
            return "live", "pagină de vânzare / licitație"
    if ITEM_PATH.search(urlsplit(final_url).path):
        return "unknown", "pagină de anunț; ofertă neverificată"
    return "non_sale", "nu am găsit o ofertă de vânzare"


async def verify_offer(listing: dict, client) -> tuple:
    url = listing.get("url", "")
    if not public_url(url):
        return "non_sale", "URL public invalid"
    if is_editorial_url(url):
        return "non_sale", "pagină editorială"
    if ARCHIVE_PATH.search(urlsplit(url).path):
        return "dead", "arhivă cu licitații încheiate"
    if not await public_dns(url):
        return "unknown", "destinația publică nu poate fi verificată"
    gov = GOVDEALS_RX.search(url)
    if gov:
        from scrapers.govdeals import GovDealsClient, parse_asset
        try:
            is_surplus = (urlsplit(url).hostname or "").endswith("allsurplus.com")
            api = GovDealsClient(client, "AD" if is_surplus else "GD", 2 if is_surplus else 1)
            await api.initialize()
            data = await api.asset(*gov.groups())
            verified = parse_asset(data, platform="AllSurplus" if is_surplus else "GovDeals",
                                   host="www.allsurplus.com" if is_surplus else "www.govdeals.com")
            if not verified:
                return "dead", "GovDeals: licitație încheiată sau indisponibilă"
            listing.update({k: v for k, v in verified.items() if k not in ("id", "source")})
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
                    listing["title"] = data.get("title") or listing.get("title", "")
                    listing["description"] = BeautifulSoup(data.get("description") or "", "lxml").get_text(" ", strip=True)[:1200]
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
            if not public_url(current):
                return "non_sale", "redirect către URL nepublic"
            if current != url and not await public_dns(current):
                return "non_sale", "redirect către destinație nepublică sau neverificată"
            response = await client.get(current, follow_redirects=False, headers={"User-Agent": UA})
            if response.is_redirect and response.headers.get("location"):
                from urllib.parse import urljoin
                current = urljoin(current, response.headers["location"])
                continue
            return inspect_page(listing, response.text, response.status_code, current)
        return "unknown", "prea multe redirecționări"
    except Exception:
        return "unknown", "pagina nu a putut fi accesată"
