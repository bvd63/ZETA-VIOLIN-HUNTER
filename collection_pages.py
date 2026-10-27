"""Treat inventories as discovery inputs, never as individual instruments."""
import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit

from database import canonical_url
from source_policy import is_disabled_source

MERCATINO_ITEM = re.compile(r'/mm/a_[^/]+_id\d+\.html$', re.I)


def mercatino_item(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or '').removeprefix('www.') == 'mercatinomusicale.com' and bool(MERCATINO_ITEM.fullmatch(parts.path))


def known_collection_url(url: str) -> bool:
    parts = urlsplit(url)
    return ((parts.hostname or '').lower().removeprefix('www.') == 'mercatinomusicale.com'
            and parts.path.startswith('/mm/s_'))


def collection_leads(soup, url: str, item_page: bool) -> list | None:
    """None means not a proven collection; [] is a recognized empty list.

    An ItemList nested in a product recommendation is not primary-page proof.
    Only explicit collection types/known routes on non-item pages qualify.
    """
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().removeprefix('www.')
    known = known_collection_url(url)
    collection = known
    if not item_page and not known:
        primary_product = False
        from product_metadata import same_product_url, primary_microdata
        h1 = soup.find('h1')
        for tag in soup.find_all('script', type='application/ld+json'):
            try:
                roots = json.loads(tag.string or tag.get_text())
                roots = roots if isinstance(roots, list) else [roots]
                for root in roots:
                    if not isinstance(root, dict):
                        continue
                    nodes = root.get('@graph', [root])
                    if not isinstance(nodes, list):
                        continue
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        types = node.get('@type', [])
                        types = [types] if isinstance(types, str) else types
                        collection |= bool(set(types) & {'SearchResultsPage', 'CollectionPage', 'ItemList'})
                        if 'Product' in types and node.get('offers'):
                            name = str(node.get('name') or '')
                            primary_product |= bool(
                                (node.get('url') and same_product_url(str(node['url']), url))
                                or (not node.get('url') and name and h1 and
                                    name.casefold() in h1.get_text(' ', strip=True).casefold()))
            except (ValueError, TypeError):
                continue
        if primary_product or primary_microdata(soup, url):
            return None  # recommendation ItemLists never turn a primary product into a search page
    if not collection:
        return None
    from offer_verifier import is_item_url, public_url
    results, seen = [], set()
    for a in soup.select('a[href]'):
        try:
            target = canonical_url(urljoin(url, a['href']))
        except ValueError:
            continue
        if ((urlsplit(target).hostname or '').removeprefix('www.') != host
                or not public_url(target) or is_disabled_source(target)
                or target in seen or not is_item_url(target)):
            continue
        seen.add(target)
        title = a.get_text(' ', strip=True)
        image = a.find('img')
        if not title and image:
            title = str(image.get('alt') or '')
        title = title or urlsplit(target).path.rsplit('/', 1)[-1].replace('-', ' ')
        results.append({'id': 'collection_' + hashlib.sha256(target.encode()).hexdigest()[:20],
                        'platform': host, 'title': title[:300], 'description': '', 'source': 'search',
                        'url': target, 'price': 'See listing', 'location': 'Unknown', 'image_url': '',
                        '_discovered_via': url, '_discovery_priority': 2})
    return results
