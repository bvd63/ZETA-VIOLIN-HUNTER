"""ID/variation-bound Browse details, independent of index snippets and RO shipping.

11003 means not found, not proven SOLD; 11004 may be a temporary seller update.
Both retain the candidate for retry. Transport/auth/limit failures prove neither.
Official contract: developer.ebay.com/develop/api/buy/browse_api/item/getItemByLegacyId
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit
import weakref

import httpx
from bs4 import BeautifulSoup

from config import Config
from provider_state import read, store
from work_context import work_kind

log = logging.getLogger(__name__)
API = 'https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id'
MARKETS = {'com': 'US', 'co.uk': 'GB', 'de': 'DE', 'fr': 'FR', 'it': 'IT', 'es': 'ES',
           'com.au': 'AU', 'ca': 'CA', 'at': 'AT', 'ch': 'CH', 'nl': 'NL', 'pl': 'PL',
           'ie': 'IE', 'be': 'BE', 'com.sg': 'SG', 'com.hk': 'HK'}
_LOOPS = weakref.WeakKeyDictionary()
_AUTH = None


def target(url):
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower().removeprefix('www.')
        # eBay Belgium has official language subdomains.
        if host in ('befr.ebay.be', 'benl.ebay.be'):
            host = 'ebay.be'
        market = MARKETS.get(host.removeprefix('ebay.')) if host.startswith('ebay.') else None
        match = re.fullmatch(r'/itm/(?:[^/]+/)?(\d+)/?', p.path)
        variants = parse_qs(p.query, keep_blank_values=True).get('var', [])
        if (p.scheme not in ('https', 'http') or not market or not match or p.username or p.password
                or p.port not in (None, 80, 443) or len(variants) > 1
                or variants and not re.fullmatch(r'\d+', variants[0])):
            return None
        return {'id': match[1], 'variation': variants[0] if variants else '0', 'market': 'EBAY_' + market}
    except (TypeError, ValueError):
        return None


def _key(t):
    return 'ebay:primary:' + t['id'] + ':' + t['variation'] + ':' + t['market']


def _closed_key(t):
    return 'ebay:closed:' + t['id'] + ':' + t['variation']


def _bound(data, t):
    if not isinstance(data, dict):
        return False
    if type(data.get('legacyItemId')) not in (str, int) or str(data['legacyItemId']) != t['id']:
        return False
    rest = re.fullmatch(r'v1\|(\d+)\|(\d+)', str(data.get('itemId') or ''))
    web = target(data.get('itemWebUrl'))
    return bool(rest and rest[1] == t['id'] and rest[2] == t['variation'] and web
                and web['id'] == t['id'] and web['variation'] in ('0', t['variation']))


def parse_primary(data, t, *, now=None):
    if not _bound(data, t):
        return None
    now = time.time() if now is None else now
    title = data.get('title')
    if (not isinstance(title, str) or not title.strip()
            or any(k in data and data[k] is not None and not isinstance(data[k], str)
                   for k in ('description', 'shortDescription', 'condition', 'brand', 'categoryPath'))):
        return None
    avail = data.get('estimatedAvailabilities')
    statuses = [a.get('estimatedAvailabilityStatus') for a in avail if isinstance(a, dict)] if isinstance(avail, list) else []
    known = (bool(statuses) and isinstance(avail, list) and len(statuses) == len(avail)
             and all(s in ('IN_STOCK', 'OUT_OF_STOCK') for s in statuses))
    if known:
        for option in avail:
            quantities = [option[k] for k in ('estimatedAvailableQuantity', 'estimatedRemainingQuantity') if k in option]
            if (any(type(q) is not int or q < 0 for q in quantities)
                    or option['estimatedAvailabilityStatus'] == 'IN_STOCK' and any(q == 0 for q in quantities)
                    or option['estimatedAvailabilityStatus'] == 'OUT_OF_STOCK' and any(q > 0 for q in quantities)):
                known = False  # conflicting stock is unknown, never invented live/SOLD
                break
    # Different delivery options may differ: one buyable option keeps the item.
    state = ('live' if known and any(s == 'IN_STOCK' for s in statuses)
             else 'held' if known and all(s == 'OUT_OF_STOCK' for s in statuses) else 'unknown')
    reason = 'eBay: API primar fără stoc' if state == 'held' else 'eBay: detalii primare; disponibilitate ' + state
    if data.get('itemEndDate'):
        try:
            end = datetime.fromisoformat(str(data['itemEndDate']).replace('Z', '+00:00'))
            if end.tzinfo is None:
                raise ValueError('unscoped time')
            if end.timestamp() <= now:
                state, reason = 'held', 'eBay: termenul primar al anunțului a expirat'
        except (ValueError, TypeError, OverflowError):
            if state != 'held':
                state, reason = 'unknown', 'eBay: termen primar neconfirmat'
    body = data.get('description') or data.get('shortDescription') or ''
    body = BeautifulSoup(body, 'lxml').get_text(' ', strip=True) if body else ''
    # Only metadata of this bound primary item; never recommendations/products.
    aspects = data.get('localizedAspects') if isinstance(data.get('localizedAspects'), list) else []
    labels = lambda text: ''.join(c for c in unicodedata.normalize('NFKD', text).casefold() if not unicodedata.combining(c)).strip()
    def aspect(names):
        names = {labels(name) for name in names}
        return [a['value'].strip() for a in aspects if isinstance(a, dict) and isinstance(a.get('name'), str)
                and labels(a['name']) in names and isinstance(a.get('value'), str) and a['value'].strip()]
    models = aspect({'model', 'modele', 'modell', 'modello', 'modelo', 'モデル', '모델', '型号', '型號'})
    brands = aspect({'brand', 'marque', 'marke', 'hersteller', 'marca', 'merk', 'ブランド', 'メーカー', '브랜드', '品牌'})
    price = data.get('price') or data.get('currentBidPrice') or data.get('minimumPriceToBid')
    formatted = 'N/A'
    if isinstance(price, dict) and isinstance(price.get('currency'), str) and re.fullmatch(r'[A-Z]{3}', price['currency']):
        try:
            value = float(price['value']) if type(price.get('value')) in (str, int, float) else float('nan')
            if math.isfinite(value) and value >= 0:
                formatted = f'{value:g} {price["currency"]}'
        except (TypeError, ValueError, OverflowError):
            pass
    item = {'title': title.strip(), 'description': body, 'brand': data.get('brand') or ', '.join(brands),
            'model': ', '.join(models), 'categories': (data.get('categoryPath') or '').split('|'),
            'condition': data.get('condition') or '', 'price': formatted,
            '_ebay_primary_id': t['id'], '_ebay_primary_variation': t['variation']}
    return {'state': state, 'reason': reason, 'listing': item, 'terminal': state == 'held'}


def parse_response(response, t, *, now=None):
    try:
        data = response.json()
    except (ValueError, TypeError):
        return None
    if response.status_code == 200:
        return parse_primary(data, t, now=now)
    errors = data.get('errors') if isinstance(data, dict) else None
    if not isinstance(errors, list) or len(errors) != 1 or not isinstance(errors[0], dict):
        return None
    e = errors[0]
    if (e.get('domain') != 'API_BROWSE' or e.get('category') != 'REQUEST' or type(e.get('errorId')) is not int):
        return None
    if ((response.status_code in (400, 404) and e['errorId'] == 11003)
            or (response.status_code in (400, 409) and e['errorId'] == 11004)):
        return {'state': 'held', 'reason': f'eBay: API_BROWSE {e["errorId"]}; ofertă negăsită sau momentan indisponibilă, reîncercare păstrată', 'listing': {}}
    return None


def cached(url):
    t = target(url)
    if not t:
        return None
    value = read(_key(t), {})
    if (not isinstance(value, dict) or value.get('id') != t['id'] or value.get('variation') != t['variation'] or value.get('market') != t['market']
            or value.get('state') not in ('live', 'held', 'unknown')
            or type(value.get('checked')) not in (int, float) or not math.isfinite(value['checked'])
            or value['checked'] > time.time() + 5):
        return None
    return value


def hold(url):
    """Not-found/update errors stay regional; bound expired/out-of-stock is shared."""
    t = target(url)
    if not t:
        return None
    local = cached(url)
    value = read(_closed_key(t), {})
    if (isinstance(value, dict) and value.get('state') == 'held' and value.get('id') == t['id']
            and value.get('variation') == t['variation'] and type(value.get('checked')) in (int, float)
            and math.isfinite(value['checked']) and value['checked'] <= time.time()+5
            and (not local or local['state'] != 'live' or value['checked'] >= local['checked'])):
        return value
    return local if local and local['state'] == 'held' else None


def remember(t, record, checked):
    if (record.get('state') not in ('live', 'held', 'unknown') or type(checked) not in (int, float)
            or not math.isfinite(checked) or checked < 0):
        raise ValueError('invalid eBay primary observation')
    record = {**record, 'checked': checked, 'id': t['id'], 'variation': t['variation'], 'market': t['market']}
    with store() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT value FROM provider_state WHERE key=?', (_key(t),)).fetchone()
        previous = json.loads(row[0]) if row else {}
        if checked < previous.get('checked', 0) or previous.get('state') == 'held' and record['state'] == 'unknown':
            c.rollback()
            return
        c.execute('INSERT OR REPLACE INTO provider_state VALUES(?,?)', (_key(t), json.dumps(record)))
        if record['state'] == 'live' or record['state'] == 'held' and record.get('terminal') is True:
            row = c.execute('SELECT value FROM provider_state WHERE key=?', (_closed_key(t),)).fetchone()
            global_previous = json.loads(row[0]) if row else {}
            if checked >= global_previous.get('checked', 0):
                marker = {k: v for k, v in record.items() if k != 'listing'}
                c.execute('INSERT OR REPLACE INTO provider_state VALUES(?,?)', (_closed_key(t), json.dumps(marker)))
        c.commit()


def _reserve():
    now = time.time()
    day = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    aux = work_kind.get() != 'core'
    with store() as c:
        c.execute('BEGIN IMMEDIATE')
        def total(name, since):
            return c.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>=?', (name, since)).fetchone()[0]
        if (total('ebay_primary', day) >= 400 or total('ebay_primary', now - 31*86400) >= 12400
                or aux and (total('ebay_primary:aux', day) >= 300 or total('ebay_primary:aux', now-31*86400) >= 9300)):
            c.rollback()
            return False
        c.executemany('INSERT INTO provider_reservations VALUES(?,?,?)', [('ebay_primary', now, 1), ('ebay_primary:aux' if aux else 'ebay_primary:core', now, 1)])
        c.commit()
        return True


async def fetch_primary(url):
    global _AUTH
    t = target(url)
    if not t:
        return None
    loop = asyncio.get_running_loop()
    if loop not in _LOOPS:
        _LOOPS[loop] = ([asyncio.Lock() for _ in range(16)], asyncio.Semaphore(3), asyncio.Lock())
    locks, slots, auth_lock = _LOOPS[loop]
    async with locks[hashlib.sha256(_key(t).encode()).digest()[0] % 16]:
        previous = cached(url)
        negative = hold(url)
        if previous and previous['state'] != 'held' and negative and negative['checked'] > previous['checked']:
            previous = None
        ttl = 300 if previous and previous['state'] == 'held' else 120
        if previous and time.time() - previous['checked'] < ttl:
            return previous
        if not Config.EBAY_CLIENT_ID or not Config.EBAY_CLIENT_SECRET:
            return None
        if not _reserve():
            log.info('eBay primary allowance deferred; original HTML/queue route retained')
            return None
        async with slots, httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            from scrapers.ebay import EbayScraper
            async with auth_lock:
                if (_AUTH is None or _AUTH.client_id != Config.EBAY_CLIENT_ID or _AUTH.client_secret != Config.EBAY_CLIENT_SECRET):
                    _AUTH = EbayScraper(Config.EBAY_CLIENT_ID, Config.EBAY_CLIENT_SECRET)
                token = await _AUTH._get_token(client)
            if not token:
                return None
            params = {'legacy_item_id': t['id']}
            if t['variation'] != '0':
                params['legacy_variation_id'] = t['variation']
            started = time.time()
            response = await client.get(API, params=params, headers={'Authorization': 'Bearer ' + token,
                'X-EBAY-C-MARKETPLACE-ID': t['market']}, follow_redirects=False)
            if response.status_code == 401:
                _AUTH._token = ''  # next bounded observation renews; no POST/GET retry loop
            record = parse_response(response, t) if len(response.content) <= 2*1024*1024 else None
            log.info('eBay fresh primary id=%s variation=%s market=%s HTTP=%s state=%s',
                     t['id'], t['variation'], t['market'], response.status_code, record['state'] if record else 'unverified')
            if record:
                remember(t, record, started)
                saved = cached(url)
                if record['state'] == 'unknown' and saved and saved['state'] == 'held':
                    return None  # retain hold, but permit a bound HTML recovery
                return saved
    return None
