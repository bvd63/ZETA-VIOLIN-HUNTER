"""Anonymous upcoming music-auction inventory, independent of paid indexes.

Calendar -> all catalog pages -> durable individual lots, without a title gate.
Readability was verified from Railway with Chromium on 2026-10-27. A missing
preview is explicit, not an assertion that an unpublished catalog is empty.
"""
import logging
import re
import time
import httpx
from datetime import datetime
from urllib.parse import urljoin, urlsplit, parse_qs
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from browser_fetch import PublicSearchBrowser
from config import Config
from discovery_queue import DiscoveryQueue, complete
from filters import has_zeta_signal, has_violin_word
from offer_verifier import inspect_page
from scrapers.base import BaseScraper, BROWSER_HEADERS

log = logging.getLogger(__name__)
HOME = 'https://www.musicalinstrument-auctions.co.uk'
CALENDAR = HOME + '/auctions'
PAGE_NS = 'Gardiner:catalog-pages'
LOT_NS = 'search:Gardiner Houlgate'
CATALOG = re.compile(r'^/sale/\d+/[^/]+/?$')
LOT = re.compile(r'^/sale/(\d+)/\d+/[^/]+/?$')
DATE = re.compile(r'Sale Date:\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})', re.I)


def own_url(url: str) -> bool:
    return urlsplit(url).hostname in ('www.musicalinstrument-auctions.co.uk', 'musicalinstrument-auctions.co.uk')


def calendar_catalogs(text: str, today=None) -> tuple[list, int, list]:
    soup = BeautifulSoup(text, 'lxml')
    if not soup.find(['h1', 'h2'], string=re.compile(r'^\s*Upcoming Auctions\s*$', re.I)):
        raise ValueError('Gardiner calendar layout/access not recognized')
    today = today or datetime.now(ZoneInfo('Europe/London')).date()
    catalogs, waiting, recognized, errors = [], 0, 0, []
    for heading in soup.find_all(['h2', 'h3']):
        box = heading.parent
        match = None
        card = None
        while box and box.name not in ('body', 'html', '[document]'):
            if len(box.find_all(['h2', 'h3'])) > 1:
                break  # never borrow a sale date from another card/footer
            card = box
            match = DATE.search(box.get_text(' ', strip=True))
            if match:
                break
            box = box.parent
        if card is None:
            continue
        box = card
        links = [urljoin(CALENDAR, a['href']) for a in box.select('a[href]')]
        links = [u for u in links if own_url(u) and CATALOG.fullmatch(urlsplit(u).path)]
        if not match:
            if links:
                recognized += 1
                errors.append('Gardiner calendar sale date unparsed; catalog retained')
                catalogs.extend({'url':u, 'title':heading.get_text(' ', strip=True)} for u in dict.fromkeys(links))
            continue
        try:
            date = datetime.strptime(' '.join(match.groups()), '%d %B %Y').date()
        except ValueError:
            recognized += 1
            errors.append('Gardiner calendar sale date invalid; catalog retained')
            catalogs.extend({'url':u, 'title':heading.get_text(' ', strip=True)} for u in dict.fromkeys(links))
            continue
        recognized += 1
        if date < today:
            continue  # calendar can briefly retain a sale after its date
        if links:
            catalogs.extend({'url':u, 'title':heading.get_text(' ', strip=True),
                             '_auction_date':date.isoformat()} for u in dict.fromkeys(links))
        elif re.search(r'preview available soon', box.get_text(' ', strip=True), re.I):
            waiting += 1
        else:
            errors.append('Gardiner announced sale without readable catalog/preview state')
    if not recognized and not re.search(r'no upcoming auctions', soup.get_text(' ', strip=True), re.I):
        raise ValueError('Gardiner calendar sales not parsed')
    return catalogs, waiting, errors


def catalog_links(text: str, url: str) -> tuple[list, list]:
    soup = BeautifulSoup(text, 'lxml')
    sale = urlsplit(url).path.split('/')[2]
    lots, pages, seen = [], [], set()
    for a in soup.select('a[href]'):
        try:
            target = urljoin(url, a['href'])
            parts = urlsplit(target)
        except ValueError:
            continue
        if not own_url(target):
            continue
        match = LOT.fullmatch(parts.path)
        if match and match.group(1) == sale:
            target = target.split('?', 1)[0].split('#', 1)[0]
            if target in seen:
                continue
            seen.add(target)
            title = a.get_text(' ', strip=True)
            if not title and a.find('img'):
                title = str(a.find('img').get('alt') or '')
            title = title or parts.path.rsplit('/', 1)[-1].replace('-', ' ')
            lots.append({'url':target, 'id':BaseScraper()._make_id('gardiner',target),
                'title':title[:300], 'description':'', 'platform':'Gardiner Houlgate',
                'source':'search', 'price':'See auction lot', 'location':'United Kingdom',
                '_discovered_via':url,
                '_discovery_priority':0 if has_zeta_signal(title) else 1 if has_violin_word(title) else 2})
        elif parts.path.rstrip('/') == urlsplit(url).path.rstrip('/'):
            page = parse_qs(parts.query).get('page', [''])[0]
            if page.isdigit():
                pages.append({'url':HOME + parts.path + '?page=' + page, 'title':'Gardiner catalog page'})
    if not lots and not re.search(r'no (?:lots|results) (?:found|available)', soup.get_text(' ', strip=True), re.I):
        raise ValueError('Gardiner catalog item links not parsed')
    return lots, list({p['url']:p for p in pages}.values())


class GardinerHoulgateScraper(BaseScraper):
    name = 'Gardiner Houlgate'

    async def search(self) -> list:
        results = []
        deadline = time.monotonic() + min(180, max(30, Config.SCRAPER_TIMEOUT_SEC - 35))
        async with self.make_client(headers=BROWSER_HEADERS) as client, PublicSearchBrowser() as browser:
            async def fetch(url):
                before, ok, failures = self.requests_attempted, self.requests_ok, len(self.failures)
                try:
                    response = await client.get(url, timeout=20)
                    if response.status_code == 200:
                        return response.text
                except httpx.HTTPError:
                    pass  # an independent public browser can recover transport errors too
                status, text = await browser.fetch(url)
                if status != 200:
                    raise ValueError('Gardiner public browser HTTP ' + str(status))
                self.requests_recovered += max(0, self.requests_attempted-before-(self.requests_ok-ok))
                del self.failures[failures:]
                return text

            self.record_discovery_query('upcoming music auction calendar; all models and catalog lots')
            catalogs, waiting, warnings = calendar_catalogs(await fetch(CALENDAR))
            self.content_failures.extend(warnings)
            self.coverage_stats = {'catalogs':len(catalogs), 'previews_unpublished':waiting,
                                   'catalog_pages_checked':0, 'detail_checked':0}
            with DiscoveryQueue(PAGE_NS) as queue:
                queue.put(catalogs)
                for _ in range(25):
                    if time.monotonic() > deadline-45:
                        break
                    page = queue.claim(1)
                    if not page:
                        break
                    page = page[0]
                    if page.get('_auction_date') and page['_auction_date'] < datetime.now(ZoneInfo('Europe/London')).date().isoformat():
                        queue.finish(page, 365*86400)
                        continue
                    try:
                        lots, pages = catalog_links(await fetch(page['url']), page['url'])
                        self.fetched += len(lots)
                        self.coverage_stats['catalog_pages_checked'] += 1
                        with DiscoveryQueue(LOT_NS) as items:
                            items.put(lots)
                        queue.put([{**p, '_auction_date':page.get('_auction_date', '')} for p in pages])
                        queue.finish(page, 10*3600)
                    except Exception as exc:
                        queue.finish(page, 3600)
                        self.content_failures.append('catalog page ' + type(exc).__name__)
                self.coverage_stats['catalog_pages_deferred'] = queue.due_count()
            with DiscoveryQueue(LOT_NS) as queue:
                for item in queue.claim(12):  # batch reserves oldest slots AND respects priority
                    if time.monotonic() > deadline-45:
                        break
                    try:
                        state, why = inspect_page(item, await fetch(item['url']), 200, item['url'])
                        self.coverage_stats['detail_checked'] += 1
                        if state == 'live':
                            item['verification'], item['liveness'] = state, why
                            results.append(item)  # ordinary shared classifier decides identity
                        queue.finish(item, 3600 if state == 'unknown' else 10*3600)
                    except Exception as exc:
                        queue.finish(item, 3600)
                        self.content_failures.append('lot detail ' + type(exc).__name__)
                self.coverage_stats['detail_deferred'] = queue.due_count()
        log.info('Gardiner calendar/catalog coverage: %s', self.coverage_stats)
        return results
