"""Additive free whole-France discovery; original web/Actor routes stay intact."""
import asyncio
import logging

from keywords import market_queries
from leboncoin_native import NativeClient, PAGE_SIZE, normalize_native
from native_candidates import card_hints, detail_priority
from discovery_queue import DiscoveryQueue
from provider_state import read, write
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)


class LeboncoinNativeScraper(BaseScraper):
    name = 'Leboncoin (native free)'
    search_engine = True  # existing durable primary-detail queue and fairness

    async def search(self):
        if not hasattr(self, 'failures'):
            self.reset_health()
        results, seen = [], set()
        self.coverage_stats = {'provider': 'native public API', 'country': 'FR',
                               'queries': [], 'raw_rows': 0, 'distinct_cards': 0}
        queries = market_queries('fr', broad=True, limit=8)
        try:
            async with NativeClient(self) as client:
                for keyword in queries:
                    cursor_key = 'leboncoin:native:offset:' + keyword
                    saved = read(cursor_key, PAGE_SIZE)
                    cursor = saved if type(saved) is int and saved >= PAGE_SIZE else PAGE_SIZE
                    try:
                        # Always refresh newest; one rotating page retains older
                        # inventory without an age cutoff or endless pagination.
                        offsets = [0]
                        total = None
                        for offset in offsets:
                            self.record_discovery_query(f'{keyword!r} country=FR category=30 body=true offset={offset}')
                            rows, total = await client.search(keyword, offset)
                            self.fetched += len(rows)
                            self.coverage_stats['raw_rows'] += len(rows)
                            self.coverage_stats['queries'].append({'keyword': keyword, 'offset': offset, 'total': total, 'rows': len(rows)})
                            page_items = []
                            for row in rows:
                                item = normalize_native(row)
                                if not item:
                                    self.content_failures.append('Leboncoin native card identity/schema invalid')
                                    continue
                                hints = card_hints(item['title'], item['description'], keyword)
                                if hints is None or item['_leboncoin_id'] in seen:
                                    continue
                                seen.add(item['_leboncoin_id'])
                                item.update(hints)
                                item.update({'id': self._make_id('leboncoin', item['_leboncoin_id']),
                                             'source': 'search', '_direct_item_candidate': True,
                                             '_query_identity_unverified': True,
                                             'relevance_score': self._relevance_score(item['title'], item['description'])})
                                item['_discovery_priority'] = detail_priority(item)
                                results.append(item)
                                page_items.append(item)
                            # Save candidates BEFORE advancing the older-page
                            # cursor or requesting the next term. Cancellation
                            # must not lose a page already received successfully.
                            with DiscoveryQueue('search:' + self.name) as queue:
                                queue.put(page_items)
                            if offset == 0 and total > PAGE_SIZE:
                                offsets.append(cursor if cursor < total else PAGE_SIZE)
                            if offset > 0:
                                write(cursor_key, offset + PAGE_SIZE if offset + len(rows) < total else PAGE_SIZE)
                            await asyncio.sleep(.5)
                        if total is not None and total > PAGE_SIZE * 2:
                            self.content_failures.append('Leboncoin native older pages retained in rotating cursor; partial inventory')
                    except Exception as exc:
                        self.content_failures.append('Leboncoin native query: ' + str(exc)[:100])
                        # Do not repeat eight blocked requests; all unattempted
                        # terms remain in the plan for the next natural run.
                        if isinstance(exc, RuntimeError):
                            break
        except Exception as exc:
            self.content_failures.append('Leboncoin native access: ' + type(exc).__name__)
        self.coverage_stats['distinct_cards'] = len(results)
        self.coverage_stats['requested_keywords'] = queries
        if len({q['keyword'] for q in self.coverage_stats['queries']}) < len(self.coverage_stats['requested_keywords']):
            self.content_failures.append('Leboncoin native unattempted keywords; coverage incomplete')
        return results
