"""Read primary Product/Offer microdata, scoped away from recommendations.

Uses the structured-evidence approach documented by extruct/changedetection;
no external service or generative judgement is needed to extract these fields.
"""
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode


def structured_condition(value) -> str:
    """Known schema.org enums only; descriptions such as 'new strings' are not evidence."""
    if isinstance(value, dict):
        value = value.get('@id')
    if not isinstance(value, str):
        return ''
    value = value.strip()
    try:
        parts = urlsplit(value)
        if parts.scheme:
            if (parts.scheme not in ('http','https') or parts.hostname not in ('schema.org','www.schema.org')
                    or parts.username or parts.password or parts.port not in (None,80,443)
                    or parts.query or parts.fragment):
                return ''
            value = parts.path.removeprefix('/')
    except ValueError:
        return ''
    return {'NewCondition':'Brand New', 'UsedCondition':'Used',
            'RefurbishedCondition':'Refurbished', 'DamagedCondition':'Damaged'}.get(value,'')


def primary_condition(product: dict) -> tuple[str, bool]:
    """Offer condition wins; contradictory/partially known offers stay uncertain."""
    fallback = structured_condition(product.get('itemCondition'))
    unknown = bool(product.get('itemCondition') and not fallback)
    offers = product.get('offers') or []
    offers = [offers] if isinstance(offers,dict) else offers
    if not isinstance(offers,list):
        return fallback,unknown
    conditions = []
    for offer in offers:
        # The matched primary Product owns these offers. An Offer URL may be
        # a separate checkout/seller URL, so it is not a product-identity gate.
        if not isinstance(offer,dict):
            continue
        if offer.get('offers'):
            # Unexpanded AggregateOffer data cannot prove that every child is
            # new. Keep the candidate uncertain rather than reject a used one.
            return 'Condiție neconfirmată',True
        explicit = offer.get('itemCondition')
        # An explicit unknown offer condition must not turn into product-level
        # New; only an absent/empty offer field inherits the product default.
        if explicit is None or explicit == '' or explicit == {}:
            conditions.append(fallback)
        else:
            condition = structured_condition(explicit)
            unknown |= not bool(condition)
            conditions.append(condition)
    if not conditions:
        return fallback,unknown
    if len(set(conditions)) == 1:
        return conditions[0],bool(unknown and not conditions[0])
    return 'Condiție neconfirmată',True


def is_primary_product(product: dict, url: str, title: str) -> bool:
    """A singleton alone is not proof that its stock belongs to this item."""
    name, own_url = str(product.get('name') or ''), str(product.get('url') or '').strip()
    if own_url and not same_product_url(own_url,url):
        return False
    if own_url and '#' not in own_url:
        return True
    return bool(name and title and name.casefold() in title.casefold())


def page_condition(products: list, url: str, title: str) -> tuple[str, bool]:
    """Combine all matched primary representations before choosing an offer."""
    evidence, uncertain = set(),False
    for product in products:
        if not is_primary_product(product,url,title):
            continue
        condition,unknown = primary_condition(product)
        if condition:
            evidence.add(condition)
        uncertain |= unknown
    if not evidence:
        return '',False  # Unknown metadata does not overwrite an existing label.
    if len(evidence)>1 or uncertain:
        return 'Condiție neconfirmată',True
    return evidence.pop(),False


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
            offers.append({key: value(key, offer) for key in ('price', 'priceCurrency', 'availability', 'itemCondition') if value(key, offer)})
        if offers:
            result.append({'@type':'Product', 'name':name, 'url':own_url,
                           'description':value('description'), 'brand':value('brand'), 'model':value('model'),
                           'itemCondition':value('itemCondition'), 'offers':offers})
    return result
