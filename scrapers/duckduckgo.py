"""Anonymous first-page DuckDuckGo HTML discovery; item pages remain unverified."""
import asyncio
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from config import Config
from database import canonical_url
from offer_verifier import public_url, index_card_negative
from provider_state import claim_window, read, write
from scrapers.base import BaseScraper, BROWSER_HEADERS
from scrapers.search_reserves import plan
from source_policy import is_disabled_source

SEARCH_URL = 'https://html.duckduckgo.com/html/'
DDG_HOSTS = {'duckduckgo.com', 'www.duckduckgo.com', 'html.duckduckgo.com'}


def _destination(href: str) -> str:
    try:
        url = urljoin(SEARCH_URL, href or '')
        parts = urlsplit(url)
        if (parts.hostname or '').lower() in DDG_HOSTS:
            if parts.path.rstrip('/') != '/l':
                return ''
            values = parse_qs(parts.query).get('uddg', [])
            if len(values) != 1:
                return ''
            url = values[0]
        if (urlsplit(url).hostname or '').lower() in DDG_HOSTS:
            return ''
        if not public_url(url) or is_disabled_source(url):
            return ''
        return url
    except (ValueError, TypeError):
        return ''


def parse_results(content: str, query: str) -> tuple:
    """Return parsed cards and count; reject challenges and ambiguous empty pages."""
    soup = BeautifulSoup(content, 'lxml')
    if soup.select_one('.anomaly-modal, #anomaly-modal, form[action*="anomaly"], #challenge-running'):
        raise ValueError('challenge page')
    inputs = soup.select('input[name="q"]')
    titles = soup.find_all('title')
    if (not inputs and not titles
            or any(str(inp.get('value', '')) != query for inp in inputs)
            or any(title.get_text(' ', strip=True) != query + ' at DuckDuckGo' for title in titles)):
        raise ValueError('query echo missing or mismatched')
    anchors = soup.select('.result .result__a')
    if not anchors:
        empty = soup.select_one('.no-results, .results--zero')
        if empty and re.search(r'\bno results found\b', empty.get_text(' ', strip=True), re.I):
            return [], 0
        raise ValueError('unrecognized or incomplete result layout')
    results = []
    for anchor in anchors:
        title = anchor.get_text(' ', strip=True)
        url = _destination(anchor.get('href', ''))
        if not title or not url:
            continue
        card = anchor.find_parent(class_='result')
        snippet = card.select_one('.result__snippet') if card else None
        description = snippet.get_text(' ', strip=True) if snippet else ''
        result = {'url': url, 'title': title, 'description': description}
        result.update(index_card_negative(url, description))
        results.append(result)
    return results, len(anchors)


class DuckDuckGoScraper(BaseScraper):
    name = 'DuckDuckGo Search'
    search_engine = True

    async def search(self) -> list:
        if not hasattr(self, 'content_failures'):
            self.reset_health()
        seconds = getattr(Config, 'DUCKDUCKGO_GUARD_HOURS', 10) * 3600
        auxiliary = getattr(self, 'auxiliary_work', False) or getattr(self, 'manual_fresh', False)
        key = 'duckduckgo:aux:last_run' if auxiliary else 'duckduckgo:last_run'
        if (auxiliary and time.time()-float(read('duckduckgo:last_run',0) or 0) < seconds
                or not claim_window(key, seconds)):
            self.skipped = True
            return []
        cursor = int(read('duckduckgo:cursor', 0) or 0)
        count = max(0, int(getattr(Config, 'DUCKDUCKGO_QUERIES_PER_RUN', 8)))
        candidates, seen = [], {}
        async with self.make_client(timeout=20, headers=BROWSER_HEADERS) as client:
            for index, (terms, hosts) in enumerate(plan(cursor, count)):
                if index:
                    await asyncio.sleep(1)
                query = f"({terms}) (" + ' OR '.join('site:' + host for host in hosts) + ')' if hosts else terms
                try:
                    self.record_discovery_query(query)
                    response = await client.post(SEARCH_URL, data={'q': query})
                    if response.status_code != 200:
                        self.content_failures.append(f'DuckDuckGo HTTP {response.status_code}; query retained')
                        break
                    rows, raw = parse_results(response.text, query)
                    self.fetched += raw
                    for row in rows:
                        key = canonical_url(row['url'])
                        if key in seen:
                            if row.get('_indexed_availability_negative'):
                                seen[key]['_indexed_availability_url'] = row['_indexed_availability_url']
                                seen[key]['_indexed_availability_negative'] = row['_indexed_availability_negative']
                            continue
                        item = {**row, 'id': self._make_id('duckduckgo', key),
                            'platform': urlsplit(row['url']).hostname or self.name,
                            'price': 'See listing', 'location': 'Unknown', 'source': 'search',
                            '_native_search_query': query}
                        seen[key] = item
                        candidates.append(item)
                    write('duckduckgo:cursor', cursor + index + 1)
                except Exception as exc:
                    self.content_failures.append('DuckDuckGo search unresolved: ' + type(exc).__name__)
                    break
        return candidates
