"""Read primary Product/Offer microdata, scoped away from recommendations.

Uses the structured-evidence approach documented by extruct/changedetection;
no external service or generative judgement is needed to extract these fields.
"""
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode


def same_product_url(candidate: str, url: str) -> bool:
    """Match the primary item, including query IDs and host, ignoring trackers."""
    from database import canonical_url
    try:
        def identity(value):
            parts = urlsplit(canonical_url(value))
            query = [(k,v) for k,v in parse_qsl(parts.query) if k.lower() not in
                     ('currency','locale','lang','language','country','ship_to')]
            return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),''))
        return identity(urljoin(url, candidate)) == identity(url)
    except ValueError:
        return False


def primary_microdata(soup, url: str) -> list:
    main = soup.find('main') or soup.find(id='main-content') or soup
    result = []
    for node in main.select('[itemscope][itemtype]'):
        if not str(node.get('itemtype', '')).rstrip('/').endswith('/Product'):
            continue
        parents = [node] + list(node.parents)
        if any(p.name in ('aside', 'nav', 'footer') or any(
                word in ' '.join(p.get('class', [])).lower() for word in ('recommend', 'related')) for p in parents):
            continue

        def value(prop: str, scope=None) -> str:
            owner = scope if scope is not None else node
            for tag in owner.select('[itemprop]'):
                if prop not in str(tag.get('itemprop', '')).split():
                    continue
                nearest = next((p for p in tag.parents if p.has_attr('itemscope')), None)
                if nearest is not owner:
                    continue
                if any(p.name in ('aside', 'nav', 'footer') or any(
                        word in ' '.join(p.get('class', [])).lower() for word in ('recommend', 'related'))
                       for p in tag.parents):
                    continue
                return str(tag.get('content') or tag.get('href') or tag.get_text(' ', strip=True))
            return ''

        own_url = value('url')
        if own_url and not same_product_url(own_url, url):
            continue
        name = value('name')
        h1 = main.find('h1')
        if not own_url and h1 and name.casefold() not in h1.get_text(' ', strip=True).casefold():
            continue
        offers = []
        for offer in node.select('[itemprop="offers"][itemscope], [itemscope][itemtype$="/Offer"]'):
            product_parent = next((p for p in offer.parents
                                   if str(p.get('itemtype', '')).rstrip('/').endswith('/Product')), None)
            if product_parent is not node or any(p.name in ('aside', 'nav', 'footer') or any(
                    word in ' '.join(p.get('class', [])).lower() for word in ('recommend', 'related'))
                    for p in offer.parents):
                continue
            offers.append({key: value(key, offer) for key in ('price', 'priceCurrency', 'availability') if value(key, offer)})
        if offers:
            result.append({'@type':'Product', 'name':name, 'url':url,
                           'description':value('description'), 'brand':value('brand'), 'model':value('model'), 'offers':offers})
    return result
