"""Search-only regional reserve. An index response never proves primary stock."""
import asyncio
import json
import math
import time

import httpx
from config import Config
from gumtree_reader import _reserve
from provider_state import read, write
from willhaben_reader import _base_url, _LIMIT, MAX_BODY, MAX_WIRE, REGION, ReaderUnavailable
from work_context import work_kind

API='https://api.leboncoin.fr/finder/search'


async def search(keyword, offset=0, *, observer=None, client=None, timeout=15):
    configured=getattr(Config,'WILLHABEN_READER_URL','')
    if not configured:
        return None
    base=_base_url(configured)
    if (not base or not isinstance(keyword,str) or not keyword.strip()
            or len(keyword.encode('utf-16-le'))//2>400 or type(offset) is not int or not 0<=offset<=2147483647):
        raise ReaderUnavailable('Leboncoin reader request invalid')
    target={'kind':'search','keyword':keyword,'offset':offset}
    try:
        async with asyncio.timeout(timeout):
            async with _LIMIT:
                guard=await asyncio.to_thread(read,'leboncoin_reader:access',{})
                if isinstance(guard,dict) and guard.get('blocked_until',0)>time.time():
                    raise ReaderUnavailable('Leboncoin reader access cooldown; native route remains first')
                if not await asyncio.to_thread(_reserve,work_kind.get()!='core',provider='leboncoin_reader'):
                    raise ReaderUnavailable('Leboncoin reader allowance unavailable')
                if client is not None:
                    return await _read(client,base,target,observer,timeout)
                async with httpx.AsyncClient(timeout=timeout,follow_redirects=False) as own:
                    return await _read(own,base,target,observer,timeout)
    except asyncio.CancelledError:
        raise
    except ReaderUnavailable:
        raise
    except Exception:
        raise ReaderUnavailable('Leboncoin reader unavailable') from None


async def _read(client,base,target,observer,timeout):
    if observer:observer.requests_attempted+=1
    async with client.stream('POST',base+'/read-leboncoin',json=target,follow_redirects=False,timeout=timeout,
                             headers={'X-Leboncoin-Reader-Version':'1'}) as response:
        if response.status_code!=200:
            raise ReaderUnavailable('Leboncoin reader response unavailable')
        chunks=[];size=0
        async for chunk in response.aiter_bytes():
            size+=len(chunk)
            if size>MAX_WIRE:raise ReaderUnavailable('Leboncoin reader response invalid')
            chunks.append(chunk)
    try:
        data=json.loads(b''.join(chunks))
        if (not isinstance(data,dict) or set(data)!={'version','request','requested','url','status','body','checked','region','location'}
                or data['version']!='leboncoin-reader-v1' or data['region']!=REGION
                or not isinstance(data['request'],dict) or data['request']!=target
                or type(data['request'].get('offset')) is not int
                or data['requested']!=API or data['url']!=API
                or type(data['status']) is not int or not 100<=data['status']<=599
                or data['location'] is not None or not isinstance(data['body'],str)
                or len(data['body'].encode())>MAX_BODY
                or type(data['checked']) not in (int,float) or not math.isfinite(data['checked'])
                or abs(time.time()-data['checked'])>120):
            raise ValueError('invalid')
        if data['status']!=200:
            if data['status'] in (403,429):
                await asyncio.to_thread(write,'leboncoin_reader:access',
                    {'checked':time.time(),'blocked_until':time.time()+3600,'status':data['status']})
            raise ReaderUnavailable('Leboncoin reader upstream HTTP '+str(data['status']))
        payload=json.loads(data['body'])
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError):
        raise ReaderUnavailable('Leboncoin reader response invalid') from None
    if observer:
        observer.requests_ok+=1
        if not isinstance(getattr(observer,'coverage_stats',None),dict):observer.coverage_stats={}
        observer.coverage_stats.setdefault('private_us_queries',[]).append({'keyword':target['keyword'],'offset':target['offset']})
    return httpx.Response(200,json=payload,request=httpx.Request('POST',API))
