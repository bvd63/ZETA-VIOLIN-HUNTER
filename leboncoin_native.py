"""Bounded anonymous mobile API reads; no account, proxy, Actor or paid key.

Protocol reviewed against MIT etienne-hd/lbc 1.1.9 and verified from Railway.
Search cards cannot prove availability. Only a matching individual record can.
"""
import asyncio
import math
import logging
import time
import uuid

from leboncoin_api import item_id, remember, remember_status

API = 'https://api.leboncoin.fr'
PAGE_SIZE = 35
log = logging.getLogger(__name__)


def normalize_native(row, *, primary=False, expected_id=None):
    if not isinstance(row, dict):
        return {}
    identity = item_id(row.get('url'))
    if (not identity or type(row.get('list_id')) not in (int, str)
            or str(row['list_id']) != identity or expected_id and identity != expected_id
            or str(row.get('category_id')) != '30' or row.get('ad_type') != 'offer'
            or not isinstance(row.get('subject'), str) or not row['subject'].strip()
            or primary and (not isinstance(row.get('body'), str) or not row['body'].strip())):
        return {}
    body = row.get('body') if isinstance(row.get('body'), str) else ''
    status = str(row.get('status') or '').lower()
    # The condition/state attributes describe wear, not whether the ad is live.
    dead = (status in ('sold', 'expired', 'deleted', 'removed', 'inactive')
            or any(row.get(k) is True for k in ('is_sold', 'sold', 'deleted', 'expired')))
    held = (status in ('pending', 'reserved', 'hidden')
            or any(row.get(k) is True for k in ('pending', 'reserved', 'hidden'))
            or row.get('is_active') is False or row.get('is_live') is False)
    invalid_flags = any(k in row and row[k] is not None and type(row[k]) is not bool
                        for k in ('is_sold', 'sold', 'deleted', 'expired', 'pending', 'reserved', 'hidden', 'is_active', 'is_live'))
    state = ('dead' if dead else 'held' if held else 'live' if status == 'active' and not invalid_flags else 'unknown') if primary else 'unknown'
    prices = row.get('price')
    price = prices[0] if isinstance(prices, list) and len(prices) == 1 else None
    if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
        price = None
    attributes = row.get('attributes') if isinstance(row.get('attributes'), list) else []
    condition = next((a.get('value_label', '') for a in attributes if isinstance(a, dict)
                      and a.get('key') == 'condition' and isinstance(a.get('value_label'), str)), '')
    location = row.get('location') if isinstance(row.get('location'), dict) else {}
    images = row.get('images') if isinstance(row.get('images'), dict) else {}
    photos = images.get('urls') if isinstance(images.get('urls'), list) else []
    return {'url': row['url'], 'title': row['subject'].strip(), 'description': body,
            'platform': 'Leboncoin', 'price': f'{price:g} EUR' if price is not None else 'N/A',
            'condition': condition, 'location': ', '.join(x for x in (location.get('city'), 'France') if isinstance(x, str) and x),
            'date_posted': str(row.get('first_publication_date') or '')[:10],
            'image_url': next((x for x in photos if isinstance(x, str) and x.startswith('https://')), ''),
            '_leboncoin_id': identity, '_leboncoin_observed_state': state,
            '_leboncoin_verification_source': 'native mobile API' if primary else 'search card',
            '_leboncoin_primary_checked': primary}


def search_payload(keyword, offset=0):
    return {'filters': {'category': {'id': '30'}, 'enums': {'ad_type': ['offer']},
                        'keywords': {'text': keyword}, 'location': {}},
            'limit': PAGE_SIZE, 'limit_alu': 0, 'offset': offset, 'disable_total': False,
            'extend': True, 'listing_source': 'direct-search', 'sort_by': 'time', 'sort_order': 'desc'}


class NativeClient:
    def __init__(self, observer=None):
        self.observer = observer

    async def __aenter__(self):
        from curl_cffi import AsyncSession
        self.session = AsyncSession(impersonate='safari_ios', timeout=15, verify=True, trust_env=False,
            headers={'User-Agent': f'LBC;iOS;26.2;iPhone;phone;{uuid.uuid4()};wifi;101.44.0',
                     'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-site'})
        try:
            await self.request('GET', 'https://www.leboncoin.fr/')
        except BaseException:
            await self.session.close()
            raise
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def request(self, method, url, **kwargs):
        if self.observer:
            self.observer.requests_attempted += 1
        response = await self.session.request(method, url, allow_redirects=False, **kwargs)
        if response.status_code != 200:
            if self.observer:
                self.observer.failures.append(f'Leboncoin native HTTP {response.status_code}')
            raise RuntimeError(f'Leboncoin native HTTP {response.status_code}')
        if self.observer:
            self.observer.requests_ok += 1
        return response

    async def search(self, keyword, offset=0):
        data = (await self.request('POST', API + '/finder/search', json=search_payload(keyword, offset))).json()
        if (not isinstance(data, dict) or not isinstance(data.get('ads'), list)
                or len(data['ads']) > PAGE_SIZE or type(data.get('total')) is not int
                or data['total'] < 0 or data['total'] < len(data['ads'])):
            raise ValueError('Leboncoin native search schema invalid')
        if data['total'] > offset and not data['ads']:
            raise ValueError('Leboncoin native search unexpectedly empty')
        return data['ads'], data['total']

    async def primary(self, url):
        identity = item_id(url)
        if not identity:
            raise ValueError('Leboncoin primary URL invalid')
        # Handle terminal HTTP only on this fixed individual-ID API endpoint.
        try:
            response = await self.request('GET', API + '/api/adfinder/v1/classified/' + identity)
        except RuntimeError as exc:
            if str(exc) in ('Leboncoin native HTTP 404', 'Leboncoin native HTTP 410'):
                remember_status({'type': 'status', 'scope': 'ad', 'status': 'not_found', 'key': identity}, checked=time.time())
                return None
            raise
        item = normalize_native(response.json(), primary=True, expected_id=identity)
        if not item:
            raise ValueError('Leboncoin primary identity/schema invalid')
        remember(item)
        log.info('Leboncoin fresh primary id=%s state=%s via public mobile API', identity, item['_leboncoin_observed_state'])
        return item


async def refresh_primary(url):
    async with asyncio.timeout(30):
        async with NativeClient() as client:
            return await client.primary(url)
