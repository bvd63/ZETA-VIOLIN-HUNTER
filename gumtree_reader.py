"""Fixed-host anonymous Gumtree reads on the existing US private service.

Independent durable allowance; shared concurrency with Willhaben. No external
proxy, login, cookies, paid extraction or arbitrary destination is accepted.
"""
import asyncio
from datetime import datetime, timezone
import json
import math
import re
import time
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import httpx
from config import Config
from provider_state import store
from work_context import work_kind
from willhaben_reader import ReaderUnavailable, _base_url, _LIMIT, MAX_BODY, MAX_WIRE, REGION

VERSION = 'gumtree-reader-v1'


def _target(url):
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or (p.hostname or '').lower() not in ('gumtree.com', 'www.gumtree.com')
                or p.username or p.password or p.port not in (None, 443) or p.fragment):
            return None
        if p.path == '/search':
            params = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
            if len(params) != 2 or len(dict(params)) != 2:
                return None
            values = dict(params)
            keyword = values.get('q', '')
            if (values.get('search_category') != 'all' or not keyword.strip()
                    or len(keyword.encode('utf-16-le')) // 2 > 400):
                return None
            return {'kind': 'search', 'keyword': keyword}
        if p.query or not re.fullmatch(r'/p/(?:[^/]+/)+[0-9]+/?', p.path):
            return None
        for segment in p.path.strip('/').split('/'):
            if re.search(r'%(?![0-9a-fA-F]{2})', segment):
                return None
            value = unquote(segment, errors='strict')
            if (not value or unicodedata.category(value[0])[0] not in ('L', 'N')
                    or any(c not in '_-' and unicodedata.category(c)[0] not in ('L', 'N', 'M') for c in value)):
                return None
        return {'kind': 'item', 'path': p.path}
    except (ValueError, TypeError, UnicodeError):
        return None


def item_id(url):
    try:
        p = urlsplit(url)
        target = _target(urlunsplit((p.scheme, p.netloc, p.path, '', '')))
        return target['path'].rstrip('/').split('/')[-1] if target and target['kind'] == 'item' else ''
    except (ValueError, TypeError):
        return ''


def _same_target(expected, observed):
    actual = _target(observed)
    if not actual or actual['kind'] != expected['kind']:
        return False
    if actual['kind'] == 'search':
        return actual == expected
    return actual['path'].rstrip('/').split('/')[-1] == expected['path'].rstrip('/').split('/')[-1]


def _reserve(auxiliary, now=None, *, provider='gumtree_reader'):
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        def used(name, since):
            return conn.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>=?',
                                (name, since)).fetchone()[0]
        if (used(provider, now - 31 * 86400) >= 6200 or used(provider, day) >= 200
                or auxiliary and (used(provider+':aux', day) >= 160
                                  or used(provider+':aux', now - 31 * 86400) >= 4960)):
            conn.rollback()
            return False
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
                         [(provider, now, 1), (provider+':aux' if auxiliary else provider+':core', now, 1)])
        conn.commit()
        return True


async def fetch(url, client=None, *, auxiliary=None, timeout=15):
    configured = getattr(Config, 'WILLHABEN_READER_URL', '')
    if not configured:
        return None
    base, original = _base_url(configured), _target(url)
    if not base or not original:
        raise ReaderUnavailable('Gumtree reader request invalid')
    is_aux = True if original['kind'] == 'item' else (work_kind.get() != 'core' if auxiliary is None else bool(auxiliary))
    async def perform(c):
        current, visited = url, set()
        for hop in range(4):
            normalized = str(httpx.URL(current))
            if normalized in visited:
                raise ReaderUnavailable('Gumtree reader redirect loop')
            visited.add(normalized)
            target = _target(current)
            if not target or not _same_target(original, current):
                raise ReaderUnavailable('Gumtree reader redirect target invalid')
            if not await asyncio.to_thread(_reserve, is_aux):
                raise ReaderUnavailable('Gumtree reader allowance unavailable')
            result = await _read(c, base, target, timeout)
            if not 300 <= result[0] < 400:
                return result
            if (hop == 3 or result[0] not in (301, 302, 303, 307, 308)
                    or not isinstance(result[3], str) or not _same_target(original, result[3])):
                raise ReaderUnavailable('Gumtree reader redirect target invalid')
            current = result[3]
    try:
        async with asyncio.timeout(timeout):
            async with _LIMIT:
                if client is not None:
                    return await perform(client)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as own:
                    return await perform(own)
    except asyncio.CancelledError:
        raise
    except ReaderUnavailable:
        raise
    except Exception:
        raise ReaderUnavailable('Gumtree reader unavailable') from None


async def _read(client, base, target, timeout):
    async with client.stream('POST', base + '/read-gumtree', json=target, follow_redirects=False, timeout=timeout,
                             headers={'X-Gumtree-Reader-Version': '1'}) as response:
        if response.status_code != 200:
            raise ReaderUnavailable('Gumtree reader response unavailable')
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_WIRE:
                raise ReaderUnavailable('Gumtree reader response invalid')
            chunks.append(chunk)
    try:
        data = json.loads(b''.join(chunks))
        if (not isinstance(data, dict) or set(data) != {'version', 'requested', 'url', 'status', 'body', 'checked', 'region', 'location'}
                or data['version'] != VERSION or data['region'] != REGION
                or not isinstance(data['requested'], str) or not _same_target(target, data['requested'])
                or not isinstance(data['url'], str) or not _same_target(target, data['url'])
                or type(data['status']) is not int or not 100 <= data['status'] <= 599
                or not isinstance(data['body'], str) or len(data['body'].encode('utf-8')) > MAX_BODY
                or data['location'] is not None and not isinstance(data['location'], str)
                or type(data['checked']) not in (int, float) or not math.isfinite(data['checked'])
                or abs(time.time() - data['checked']) > 120):
            raise ValueError('invalid')
        return data['status'], data['body'], data['url'], data['location']
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ReaderUnavailable('Gumtree reader response invalid') from None
