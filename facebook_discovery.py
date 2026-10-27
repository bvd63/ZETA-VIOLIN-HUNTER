"""Additive US Marketplace discovery through existing search-provider accounts."""
import json
import re
from urllib.parse import urlsplit

from config import Config
from database import connect
from keywords import MODEL_CODES, PREFERRED_QUERIES

US_REGIONS = (
    'Los Angeles California', 'New York New York', 'Chicago Illinois', 'Nashville Tennessee',
    'San Francisco California', 'Austin Texas', 'Seattle Washington', 'Boston Massachusetts',
    'Miami Florida', 'Denver Colorado', 'Phoenix Arizona', 'Atlanta Georgia',
    'Detroit Michigan', 'Minneapolis Minnesota', 'Houston Texas', 'Washington DC',
    'Portland Oregon', 'Cleveland Ohio', 'San Diego California', 'Las Vegas Nevada',
)
TERMS = ('Zeta violin', 'Zetta violin', 'Zeta 4-string', 'Zeta 5-strings', 'Zeta Jazz Fusion',
         'Strados violin') + tuple('Zeta ' + code for code in MODEL_CODES)


def queries(cursor: int, count: int, offset: int = 0) -> list:
    result = []
    for i in range(count):
        slot = cursor + i + offset
        cycle = slot // 2
        if slot % 2 == 0:
            # Broad preferred-model query, without a city restriction.
            term = PREFERRED_QUERIES[cycle % len(PREFERRED_QUERIES)]
            query = term + ' used violin Facebook Marketplace United States'
        else:
            term = TERMS[cycle % len(TERMS)]
            query = term + ' Facebook Marketplace ' + US_REGIONS[cycle % len(US_REGIONS)]
        result.append((query, ['facebook.com']))
    return result


def item_id(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ('facebook.com','www.facebook.com','m.facebook.com'):
        return ''
    match = re.fullmatch(r'/marketplace/item/(\d+)/?', parts.path)
    return match.group(1) if match else ''


def seed_detail_candidates(items: list) -> int:
    """Queue public index leads for the existing two-detail budget, never run an Actor."""
    if not Config.APIFY_TOKEN or not Config.APIFY_ENABLED:
        return 0
    from scrapers.apify_facebook import store, discovery_reason
    from scrapers.base import BaseScraper
    inserted = 0
    with store() as conn:
        for item in items:
            identifier = item_id(item.get('url',''))
            if not identifier:
                continue
            candidate = {**item, 'id':BaseScraper()._make_id('facebook_marketplace',identifier),
                         'url':f'https://www.facebook.com/marketplace/item/{identifier}/',
                         'platform':'Facebook Marketplace', 'provider':'web-index',
                         'apify_is_live':None,'apify_is_sold':None,'apify_is_pending':None,'apify_is_hidden':None}
            if discovery_reason(candidate):
                continue
            # Keep stronger Actor data and known verified details on conflicts.
            inserted += conn.execute('INSERT OR IGNORE INTO apify_items (url,candidate) VALUES (?,?)',
                                      (candidate['url'],json.dumps(candidate,ensure_ascii=False))).rowcount
        conn.commit()
    return inserted


def cached_offer(url: str) -> dict:
    if not item_id(url):
        return {}
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apify_items'").fetchone():
            return {}
        row = conn.execute('SELECT details,checked FROM apify_items WHERE url=?',
                           (f'https://www.facebook.com/marketplace/item/{item_id(url)}/',)).fetchone()
        if row and row[0]:
            listing = json.loads(row[0])
            if not isinstance(listing,dict) or item_id(listing.get('url','')) != item_id(url):
                return {}  # Never transfer another item's sold/live evidence.
            return {'listing':listing,'checked':row[1]}
        return {}
    finally:
        conn.close()
