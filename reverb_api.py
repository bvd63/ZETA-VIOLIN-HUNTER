"""Shared fixed-host Reverb API headers and conservative listing-state parsing."""
import re
import time
from urllib.parse import urlsplit
from config import Config
from scrapers.base import BROWSER_UA

API = 'https://api.reverb.com/api/listings'
CLOSED = frozenset(('sold','ended','expired','closed','deleted','cancelled','canceled'))


def build_headers() -> dict:
    headers = {'User-Agent':BROWSER_UA, 'Accept':'application/hal+json',
               'Content-Type':'application/hal+json', 'Accept-Version':'3.0'}
    if Config.REVERB_API_TOKEN:
        headers['Authorization'] = 'Bearer ' + Config.REVERB_API_TOKEN
    return headers


async def get_listing(client, item_id: str):
    if not isinstance(item_id,str) or not re.fullmatch(r'[0-9]+',item_id):
        raise ValueError('invalid Reverb listing ID')
    return await client.get(API + '/' + item_id, headers=build_headers(), follow_redirects=False)


def _web_id(url):
    if not isinstance(url,str):
        return ''
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http','https') or (parts.hostname or '').lower() not in ('reverb.com','www.reverb.com')
                or parts.username or parts.password or parts.port not in (None,80,443)):
            return ''
        match = re.fullmatch(r'/(?:[a-z]{2}(?:-[a-z]{2})?/)?item/([0-9]+)(?:-[^/]*)?/?',parts.path,re.I)
        return match[1] if match else ''
    except (TypeError,ValueError):
        return ''


def listing_state(data, item_id: str) -> str:
    """Only a bound, well-formed listing and an explicit recognized state count."""
    if not isinstance(data,dict):
        return 'unknown'
    if 'id' in data:
        value = data['id']
        if (isinstance(value,bool) or not isinstance(value,(str,int))
                or not re.fullmatch(r'[0-9]+',str(value)) or str(value) != item_id):
            return 'unknown'
    links = data.get('_links')
    if '_links' in data and not isinstance(links,dict):
        return 'unknown'
    web = links.get('web') if isinstance(links,dict) else None
    if isinstance(links,dict) and 'web' in links:
        if not isinstance(web,dict) or 'href' not in web or _web_id(web['href']) != item_id:
            return 'unknown'
    state = data.get('state')
    if not isinstance(state,dict) or not isinstance(state.get('slug'),str):
        return 'unknown'
    slug = state['slug'].lower()
    return slug if slug == 'live' or slug in CLOSED else 'unknown'


def remember_availability(data):
    """Share only an ID-bound current primary state; never guess from missing data."""
    if not isinstance(data,dict):
        return None
    value = data.get('id')
    if isinstance(value,bool) or not isinstance(value,(int,str)) or not re.fullmatch(r'[0-9]+',str(value)):
        return None
    item_id = str(value)
    links = data.get('_links')
    web = links.get('web') if isinstance(links,dict) else None
    url = web.get('href') if isinstance(web,dict) else None
    if _web_id(url) != item_id:
        return None
    state = listing_state(data,item_id)
    if state == 'unknown':
        return None
    from provider_state import write
    key = 'availability:indexed_closed:reverb:' + item_id
    try:
        write(key, {} if state == 'live' else {
            'reason':'Reverb: API primar state=' + state,
            'observed':time.time(), 'source':'primary_api'})
    except Exception:
        return None
    return state
