"""Verify purchase evidence, not just that a page returns HTTP 200. No AI/API fee."""

import html
import asyncio
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlsplit, parse_qs, urljoin
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


def _reverb_item_id(url: str) -> str:
    if not isinstance(url, str):
        return ''
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http','https') or parts.username or parts.password
                or parts.port not in (None,80,443)):
            return ''
    except ValueError:
        return ''
    if (parts.hostname or '').lower() not in ('reverb.com', 'www.reverb.com'):
        return ''
    match = re.match(r'^/(?:[a-z]{2}(?:-[a-z]{2})?/)?item/(\d+)(?:[-/]|$)', parts.path, re.I)
    return match[1] if match else ''


def _reverb_item(url: str) -> bool:
    return bool(_reverb_item_id(url))


def _offerup_item_id(url: str) -> str:
    if not isinstance(url, str):
        return ''
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or parts.username or parts.password
                or parts.port not in (None, 80, 443)
                or (parts.hostname or '').lower() not in ('offerup.com', 'www.offerup.com')):
            return ''
    except ValueError:
        return ''
    match = re.fullmatch(r'/item/detail/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)/?', parts.path, re.I)
    return match[1].lower() if match else ''


def _willhaben_item_id(url: str) -> str:
    # Bind redirects and native recovery to the ORIGINAL offer, including
    # negative statuses. Another item's 404/SOLD cannot close this candidate.
    from willhaben_reader import _item_id
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or (p.hostname or '').lower() not in ('willhaben.at','www.willhaben.at')
                or p.username or p.password or p.port not in (None,443)):
            return ''
        return _item_id(p.path)  # Tracking parameters cannot erase the original ID.
    except (TypeError,ValueError,UnicodeError):
        return ''


def index_card_negative(url: str, snippet: str) -> dict:
    """Only an associated index-card snippet may use short Reverb status text."""
    if not isinstance(snippet,str):
        return {}
    text = BeautifulSoup(snippet,'lxml').get_text(' ',strip=True)
    if _offerup_item_id(url):
        lines = [BeautifulSoup(line, 'lxml').get_text(' ', strip=True) for line in snippet.splitlines()]
        if (not re.search(r'\bthis item has been sold\b', text, re.I)
                and not any(re.fullmatch(r'sold[.!]?', line, re.I) for line in lines)):
            return {}
        return {'_indexed_availability_url':url, '_indexed_availability_negative':'sold'}
    if not _reverb_item_id(url):
        return {}
    match = re.search(r'\b(this gear has sold|this listing has ended)\b',text,re.I)
    if not match:
        return {}
    return {'_indexed_availability_url':url, '_indexed_availability_negative':
            'sold' if match[1].casefold() == 'this gear has sold' else 'ended'}


def _offerup_title(value: str) -> str:
    title = re.sub(r'\s+', ' ', html.unescape(value)).strip()
    title = re.sub(r'\s+for sale in\s+.+?\s*[-–|]\s*offerup\s*$', '', title, flags=re.I)
    return re.sub(r'\s*[-–|]\s*offerup\s*$', '', title, flags=re.I).casefold()


def indexed_closed_reason(listing: dict) -> str:
    """A title-bound extracted Reverb status is negative evidence, not live stock.

    This is deliberately narrower than searching descriptions for 'sold'. A
    markdown primary heading and a standalone status before the description,
    or an explicitly URL-bound negative index card, are required. A successful
    current primary check can supersede this index.
    """
    url = listing.get('url', '')
    offerup_id = _offerup_item_id(url)
    if offerup_id:
        bound_url = listing.get('_indexed_availability_url')
        if (_offerup_item_id(bound_url) == offerup_id
                and listing.get('_indexed_availability_negative') == 'sold'):
            return 'OfferUp: rezultatul indexat indică SOLD; verificarea primară actuală este necesară'
        title = _offerup_title(str(listing.get('title') or ''))
        for content in (listing.get('_discovery_description'), listing.get('description')):
            matched, size = False, 0
            for raw in str(content or '').splitlines():
                line = re.sub(r'\s+', ' ', html.unescape(raw)).strip()
                heading = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
                value = heading.group(1).strip() if heading else line
                if re.match(r'^(?:description|details|other (?:listings|items)|similar|related|recommended|more from|you may also like)\b', value, re.I):
                    if matched:
                        break
                if heading and not matched:
                    if title and _offerup_title(value) == title:
                        matched = True
                    continue
                if not matched:
                    continue
                size += len(line)
                if size > 1200:
                    break
                if heading and not re.match(r'^(?:sold by\b|additional images\b)', value, re.I):
                    break
                if value.casefold() == 'sold' and not heading:
                    return 'OfferUp: textul indexat indică SOLD; verificarea primară actuală este necesară'
        return ''
    if not _reverb_item(url):
        return ''
    bound_url = listing.get('_indexed_availability_url')
    if (isinstance(bound_url, str) and _reverb_item(bound_url)
            and listing.get('_indexed_availability_negative') in ('sold', 'ended')):
        if _reverb_item_id(bound_url) == _reverb_item_id(listing['url']):
            return 'Reverb: rezultatul indexat indică SOLD/ended pentru acest URL; verificarea primară actuală este necesară'
    title = re.sub(r'\s+', ' ', html.unescape(str(listing.get('title', '')))).casefold().split(' | ')[0]
    if not title:
        return ''
    for content in (listing.get('_discovery_description'), listing.get('description')):
        matched, size = False, 0
        for line in str(content or '').splitlines():
            line = re.sub(r'\s+', ' ', html.unescape(line)).strip()
            heading = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
            if heading:
                if heading.group(1).casefold() != title:
                    if matched:
                        break
                    continue
                matched = True
                continue
            if not matched:
                continue
            size += len(line)
            if size > 1200 or re.match(r'^(?:About this listing|Product specs|Promoted similar listings|Listed\s*:)', line, re.I):
                break
            if line.casefold() in ('sold', 'this gear has sold', 'this listing has ended'):
                return 'Reverb: textul extras indică SOLD; verificarea primară actuală este necesară'
    return ''


def availability_conflict(listing: dict, state: str) -> bool:
    return state == 'unknown' and bool(listing.get('_availability_conflict')
        or indexed_closed_reason(listing) or saved_indexed_closed_reason(listing))


def _indexed_state_key(listing: dict) -> str:
    item_id = _reverb_item_id(listing.get('url', ''))
    if item_id:
        return 'availability:indexed_closed:reverb:' + item_id
    item_id = _offerup_item_id(listing.get('url', ''))
    return 'availability:indexed_closed:offerup:' + item_id if item_id else ''


def remember_indexed_closed(listing: dict) -> None:
    """Share URL-bound negative evidence across rediscovery and provider queues."""
    reason = indexed_closed_reason(listing)
    if reason:
        from provider_state import write
        write(_indexed_state_key(listing), {'reason': reason, 'observed': time.time()})


def saved_indexed_closed_reason(listing: dict) -> str:
    key = _indexed_state_key(listing)
    if not key:
        return ''
    from provider_state import read
    value = read(key, {})
    return str(value.get('reason') or '') if isinstance(value, dict) else ''


def primary_closed_reason(soup, host: str, url: str = '') -> str:
    """Marketplace-specific primary status, never generic 'sold' in a sidebar."""
    if _offerup_item_id(url):
        primary = BeautifulSoup(str(soup.find('main') or soup), 'lxml')
        for node in primary.select('script, style, aside, nav, footer'):
            node.decompose()
        for node in list(primary.find_all(True)):
            if node.parent is None:
                continue
            labels = ' '.join(str(node.get(k) or '') for k in ('class','id','aria-label','data-testid'))
            if re.search(r'recommend|related|similar|suggested|promoted', labels, re.I):
                node.decompose()
        heading = primary.find('h1')
        if heading:
            from bs4.element import Tag, NavigableString
            for link in list(primary.find_all('a',href=True)):
                if link.parent is None:
                    continue
                other = _offerup_item_id(urljoin(url,link.get('href','')))
                if other and other != _offerup_item_id(url):
                    box = link.find_parent(('article','section')) or link.find_parent('div')
                    if box and box.find('h1') is not heading:
                        box.decompose()
            for node in heading.next_elements:
                if isinstance(node, Tag):
                    if (node.get('id', '').casefold() == 'description'
                            or node.get('itemprop') == 'description'
                            or str(node.get('data-testid') or '').casefold() in ('description', 'item-description', 'listing-description')
                            or str(node.get('aria-label') or '').casefold() == 'description'
                            or (node.name in ('h1','h2','h3','h4')
                                and re.match(r'^(?:description|details|other (?:listings|items)|similar|related|recommended|more from|you may also like)\b', node.get_text(' ', strip=True), re.I))):
                        break
                elif isinstance(node, NavigableString):
                    if (' '.join(str(node).split()).casefold() == 'sold'
                            and not node.find_parent('a')
                            and not any(re.match(r'^sold\s+by\b', parent.get_text(' ', strip=True), re.I)
                                        for parent in node.parents if parent.name in ('h1','h2','h3','h4','span','div','p'))
                            and not any(isinstance(sibling, NavigableString) and sibling.strip()
                                        for sibling in (node.parent.previous_sibling, node.parent.next_sibling))
                            and not any(isinstance(sibling, Tag) and re.match(r'^(?:by\b|as is\b|separately\b)',sibling.get_text(' ',strip=True),re.I)
                                        for sibling in (node.parent.previous_sibling, node.parent.next_sibling))
                            and node.parent.name in ('span','div','strong','p')):
                        return 'OfferUp: anunț marcat SOLD'
    if _reverb_item(url):
        primary = BeautifulSoup(str(soup.find('main') or soup), 'lxml')
        for node in primary.select('script, style, aside, nav, footer, [class*="recommend"], [id*="recommend"], [class*="related"], [id*="related"]'):
            node.decompose()
        for node in list(primary.find_all(True)):
            if node.parent is None:
                continue
            labels = ' '.join(str(node.get(key) or '') for key in ('class','id','aria-label','data-testid'))
            if re.search(r'recommend|related|similar|suggested|promoted', labels, re.I):
                node.decompose()
        heading = primary.find('h1')
        if heading:
            # Reverb's sale status precedes the seller's About this listing
            # prose. Buy controls belonging to recommendations cannot reopen it.
            for text in heading.find_all_next(string=True):
                value = ' '.join(str(text).split()).casefold()
                if value in ('about this listing', 'product specs', 'promoted similar listings'):
                    break
                if value in ('this gear has sold', 'this listing has ended'):
                    return 'Reverb: starea primară indică anunț vândut/încheiat'
    if host == 'invaluable.com' or host.endswith('.invaluable.com'):
        if soup.select_one('.closed-bid-lot-detail') or (soup.title and
                soup.title.get_text(' ',strip=True).casefold().startswith('sold at auction:')):
            return 'Invaluable: lot încheiat'
    if host == 'auctions.gardinerhoulgate.co.uk':
        for status in soup.select('span.h4.text-warning, strong'):
            if status.get_text(' ', strip=True).casefold() == 'closed':
                return 'Gardiner Houlgate: lot încheiat'
    if host.removeprefix('www.') == 'easyliveauction.com':
        for status in soup.select('.auction-section .lot-status .status'):
            if any(p.name in ('aside','nav','footer') or any(word in ' '.join(p.get('class', [])).lower()
                    for word in ('recommend','related','lot-nav-carousel')) for p in status.parents):
                continue
            if status.get_text(' ',strip=True).casefold() == 'closed':
                return 'Easy Live Auction: starea primară a lotului este Closed'
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
    original_offerup = _offerup_item_id(listing.get('url', ''))
    if original_offerup and _offerup_item_id(final_url) != original_offerup:
        return 'unknown', 'OfferUp: redirecționare către alt anunț; necesită reverificare'
    original_reverb = _reverb_item_id(listing.get('url', ''))
    if original_reverb and _reverb_item_id(final_url) != original_reverb:
        return 'unknown', 'Reverb: redirecționare către alt anunț; necesită reverificare'
    original_willhaben = _willhaben_item_id(listing.get('url', ''))
    if original_willhaben and _willhaben_item_id(final_url) != original_willhaben:
        return 'unknown', 'Willhaben: redirecționare către alt anunț; necesită reverificare'
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
    closed = primary_closed_reason(soup, host, final_url)
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
    from product_metadata import primary_microdata, is_primary_product, page_condition
    products, editorial = [], False
    for tag in soup.find_all("script", type="application/ld+json"):
        if any(p.name in ('aside','nav','footer') or any(word in ' '.join(p.get('class',[])).lower()
                for word in ('recommend','related')) for p in tag.parents):
            continue
        try:
            for node in _nodes(json.loads(tag.string or tag.get_text())):
                types = node.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if not isinstance(types,list):
                    types = []
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
    # Condition evidence must agree across JSON-LD/microdata representations;
    # a singleton without an actual URL/H1 match is not primary proof.
    primary_title = h1.get_text(' ',strip=True) if h1 else ''
    condition, condition_ambiguous = page_condition(products, final_url,primary_title)
    if condition:
        listing['condition'] = condition
    primary_products = [product for product in products if is_primary_product(product,final_url,primary_title)]
    closed_codes = ('soldout','outofstock','discontinued')
    all_closed = bool(primary_products)
    for product in primary_products:
        offers = product.get("offers") or []
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers,list):
            offers = []
        all_closed &= bool(offers) and all(isinstance(offer,dict) and not offer.get('offers')
                    and str(offer.get('availability','')).rsplit('/',1)[-1].lower() in closed_codes for offer in offers)
    if all_closed:
        return 'dead','toate ofertele primare sunt încheiate/indisponibile'
    for product in primary_products:
        name = str(product.get("name") or "")
        offers = product.get("offers") or []
        offers = [offers] if isinstance(offers,dict) else offers
        if not isinstance(offers,list):
            offers = []
        for offer in offers:
            if not isinstance(offer,dict):
                continue
            availability = str(offer.get("availability", "")).rsplit("/", 1)[-1].lower()
            if availability in closed_codes and not offer.get('offers'):
                continue  # Another associated offer can still be available.
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
                if condition_ambiguous:
                    return 'unknown', 'condiție nou/folosit neconfirmată; date incomplete sau contradictorii'
                return "live", "instrument cu ofertă structurată"
    if editorial:
        return "non_sale", "articol / recenzie, fără ofertă proprie"
    for tag in main.find_all(["script", "style", "nav", "footer"]):
        tag.decompose()
    scoped = BeautifulSoup(str(main), "lxml")
    for related in scoped.select('aside, [class*="recommend"], [id*="recommend"], [class*="related"], [id*="related"]'):
        related.decompose()
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
            if condition_ambiguous:
                return 'unknown','condiție nou/folosit neconfirmată; date incomplete sau contradictorii'
            return "live", "pagină de vânzare / licitație"
    if item_page:
        return "unknown", "pagină de anunț; ofertă neverificată"
    return "non_sale", "nu am găsit o ofertă de vânzare"


async def verify_offer(listing: dict, client) -> tuple:
    listing.pop('_availability_conflict', None)
    remember_indexed_closed(listing)
    closed = indexed_closed_reason(listing) or saved_indexed_closed_reason(listing)
    state, reason = await _verify_offer(listing, client)
    if state == 'live' and closed:
        from provider_state import write
        write(_indexed_state_key(listing), {})
    if state == 'unknown' and closed:
        listing['_availability_conflict'] = closed
        return state, closed + '; ' + reason
    return state, reason


async def _verify_offer(listing: dict, client) -> tuple:
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
    if facebook:
        verified = facebook['listing']
        if verified.get('apify_is_sold') is True:
            return 'dead', 'Facebook: vândut la verificarea detaliilor'
        if (verified.get('apify_is_pending') is True or verified.get('apify_is_hidden') is True
                or verified.get('apify_is_live') is False):
            origin = listing.get('source')
            listing.update(verified)
            if origin:
                listing['source'] = origin
            return 'unknown', 'Facebook: rezervat, ascuns sau momentan indisponibil'
        from scrapers.apify_facebook import available
        if facebook['checked'] >= time.time() - 6 * 3600 and available(verified):
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
        from scrapers.govdeals import GovDealsClient, parse_asset, asset_closed_reason, IncompleteAuctionDetails
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
        except IncompleteAuctionDetails:
            return 'unknown', 'GovDeals: API răspunde fără detaliile lotului; necesită reverificare'
        except Exception:
            return "unknown", "GovDeals: API indisponibil"
    reverb = REVERB_ITEM_RX.search(url)
    host = (urlsplit(url).hostname or "").lower()
    if reverb and (host == "reverb.com" or host.endswith(".reverb.com")):
        try:
            from reverb_api import get_listing, listing_state
            response = await get_listing(client, reverb.group(1))
            if response.status_code == 404:
                return "dead", "reverb: not found"
            if response.status_code == 200:
                try:
                    data = response.json()
                except (ValueError, TypeError):
                    data = None
                state = listing_state(data, reverb.group(1))
                if state not in ('unknown', 'live'):
                    return "dead", f"reverb state={state}"
                if state == "live":
                    if (any(data.get(k) is not None and not isinstance(data[k],str)
                            for k in ('title','description','make','model','finish'))
                            or any(data.get(k) is not None and not isinstance(data[k],dict)
                                   for k in ('condition','price'))):
                        raise ValueError('invalid Reverb listing attributes')
                    from scrapers.reverb import identity_fields
                    from filters import is_zeta_violin
                    identity = identity_fields(data)
                    api_title = (data.get('title') or '').strip()
                    api_description = BeautifulSoup(data.get('description') or '', 'lxml').get_text(' ',strip=True)
                    if not api_title and not is_zeta_violin('',api_description,brand=identity['brand'],
                                                          model=identity['model'],categories=identity['categories']):
                        # Live status alone does not identify the offered object.
                        # Keep weak discovery in the queue if HTML is blocked.
                        listing['_query_identity_unverified'] = True
                        raise ValueError('Reverb primary item identity missing')
                    listing["title"] = api_title
                    listing["description"] = api_description
                    listing.update(identity)
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
            original_reverb = _reverb_item_id(listing.get('url', ''))
            if original_reverb and _reverb_item_id(current) != original_reverb:
                return 'unknown', 'Reverb: redirecționare către alt anunț; necesită reverificare'
            original_willhaben = _willhaben_item_id(listing.get('url', ''))
            if original_willhaben and _willhaben_item_id(current) != original_willhaben:
                return 'unknown', 'Willhaben: redirecționare către alt anunț; necesită reverificare'
            if is_disabled_source(current):
                return "non_sale", "redirecționare către o sursă eliminată"
            if not public_url(current):
                return "non_sale", "redirect către URL nepublic"
            if current != url and not await public_dns(current):
                return "non_sale", "redirect către destinație nepublică sau neverificată"
            response = await client.get(current, follow_redirects=False, headers={"User-Agent": UA})
            if response.is_redirect and response.headers.get("location"):
                from urllib.parse import urljoin
                destination = urljoin(current, response.headers["location"])
                if response.status_code == 308 and _willhaben_item_id(listing.get('url','')):
                    from willhaben_reader import expired_redirect
                    if expired_redirect(listing['url'],destination):
                        return 'dead', 'Willhaben: redirecționarea primară confirmă ID-ul anunțului expirat'
                current = destination
                continue
            state, reason = inspect_page(listing, response.text, response.status_code, current)
            if (state == 'unknown' and (urlsplit(current).hostname or '').lower()
                    in ('willhaben.at','www.willhaben.at')):
                try:
                    from willhaben_reader import fetch as private_read
                    from urllib.parse import urlunsplit
                    parts = urlsplit(current)
                    private_url = urlunsplit((parts.scheme,parts.netloc,parts.path,'',''))
                    private = await private_read(private_url,client)
                    if private is not None and private[0] == 308 and len(private) > 3:
                        from willhaben_reader import expired_redirect
                        if expired_redirect(listing['url'],private[3]):
                            return 'dead', 'Willhaben: redirecționarea primară US confirmă ID-ul anunțului expirat'
                    if private is not None and private[0] in (200,404,410):
                        code, rendered, final = private[:3]
                        state, reason = inspect_page(listing, rendered, code, final)
                        reason = 'Private US native: ' + reason
                except Exception:
                    pass  # Existing independent reserve remains; never invent stock.
            if (state == 'unknown' and response.status_code in (247,403)
                    and (urlsplit(current).hostname or '').lower() in ('gumtree.com','www.gumtree.com')):
                try:
                    from public_node_fetch import fetch as node_fetch
                    code, rendered, final = await node_fetch(current)
                    state, reason = inspect_page(listing, rendered, code, final)
                    reason = 'Public Node: ' + reason
                except Exception:
                    pass  # Keep the existing independent reserve and unknown route.
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
