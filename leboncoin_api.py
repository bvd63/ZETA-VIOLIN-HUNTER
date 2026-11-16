"""ID-bound observations from the pinned, authenticated Leboncoin Actor."""
import re
import math
import json
import time
from datetime import datetime
from urllib.parse import urlsplit

from keywords import market_queries
from provider_state import read, store

ACTOR = 'q5xh9KX4Wbn9i9cPA'  # automation_craft/leboncoin-scraper, schema reviewed 2026-11-16
HOSTS = {'leboncoin.fr','www.leboncoin.fr'}


def item_id(url):
    try:
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None,443):
            return ''
        match=re.fullmatch(r'/(?:ad/)?[a-z_]+/(\d+)(?:\.htm)?/?',parts.path)
        return match[1] if match else ''
    except (ValueError,TypeError):
        return ''


def actor_input(*, pilot=False):
    return {'keywords':['zeta'] if pilot else market_queries('fr',broad=True,limit=8),
            'locations':[], 'categories':['instruments_de_musique'],
            'adType':'offer',
            'searchInTitleOnly':False,'includeAdDetails':True,
            'includeSellerProfiles':False,'includePersonalData':False,
            'maxItems':10,'maxItemsPerSearch':10,'maxPagesPerSearch':2,'sort':'newest',
            'proxyConfiguration':{'useApifyProxy':True,'apifyProxyGroups':['RESIDENTIAL'],'apifyProxyCountry':'FR'}}


def normalize(row):
    if not isinstance(row,dict) or row.get('type')!='ad' or row.get('adType','offer')!='offer':
        return {}
    url=row.get('url')
    identity=item_id(url)
    if not identity or str(row.get('listId',''))!=identity or not isinstance(row.get('title'),str) or not row['title'].strip():
        return {}
    title=row['title'].strip()
    description=row.get('description') if isinstance(row.get('description'),str) else ''
    price=row.get('price')
    if row.get('currency')!='EUR' or isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<0:
        price=None
    photos=row.get('imageUrls')
    photos=photos if isinstance(photos,list) else []
    status=str(row.get('status','')).lower()
    state='dead' if status in ('sold','expired','deleted','removed','inactive') else (
        'held' if status in ('pending','reserved','hidden') else
        'live' if status=='active' and row.get('detailsIncluded') is True else 'unknown')
    return {'url':url,'title':title,'description':description,
            'price':f'{price:g} EUR' if price is not None else 'N/A',
            'platform':'Leboncoin','location':', '.join(str(x) for x in (row.get('city'),row.get('departmentName'),'France') if x),
            'condition':row.get('condition') if isinstance(row.get('condition'),str) else '',
            'date_posted':str(row.get('publishedAt') or '')[:10],
            'image_url':next((x for x in photos if isinstance(x,str) and x.startswith('https://')),''),
            '_leboncoin_id':identity,'_leboncoin_observed_state':state}


def remember(item, *, checked=None):
    identity=item_id(item.get('url',''))
    if not identity or item.get('_leboncoin_id')!=identity:
        raise ValueError('Leboncoin observation identity mismatch')
    checked=time.time() if checked is None else checked
    if isinstance(checked,bool) or not isinstance(checked,(int,float)) or not math.isfinite(checked) or checked<0:
        raise ValueError('Invalid observation time')
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous_record=_read(conn,'leboncoin:offer:'+identity)
        previous=previous_record.get('listing',{})
        previous_closed=_read(conn,'leboncoin:closed:'+identity)
        state=item.get('_leboncoin_observed_state')
        if (checked<max(previous_record.get('checked',0),previous_closed.get('checked',0))
                or state=='unknown' and previous.get('_leboncoin_observed_state') in ('dead','held')):
            conn.rollback()
            return
        _write(conn,'leboncoin:offer:'+identity,{'checked':checked,'listing':item})
        if state=='live':
            _write(conn,'leboncoin:closed:'+identity,{})
        elif state=='dead':
            _write(conn,'leboncoin:closed:'+identity,{'checked':checked,'reason':'same-ID primary record inactive'})
        conn.commit()


def _read(conn,key):
    row=conn.execute('SELECT value FROM provider_state WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else {}


def _write(conn,key,value):
    conn.execute('INSERT OR REPLACE INTO provider_state VALUES(?,?)',(key,json.dumps(value)))


def remember_status(row, *, checked):
    # A missing result in a search/monitor is not a per-ID availability check.
    if row.get('type')!='status' or row.get('scope')!='ad' or row.get('status')!='not_found':
        return
    key=str(row.get('key') or '')
    identity=key if re.fullmatch(r'\d+',key) else item_id(key)
    if identity:
        with store() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous=_read(conn,'leboncoin:offer:'+identity)
            closed=_read(conn,'leboncoin:closed:'+identity)
            if checked>=max(previous.get('checked',0),closed.get('checked',0)):
                _write(conn,'leboncoin:closed:'+identity,{'checked':checked,'reason':'same-ID primary record not found'})
            conn.commit()


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else 0
    except (ValueError,TypeError,OverflowError):
        return 0


def closed_reason(url):
    identity=item_id(url)
    value=read('leboncoin:closed:'+identity,{}) if identity else {}
    return value.get('reason','') if isinstance(value,dict) else ''


def cached_offer(url, *, now=None):
    identity=item_id(url)
    if not identity:
        return None
    value=read('leboncoin:offer:'+identity,{})
    item=value.get('listing',{})
    if (not isinstance(item,dict) or item_id(item.get('url',''))!=identity or item.get('_leboncoin_id')!=identity
            or isinstance(value.get('checked'),bool) or not isinstance(value.get('checked'),(int,float))
            or not math.isfinite(value['checked'])):
        return None
    age=(time.time() if now is None else now)-value['checked']
    if age<0 or (age>6*3600 and item.get('_leboncoin_observed_state') not in ('dead','held')):
        return None
    return value
