"""Exa live extraction for blocked public offers; shared existing credit cap."""
import html
import logging
import math
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote

from config import Config
from database import canonical_url
from provider_state import read, write, reserve_work
from work_context import work_kind

log = logging.getLogger(__name__)

MARKET_CACHE_SECONDS = 10 * 3600


def remember_market_search(url: str, result: dict) -> None:
    """Store only a live-crawled result accepted by the marketplace parser.

    The caller invokes this after parsing the result count and item links. A
    cached/indexed/failed extraction cannot refresh this observation's age.
    """
    if result.get('_live_crawled') is not True or result.get('_cached') is True:
        return
    extras = result.get('extras')
    links = extras.get('links') if isinstance(extras,dict) else []
    links = links if isinstance(links,list) else []
    try:
        write('exa_market:page:' + canonical_url(url), {'checked':time.time(), 'result':{
            'url':url, 'title':str(result.get('title') or '')[:300],
            'text':str(result.get('text') or '')[:300000],
            'extras':{'links':[v for v in links if isinstance(v,str)][:300]},
        }})
    except Exception as exc:
        log.warning('Search observation cache unavailable (%s); current results retained', type(exc).__name__)


def cached_market_search(url: str, reason: str) -> dict:
    try:
        saved = read('exa_market:page:' + canonical_url(url), {})
    except Exception:
        return {}  # An optional cache must never stop current discovery.
    if not isinstance(saved,dict):
        return {}
    checked, result = saved.get('checked'), saved.get('result')
    now = time.time()
    try:
        same_url = isinstance(result,dict) and canonical_url(str(result.get('url',''))) == canonical_url(url)
    except (ValueError,TypeError):
        return {}
    if (type(checked) not in (int,float) or not math.isfinite(checked)
            or not 0 <= now-checked <= MARKET_CACHE_SECONDS or not isinstance(result,dict)
            or not same_url
            or not isinstance(result.get('text'),str) or not result['text']):
        return {}
    return {**result, '_cached':True, '_checked_at':checked, '_cache_reason':reason}


def reserve_pages(kind: str) -> bool:
    """Reserve page count and shared dollars atomically; no phantom page slots."""
    if work_kind.get() == 'queue':
        return False  # queue maintenance never launches a paid recovery
    page_rolling,page_daily=(186,6) if kind=='exa_gum_search' else (620,20)
    planned = (Config.EXA_QUERIES_PER_RUN + Config.EXA_EXTRA_QUERIES_PER_RUN) * Config.RUNS_PER_DAY * 31 * 17000 + 1426000
    ok = reserve_work('exa', 1000, int(Config.EXA_ROLLING_BUDGET_USD * 1000000), 250000,
                      planned, auxiliary=work_kind.get() != 'core',
                      counters=((kind, 1, page_rolling, page_daily),))
    if not ok:
        log.warning('Exa %s: local %s reservation guard; candidate retained', kind, work_kind.get())
    return ok


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
    if work_kind.get() != 'core':
        cached = cached_market_search(url, 'auxiliary work uses the last verified search observation')
        if cached:
            return cached
    if not reserve_pages(kind):
        cached = cached_market_search(url, 'current live extraction reservation unavailable')
        if cached:
            return cached
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
            return {**result, '_live_crawled':True}
    except Exception as exc:
        log.warning('%s live extraction unavailable (%s)',market,type(exc).__name__)
    return cached_market_search(url, 'current live extraction unavailable')


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
