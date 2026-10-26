"""Exa live extraction for blocked public offers; shared existing credit cap."""
import html
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

from config import Config
from database import canonical_url
from provider_state import read, write, store

log = logging.getLogger(__name__)


def reserve_pages(kind: str) -> bool:
    """Reserve page count and shared dollars atomically; no phantom page slots."""
    now=time.time()
    day=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    page_rolling,page_daily=(186,6) if kind=='exa_gum_search' else (620,20)
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for provider,units,rolling_cap,day_cap in (
                (kind,1,page_rolling,page_daily),('exa',1000,int(Config.EXA_ROLLING_BUDGET_USD*1000000),250000)):
            rolling=conn.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>?',
                                 (provider,now-31*86400)).fetchone()[0]
            daily=conn.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>=?',
                               (provider,day)).fetchone()[0]
            if rolling+units>rolling_cap or daily+units>day_cap:
                conn.rollback()
                log.warning('Exa %s: local %s reservation guard; original searches remain configured',kind,
                            'daily' if daily+units>day_cap else 'rolling')
                return False
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)',[(kind,now,1),('exa',now,1000)])
        conn.commit()
    return True


def supported(url: str) -> bool:
    host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    return host in ('offerup.com', 'invaluable.com', 'auctions.gardinerhoulgate.co.uk', 'ha.com', 'willhaben.at', 'gumtree.com') or host.endswith('.ha.com')


async def fresh_willhaben_search(url: str, client) -> dict:
    """Exact public Willhaben query, live text AND actual links, never guessed URLs."""
    return await _fresh_market_search(url,client,'Willhaben')


async def fresh_gumtree_search(url: str, client) -> dict:
    """Last public route for Gumtree; up to six pages/day, shared Exa cap."""
    parts=urlsplit(url)
    # Preserve decoded keywords, using the exact space encoding of the live
    # probe that succeeded. Literal '+' remains %2B, never a lost search term.
    url=urlunsplit((parts.scheme,parts.netloc,parts.path,
                   urlencode(parse_qsl(parts.query,keep_blank_values=True),quote_via=quote),''))
    return await _fresh_market_search(url,client,'Gumtree')


async def _fresh_market_search(url: str, client, market: str) -> dict:
    parts = urlsplit(url)
    host,path,kind=('willhaben.at','/iad/kaufen-und-verkaufen/marktplatz','exa_will_search') if market=='Willhaben' else (
        'gumtree.com','/search','exa_gum_search')
    if ((parts.hostname or '').lower().removeprefix('www.') != host
            or not parts.path.startswith(path) or not Config.EXA_API_KEY):
        return {}
    if not reserve_pages(kind):
        return {'_error':market+': limita locală zilnică/lunară Exa; celelalte surse continuă'}
    try:
        response=await client.post('https://api.exa.ai/contents',headers={'x-api-key':Config.EXA_API_KEY},
            json={'urls':[url],'text':True,'extras':{'links':300},
                  'maxAgeHours':0,'livecrawlTimeout':20000},timeout=30)
        data=response.json()
        status=next((s for s in data.get('statuses',[]) if canonical_url(str(s.get('id','')))==canonical_url(url)),{})
        result=next((r for r in data.get('results',[]) if canonical_url(str(r.get('url','')))==canonical_url(url)),{})
        if (response.status_code==200 and status.get('status')=='success'
                and status.get('source')=='crawled' and isinstance(result.get('text'),str)):
            return result
    except Exception as exc:
        log.warning('%s live extraction unavailable (%s)',market,type(exc).__name__)
    return {}


async def fresh_primary_page(url: str, client) -> str:
    """Return bounded primary HTML extraction only after a successful live crawl.

    At most20 pages/day ($0.020); the original six Exa searches/cycle retain
    their $0.204/day allowance under the unchanged $0.25/$8 shared limits.
    Failure keeps the caller's unknown state, never silently rejects a violin.
    """
    if not Config.EXA_API_KEY or not supported(url):
        return ''
    key = 'exa_contents:page:' + canonical_url(url)
    cached = read(key, {})
    if cached.get('checked', 0) > time.time() - 900 and cached.get('html'):
        return cached['html']
    if not reserve_pages('exa_contents'):
        return ''
    try:
        response = await client.post('https://api.exa.ai/contents',
            headers={'x-api-key': Config.EXA_API_KEY},
            json={'urls':[url], 'text':{'includeHtmlTags':True},
                  'maxAgeHours':0, 'livecrawlTimeout':20000}, timeout=30)
        data = response.json()
        statuses = data.get('statuses') or []
        # HTTP200 can contain failed URLs or stale cached results. Neither
        # proves current availability. Do not follow an unrelated result URL.
        status = next((s for s in statuses if canonical_url(str(s.get('id',''))) == canonical_url(url)), {})
        result = next((r for r in data.get('results',[]) if canonical_url(str(r.get('url',''))) == canonical_url(url)), {})
        if (response.status_code != 200 or status.get('status') != 'success'
                or status.get('source') != 'crawled' or not result.get('text')):
            write('exa_contents:health', {'checked':time.time(), 'error':'live primary extraction unavailable'})
            return ''
        body = '<title>' + html.escape(str(result.get('title',''))) + '</title>' + str(result['text'])[:300000]
        write(key, {'checked':time.time(), 'html':body})
        write('exa_contents:health', {'checked':time.time(), 'error':''})
        log.info('Live Exa primary-page recovery: %s',urlsplit(url).hostname)
        return body
    except Exception as exc:
        log.warning('Live primary extraction unavailable (%s)', type(exc).__name__)
        return ''
