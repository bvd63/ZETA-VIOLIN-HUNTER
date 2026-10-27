"""Offline DuckDuckGo discovery contract; no network or paid providers."""
import asyncio
import html
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import httpx
import database
from provider_state import read
from scrapers.duckduckgo import DuckDuckGoScraper, parse_results
from scrapers.search_reserves import plan


def page(query, body):
    return '<html><head><title>' + html.escape(query) + ' at DuckDuckGo</title></head><body>' + body + '</body></html>'


def card(url, title='Zeta violin', snippet='Used Zeta violin'):
    return '<div class="result"><a class="result__a" href="' + html.escape(url, quote=True) + '">' + title + '</a><div class="result__snippet">' + snippet + '</div></div>'


class ParserTests(unittest.TestCase):
    def test_query_echo_wrappers_private_and_associated_snippets(self):
        query = 'Zeta Jazz violin'
        wrapped = '//duckduckgo.com/l/?' + urlencode({'uddg':'https://example.com/item/123'})
        bad = '//duckduckgo.com/l/?' + urlencode({'uddg':'http://127.0.0.1/secret'})
        rows, raw = parse_results(page(query, card(wrapped) + card(bad) +
            card('https://auctions.yahoo.co.jp/jp/auction/123')), query)
        self.assertEqual(raw, 3)
        self.assertEqual(rows, [{'url':'https://example.com/item/123', 'title':'Zeta violin',
                                 'description':'Used Zeta violin'}])
        with self.assertRaises(ValueError):
            parse_results(page('different query', card(wrapped)), query)
        with self.assertRaises(ValueError):
            parse_results(page(query, '<div class="anomaly-modal">challenge</div>' + card(wrapped)), query)
        with self.assertRaises(ValueError):
            parse_results(page(query, '<p>shell</p>'), query)

    def test_only_explicit_zero_is_a_valid_empty_result(self):
        self.assertEqual(parse_results(page('q','<div class="no-results">No results found for q</div>'), 'q'), ([],0))
        with self.assertRaises(ValueError):
            parse_results(page('q','<div class="no-results">Please retry</div>'), 'q')

    def test_all_present_query_echoes_must_agree(self):
        body = card('https://example.com/item/1')
        for content in (page('q','<input name="q" value="other">'+body),
                        page('other','<input name="q" value="q">'+body),
                        page('q','<input name="q" value="q"><input name="q" value="other">'+body),
                        '<title>q at DuckDuckGo</title><title>other at DuckDuckGo</title>'+body):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_results(content,'q')
        self.assertEqual(len(parse_results(page('q','<input name="q" value="q">'+body),'q')[0]),1)
        self.assertEqual(len(parse_results('<input name="q" value="q">'+body,'q')[0]),1)

    def test_malformed_and_nested_wrappers_do_not_lose_good_card(self):
        nested = 'https://duckduckgo.com/l/?' + urlencode({'uddg': 'https://duckduckgo.com/l/?' +
                                                            urlencode({'uddg': 'https://example.com/item/2'})})
        content = page('q', card('http://[broken') + card(nested) +
                       card('https://duckduckgo.com/marketplace') + card('https://example.com/item/1'))
        rows, raw = parse_results(content, 'q')
        self.assertEqual(raw,4)
        self.assertEqual([r['url'] for r in rows],['https://example.com/item/1'])

    def test_negative_reverb_evidence_is_bound_to_card_and_exact_host(self):
        content = page('q', card('https://reverb.com/item/82035982-x',snippet='This gear has sold') +
            card('https://www.reverb.com/en-ro/item/123-x',snippet='This listing has ended') +
            card('https://reverb.com.evil.example/item/123-x',snippet='This gear has sold') +
            card('https://reverb.com/item/456-x',snippet='Available used violin'))
        rows,_ = parse_results(content,'q')
        self.assertEqual(rows[0]['_indexed_availability_url'],rows[0]['url'])
        self.assertEqual(rows[0]['_indexed_availability_negative'],'sold')
        self.assertEqual(rows[1]['_indexed_availability_negative'],'ended')
        self.assertNotIn('_indexed_availability_negative',rows[2])
        self.assertNotIn('_indexed_availability_negative',rows[3])


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, 'DB_PATH', str(Path(self.tmp.name) / 'ddg.db'))
        self.db_patch.start()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_cursor_advances_only_valid_pages_and_guard_prevents_duplicate_run(self):
        calls = []
        async def handler(request):
            calls.append(request)
            from urllib.parse import parse_qs
            query = parse_qs(request.content.decode())['q'][0]
            if len(calls) == 1:
                return httpx.Response(200, text=page(query, card('https://example.com/item/1')))
            return httpx.Response(200, text=page(query, '<div class="anomaly-modal">challenge</div>'))
        scraper = DuckDuckGoScraper()
        transport = httpx.MockTransport(handler)
        with patch.object(scraper, 'make_client', return_value=httpx.AsyncClient(transport=transport)), \
             patch('scrapers.duckduckgo.asyncio.sleep', new_callable=AsyncMock), \
             patch('scrapers.duckduckgo.Config.DUCKDUCKGO_QUERIES_PER_RUN', 3, create=True):
            results = await scraper.search()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'search')
        self.assertEqual(results[0]['description'], 'Used Zeta violin')
        self.assertEqual(scraper.fetched, 1)
        self.assertEqual(scraper.discovery_queries_attempted, 2)
        self.assertEqual(read('duckduckgo:cursor', 0), 1)
        self.assertTrue(scraper.content_failures)
        second = DuckDuckGoScraper()
        self.assertEqual(await second.search(), [])
        self.assertTrue(second.skipped)
        self.assertEqual(len(calls), 2)

    async def test_http_429_retains_cursor_and_only_ddg_state_is_written(self):
        scraper = DuckDuckGoScraper()
        with patch.object(scraper, 'make_client', return_value=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(429)))), \
             patch('scrapers.duckduckgo.Config.DUCKDUCKGO_QUERIES_PER_RUN', 2, create=True):
            self.assertEqual(await scraper.search(), [])
        self.assertEqual(read('duckduckgo:cursor', 0), 0)
        self.assertTrue(scraper.content_failures)
        conn = database.connect()
        try:
            keys = [row[0] for row in conn.execute('SELECT key FROM provider_state')]
            self.assertTrue(all(key.startswith('duckduckgo:') for key in keys))
        finally:
            conn.close()

    async def test_configured_guard_hours_are_used(self):
        scraper = DuckDuckGoScraper()
        with patch('scrapers.duckduckgo.claim_window',return_value=False) as claim, \
             patch('scrapers.duckduckgo.Config.DUCKDUCKGO_GUARD_HOURS', 3, create=True):
            self.assertEqual(await scraper.search(), [])
        claim.assert_called_once_with('duckduckgo:last_run', 3 * 3600)

    async def test_duplicate_negative_evidence_survives_same_page_and_next_query(self):
        for later_query in (False, True):
            with self.subTest(later_query=later_query):
                calls=[]
                async def handler(request):
                    from urllib.parse import parse_qs
                    query=parse_qs(request.content.decode())['q'][0]
                    calls.append(query)
                    first=card('https://reverb.com/item/82035982-original',snippet='Used violin')
                    second=card('https://www.reverb.com/en-ro/item/82035982-other',snippet='This gear has sold')
                    body=(first if len(calls)==1 else second) if later_query else first+second
                    return httpx.Response(200,text=page(query,body))
                scraper=DuckDuckGoScraper()
                with patch('scrapers.duckduckgo.claim_window',return_value=True), \
                     patch('scrapers.duckduckgo.read',return_value=0), \
                     patch('scrapers.duckduckgo.write'), \
                     patch('scrapers.duckduckgo.asyncio.sleep',new_callable=AsyncMock), \
                     patch.object(scraper,'make_client',return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))), \
                     patch('scrapers.duckduckgo.Config.DUCKDUCKGO_QUERIES_PER_RUN',2 if later_query else 1,create=True):
                    items=await scraper.search()
                self.assertEqual(len(items),1)
                self.assertEqual(items[0]['_indexed_availability_negative'],'sold')
                self.assertEqual(items[0]['_indexed_availability_url'],'https://www.reverb.com/en-ro/item/82035982-other')

    async def test_parallel_guard_allows_one_run(self):
        async def handler(request):
            from urllib.parse import parse_qs
            query = parse_qs(request.content.decode())['q'][0]
            return httpx.Response(200, text=page(query, '<div class="no-results">No results found</div>'))
        client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        first, second = DuckDuckGoScraper(), DuckDuckGoScraper()
        with patch.object(first, 'make_client', side_effect=lambda **kw: client()), \
             patch.object(second, 'make_client', side_effect=lambda **kw: client()), \
             patch('scrapers.duckduckgo.Config.DUCKDUCKGO_QUERIES_PER_RUN', 1, create=True):
            await asyncio.gather(first.search(), second.search())
        self.assertEqual(first.discovery_queries_attempted + second.discovery_queries_attempted, 1)
        self.assertEqual(read('duckduckgo:cursor', 0), 1)
        self.assertTrue(first.skipped or second.skipped)

    async def test_auxiliary_search_cannot_postpone_scheduled_window(self):
        from provider_state import write
        async def handler(request):
            from urllib.parse import parse_qs
            query = parse_qs(request.content.decode())['q'][0]
            return httpx.Response(200,text=page(query,'<div class="no-results">No results found</div>'))
        auxiliary, core = DuckDuckGoScraper(), DuckDuckGoScraper()
        auxiliary.auxiliary_work = True
        with patch('scrapers.duckduckgo.Config.DUCKDUCKGO_QUERIES_PER_RUN',1), \
             patch.object(auxiliary,'make_client',side_effect=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler))), \
             patch.object(core,'make_client',side_effect=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            await auxiliary.search()
            self.assertEqual(read('duckduckgo:last_run',0),0)
            await core.search()
        self.assertEqual(core.discovery_queries_attempted,1)
        self.assertTrue(read('duckduckgo:last_run',0))
        # Even a fresh auxiliary lane must defer after a core run.
        write('duckduckgo:aux:last_run',0)
        another = DuckDuckGoScraper(); another.auxiliary_work = True
        self.assertEqual(await another.search(),[])
        self.assertTrue(another.skipped)

    def test_planner_retains_original_terms_and_host_groups(self):
        terms, hosts = plan(1, 1)[0]
        self.assertTrue(terms)
        self.assertTrue(hosts)
        self.assertIn('facebook.com', hosts)


if __name__ == '__main__':
    unittest.main()
