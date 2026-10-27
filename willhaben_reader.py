"""Private, bounded Willhaben reader client; the primary bot owns the ledger."""
import asyncio
from datetime import datetime, timezone
import json
import math
import re
import time
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx
from config import Config
from provider_state import store
from work_context import work_kind

MAX_BODY = 2 * 1024 * 1024
MAX_WIRE = 6 * MAX_BODY + 8192
REGION = 'us-east4-eqdc4a'
VERSION = 'willhaben-reader-v1'
_LIMIT = asyncio.Semaphore(2)
_ALL = '/iad/kaufen-und-verkaufen/marktplatz'
_MUSIC = _ALL + '/musikinstrumente-noten-6540'
_ITEM = re.compile(r'^/iad/kaufen-und-verkaufen/d/([^/?#]+)/?$')


class ReaderUnavailable(Exception):
    """A fixed, non-sensitive failure; caller should use existing fallbacks."""


def _base_url(value):
    try:
        p = urlsplit(value)
        if (p.scheme != 'http' or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.railway\.internal', p.hostname or '')
                or p.username or p.password or not p.port or p.path not in ('', '/') or p.query or p.fragment):
            return ''
        return f'http://{p.hostname}:{p.port}'
    except (ValueError, TypeError):
        return ''


def _item_id(path):
    match = _ITEM.fullmatch(path)
    if (not match or re.search(r'%(?![0-9a-fA-F]{2})', path)
            or re.search(r'%2f|%5c', path, re.I)):
        return ''
    slug = unquote(match[1], errors='strict')
    end = re.search(r'-([0-9]+)$', slug)
    if (not end or unicodedata.category(slug[0])[0] not in ('L','N')
            or any(c not in '_-' and unicodedata.category(c)[0] not in ('L','N','M') for c in slug)):
        return ''
    return end[1]


def _target(url):
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or (p.hostname or '').lower() not in ('willhaben.at','www.willhaben.at')
                or p.username or p.password or p.port not in (None, 443) or p.fragment):
            return None
        if p.path in (_ALL, _MUSIC):
            params = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
            if (len(params) != 1 or params[0][0] != 'keyword' or not params[0][1].strip()
                    or len(params[0][1].encode('utf-16-le')) // 2 > 400):
                return None
            return {'kind':'search','keyword':params[0][1], 'category':'music' if p.path == _MUSIC else 'all'}
        if p.query or not _item_id(p.path):
            return None
        return {'kind':'item','path':p.path}
    except (ValueError, TypeError, UnicodeError):
        return None


def _same_target(expected, observed):
    target = _target(observed)
    if not target or target['kind'] != expected['kind']:
        return False
    if target['kind'] == 'search':
        return target == expected
    return _item_id(target['path']) == _item_id(expected['path'])


def _reserve(auxiliary, now=None):
    """Atomic aggregate daily/rolling ceilings and protected core allowance."""
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        def total(provider, since):
            return conn.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>=?',
                                (provider, since)).fetchone()[0]
        rolling = total('willhaben_reader', now - 31 * 86400)
        daily = total('willhaben_reader', day)
        if (rolling >= 6200 or daily >= 200 or (auxiliary and
                (total('willhaben_reader:aux', day) >= 160 or
                 total('willhaben_reader:aux', now - 31 * 86400) >= 4960))):
            conn.rollback()
            return False
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                         [('willhaben_reader',now,1), ('willhaben_reader:aux' if auxiliary else 'willhaben_reader:core',now,1)])
        conn.commit()
        return True


async def fetch(url: str, client=None, *, auxiliary=None, timeout=15):
    """Return (status, HTML, URL), None when unconfigured, else ReaderUnavailable."""
    configured = getattr(Config, 'WILLHABEN_READER_URL', '')
    if not configured:
        return None
    base, target = _base_url(configured), _target(url)
    if not base or not target:
        raise ReaderUnavailable('reader request invalid')
    # Primary details never consume the daily/rolling reserve for native search.
    is_aux = True if target['kind'] == 'item' else (work_kind.get() != 'core' if auxiliary is None else bool(auxiliary))
    try:
        async with asyncio.timeout(timeout):
            async with _LIMIT:
                if not await asyncio.to_thread(_reserve, is_aux):
                    raise ReaderUnavailable('reader allowance unavailable')
                if client is None:
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as own:
                        return await _read(own, base, target, timeout)
                return await _read(client, base, target, timeout)
    except asyncio.CancelledError:
        raise
    except ReaderUnavailable:
        raise
    except Exception:
        raise ReaderUnavailable('reader unavailable') from None


async def _read(client, base, target, timeout):
    async with client.stream('POST', base + '/read', json=target, follow_redirects=False, timeout=timeout) as response:
        if response.status_code != 200:
            raise ReaderUnavailable('reader response unavailable')
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_WIRE:
                raise ReaderUnavailable('reader response invalid')
            chunks.append(chunk)
    try:
        data = json.loads(b''.join(chunks))
        if (not isinstance(data,dict) or set(data) != {'version','requested','url','status','body','checked','region'}
                or data['version'] != VERSION or data['region'] != REGION
                or not isinstance(data['requested'],str) or not _same_target(target,data['requested'])
                or not isinstance(data['url'],str) or not _same_target(target,data['url'])
                or type(data['status']) is not int or not 100 <= data['status'] <= 599
                or not isinstance(data['body'],str) or len(data['body'].encode('utf-8')) > MAX_BODY
                or type(data['checked']) not in (int,float) or not math.isfinite(data['checked'])
                or abs(time.time() - data['checked']) > 120):
            raise ValueError('invalid')
        return data['status'], data['body'], data['url']
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ReaderUnavailable('reader response invalid') from None
