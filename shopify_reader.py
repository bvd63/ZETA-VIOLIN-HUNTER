"""Two fixed public used collections through the existing private US reader."""
import asyncio
from datetime import datetime, timezone
import json
import math
import time

import httpx
from config import Config
from provider_state import store
from work_context import work_kind
from willhaben_reader import ReaderUnavailable, _base_url, _LIMIT, MAX_BODY, MAX_WIRE, REGION

VERSION = 'shopify-reader-v1'
COLLECTIONS = {
    ('https://electricviolinshop.com','used'): ('evs-used','https://electricviolinshop.com/collections/used/products.json?limit=250'),
    ('https://www.stringworks.com','outlet-preowned-and-trade-in-deals'): ('stringworks-used','https://www.stringworks.com/collections/outlet-preowned-and-trade-in-deals/products.json?limit=250'),
}


def _reserve(auxiliary, now=None):
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        def used(key,since):
            return conn.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>=?',(key,since)).fetchone()[0]
        if (used('shopify_reader',day)>=40 or used('shopify_reader',now-31*86400)>=1240
                or auxiliary and (used('shopify_reader:aux',day)>=32 or used('shopify_reader:aux',now-31*86400)>=992)):
            conn.rollback();return False
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',
            [('shopify_reader',now,1),('shopify_reader:aux' if auxiliary else 'shopify_reader:core',now,1)])
        conn.commit();return True


async def fetch_collection(base, handle, client=None, *, timeout=15, on_attempt=None):
    configured = getattr(Config,'WILLHABEN_READER_URL','')
    if not configured:
        return None
    endpoint = _base_url(configured)
    target = COLLECTIONS.get((base,handle))
    if not endpoint or not target:
        raise ReaderUnavailable('Shopify reader target/config invalid')

    async def read(c):
        if not await asyncio.to_thread(_reserve,work_kind.get()!='core'):
            raise ReaderUnavailable('Shopify reader auxiliary/core allowance unavailable')
        if on_attempt is not None:on_attempt()
        async with c.stream('POST',endpoint+'/read-shopify',json={'collection':target[0]},
                headers={'X-Shopify-Reader-Version':'1'},follow_redirects=False,timeout=timeout) as response:
            if response.status_code!=200:
                raise ReaderUnavailable('Shopify reader service unavailable')
            parts=[];size=0
            async for part in response.aiter_bytes():
                size+=len(part)
                if size>MAX_WIRE:raise ReaderUnavailable('Shopify reader response too large')
                parts.append(part)
        try:
            d=json.loads(b''.join(parts))
            if (not isinstance(d,dict) or set(d)!={'version','requested','url','status','body','checked','region','location'}
                    or d['version']!=VERSION or d['region']!=REGION or d['requested']!=target[1] or d['url']!=target[1]
                    or type(d['status']) is not int or d['status']!=200 or d['location'] is not None
                    or not isinstance(d['body'],str) or len(d['body'].encode())>MAX_BODY
                    or type(d['checked']) not in (int,float) or not math.isfinite(d['checked'])
                    or abs(time.time()-d['checked'])>120):raise ValueError('invalid DTO')
            data=json.loads(d['body'])
            if not isinstance(data,dict) or not isinstance(data.get('products'),list) or len(data['products'])>250:
                raise ValueError('invalid collection')
            return data['products']
        except (ValueError,TypeError,KeyError,UnicodeError,OverflowError):
            raise ReaderUnavailable('Shopify collection response invalid') from None
    try:
        async with asyncio.timeout(timeout):
            async with _LIMIT:
                if client is not None:return await read(client)
                async with httpx.AsyncClient(timeout=timeout,follow_redirects=False) as own:return await read(own)
    except asyncio.CancelledError:raise
    except ReaderUnavailable:raise
    except Exception:raise ReaderUnavailable('Shopify reader unavailable') from None
