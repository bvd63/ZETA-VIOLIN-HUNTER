"""Exa live extraction for blocked public offers; shared existing credit cap."""
import html
import logging
import time
from urllib.parse import urlsplit

from config import Config
from database import canonical_url
from provider_state import read, write, reserve

log = logging.getLogger(__name__)


def supported(url: str) -> bool:
    host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    return host in ('offerup.com', 'invaluable.com', 'auctions.gardinerhoulgate.co.uk', 'ha.com') or host.endswith('.ha.com')


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
    if not reserve('exa_contents', 1, 620, 20):
        return ''
    if not reserve('exa', 1000, int(Config.EXA_ROLLING_BUDGET_USD * 1000000), 250000):
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
