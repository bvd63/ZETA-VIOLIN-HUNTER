"""Offline regressions for auction deadline and identity recovery."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from scrapers.govdeals import (AllSurplusScraper, GovDealsScraper, GovDealsClient,
    IncompleteAuctionDetails, auction_end, asset_closed_reason, parse_asset)

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
ASSET = {'assetId': 42, 'accountId': 32464, 'assetShortDesc': 'Zeta electric violin',
         'assetAuctionEndDate': '2026-11-01T12:00:00', 'assetStatusCd': 'AUC'}
ROW = {'assetId': 42, 'accountId': 32464, 'assetShortDescription': 'Zeta electric violin',
       'assetAuctionEndDateUtc': '2026-10-26T12:00:00', 'currentBid': 10}


class DeadlineTests(unittest.TestCase):
    def test_naive_timezones_are_explicit(self):
        self.assertEqual(auction_end('2026-11-01T12:00:00'), '2026-11-01T17:00:00+00:00')
        self.assertEqual(auction_end('2026-11-01T12:00:00', naive_utc=True), '2026-11-01T12:00:00+00:00')

    def test_primary_future_supersedes_past_row(self):
        item = parse_asset(ASSET, ROW, now=NOW)
        self.assertEqual(item['auction_end'], '2026-11-01T17:00:00+00:00')
        self.assertEqual(item['price'], 'See listing')
        self.assertEqual(asset_closed_reason(ASSET, ROW, now=NOW), '')

    def test_primary_past_supersedes_future_row(self):
        detail = {**ASSET, 'assetAuctionEndDate': '2026-10-26T12:00:00'}
        row = {**ROW, 'assetAuctionEndDateUtc': '2026-11-01T12:00:00'}
        self.assertEqual(parse_asset(detail, row, now=NOW), {})
        self.assertTrue(asset_closed_reason(detail, row, now=NOW))

    def test_row_fallback_and_conflicting_row_unknown(self):
        detail = {**ASSET, 'assetAuctionEndDate': 'invalid'}
        row = {**ROW, 'assetAuctionEndDateUtc': '2026-11-01T12:00:00'}
        self.assertEqual(parse_asset(detail, row, now=NOW)['auction_end'], '2026-11-01T12:00:00+00:00')
        row['_deadline_conflict'] = True
        self.assertEqual(parse_asset(detail, row, now=NOW), {})
        self.assertEqual(asset_closed_reason(detail, row, now=NOW), '')
        self.assertEqual(asset_closed_reason({}, ROW, now=NOW), '')

    def test_mismatched_detail_never_applies_other_status(self):
        for field in ('assetId', 'accountId'):
            with self.subTest(field=field):
                detail = {**ASSET, field: 999, 'assetStatusCd': 'SOLD'}
                with self.assertRaises(IncompleteAuctionDetails):
                    asset_closed_reason(detail, ROW, now=NOW)
                with self.assertRaises(IncompleteAuctionDetails):
                    parse_asset(detail, ROW, now=NOW)


class AsyncRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_inline_public_config_restores_both_search_businesses(self):
        for business, site in (('GD', 1), ('AD', 2)):
            calls=[]
            config={'maestroUrl':'https://maestro.lqdt1.com','maestroApiKey':'public-fixture-key'}
            def handler(request):
                calls.append(request)
                if request.url.host=='www.govdeals.com':
                    return httpx.Response(200,text='<script>window.__APP_CONFIG__='+json.dumps(config)+';</script><script src="/main.new.js"></script>')
                self.assertEqual(request.url.host,'maestro.lqdt1.com')
                self.assertEqual(request.headers['x-api-key'],'public-fixture-key')
                payload=json.loads(request.content)
                self.assertEqual((payload['businessId'],payload['siteId']),(business,site))
                self.assertEqual(payload['searchText'],'zeta')
                return httpx.Response(200,json={'assetSearchResults':[ROW]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                api=GovDealsClient(http,business,site)
                await api.initialize()
                self.assertEqual(await api.search('zeta'),[ROW])
            self.assertEqual(len(calls),2)  # No obsolete JS download or extra search.

    async def test_inline_config_cannot_change_api_destination_or_inject_headers(self):
        for changed in ({'maestroUrl':'https://example.org'}, {'maestroUrl':'http://maestro.lqdt1.com'},
                        {'maestroUrl':'https://maestro.lqdt1.com.evil.example'},
                        {'maestroUrl':'https://maestro.lqdt1.com/path'},
                        {'maestroUrl':'https://user@maestro.lqdt1.com'},
                        {'maestroApiKey':'key\r\nInjected: true'}, {'maestroApiKey':None}):
            with self.subTest(changed=changed):
                config={'maestroUrl':'https://maestro.lqdt1.com','maestroApiKey':'public-fixture',**changed}
                def handler(request):
                    self.assertEqual(request.url.host,'www.govdeals.com')
                    return httpx.Response(200,text='<script>globalThis.__APP_CONFIG__='+json.dumps(config)+';</script>')
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                    with self.assertRaisesRegex(ValueError,'configuration invalid'):
                        await GovDealsClient(http).initialize()

    async def test_invalid_inline_is_never_evaluated_or_silently_accepted(self):
        for content in ('fetch("https://example.org")', '{broken}', 'null', '[]'):
            def handler(request):
                return httpx.Response(200,text='<script>window.__APP_CONFIG__='+content+';</script>')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                with self.assertRaisesRegex(ValueError,'configuration invalid'):
                    await GovDealsClient(http).initialize()

    async def test_original_public_script_configuration_still_works(self):
        calls=[]
        def handler(request):
            calls.append(request.url.path)
            text='<script src="/main.old.js"></script>' if request.url.path=='/' else 'maestroApiKey:"old-public-fixture"'
            return httpx.Response(200,text=text)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            api=GovDealsClient(http)
            await api.initialize()
            self.assertEqual(api.headers['x-api-key'],'old-public-fixture')
        self.assertEqual(calls,['/','/main.old.js'])

    async def test_api_mismatch_and_empty_responses_are_incomplete(self):
        for status, payload in ((204, None), (200, {}), (200, {'assetId': 43, 'accountId': 32464, 'assetStatusCd': 'SOLD'}),
                                (200, {'assetId': 42, 'accountId': 9, 'assetStatusCd': 'SOLD'})):
            with self.subTest(status=status, payload=payload):
                async def handler(request):
                    return httpx.Response(status, json=payload) if status != 204 else httpx.Response(204)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                    with self.assertRaises(IncompleteAuctionDetails):
                        await GovDealsClient(http).asset('42', '32464')

    async def test_duplicate_order_and_namespaces(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        old = {**ROW, 'assetAuctionEndDateUtc': past}
        new = {**ROW, 'assetAuctionEndDateUtc': future}
        for cls in (GovDealsScraper, AllSurplusScraper):
            for rows in ([old, new], [new, old]):
                with self.subTest(cls=cls.__name__, reverse=rows[0] == new):
                    scraper = cls()
                    selected = []
                    def fake_stage(namespace, candidates, limit):
                        self.assertEqual(namespace, scraper.name)
                        self.assertEqual(limit, 30)
                        selected.extend(candidates)
                        return candidates, 0
                    # No detail deadline: conflict must remain unknown and retry.
                    detail = {**ASSET, 'assetAuctionEndDate': None}
                    with patch('scrapers.govdeals.market_queries', return_value=['q'] * 6), \
                         patch.object(GovDealsClient, 'initialize', AsyncMock()), \
                         patch.object(GovDealsClient, 'search', AsyncMock(return_value=rows)) as search, \
                         patch.object(GovDealsClient, 'asset', AsyncMock(return_value=detail)) as asset, \
                         patch('scrapers.govdeals.stage', side_effect=fake_stage), \
                         patch('scrapers.govdeals.complete') as complete:
                        self.assertEqual(await scraper.search(), [])
                    self.assertEqual(search.await_count, 6)
                    self.assertEqual(asset.await_count, 1)
                    self.assertEqual(len(selected), 1)
                    self.assertEqual(selected[0]['assetAuctionEndDateUtc'], future)
                    self.assertTrue(selected[0]['_deadline_conflict'])
                    self.assertEqual(complete.call_args.args[2], 3600)
                    self.assertEqual(scraper.coverage_stats['detail_unverified'], 1)
                    self.assertIn('allsurplus.com' if cls is AllSurplusScraper else 'govdeals.com', selected[0]['url'])

    async def test_scraper_retries_mismatched_detail(self):
        scraper = GovDealsScraper()
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        row = {**ROW, 'assetAuctionEndDateUtc': future}
        with patch('scrapers.govdeals.market_queries', return_value=['q']), \
             patch.object(GovDealsClient, 'initialize', AsyncMock()), \
             patch.object(GovDealsClient, 'search', AsyncMock(return_value=[row])), \
             patch.object(GovDealsClient, 'asset', AsyncMock(return_value={**ASSET, 'assetId': 99, 'assetStatusCd': 'SOLD'})), \
             patch('scrapers.govdeals.stage', side_effect=lambda n, c, l: (c, 0)), \
             patch('scrapers.govdeals.complete') as complete:
            self.assertEqual(await scraper.search(), [])
        self.assertEqual(complete.call_args.args[2], 3600)
        self.assertEqual(scraper.coverage_stats['detail_unverified'], 1)


class QueryTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_attempts_include_failed_pages_and_keep_the_original_plan(self):
        # Before this regression, real API searches incorrectly reported zero
        # fresh queries. HTTP success and raw counts are different evidence.
        for cls in (GovDealsScraper, AllSurplusScraper):
            with self.subTest(platform=cls.name):
                scraper=cls();scraper.reset_health()
                async def rows(keyword,page):
                    if (keyword,page)==('Jazz Fusion',1):raise ValueError('simulated API failure')
                    if (keyword,page)==('zeta',1):return [ROW]*100
                    return [ROW] if keyword=='zeta' else []
                with patch('scrapers.govdeals.market_queries',return_value=['zeta','Jazz Fusion']) as terms, \
                     patch.object(GovDealsClient,'initialize',AsyncMock()), \
                     patch.object(GovDealsClient,'search',AsyncMock(side_effect=rows)) as search, \
                     patch('scrapers.govdeals.stage',return_value=([],0)):
                    self.assertEqual(await scraper.search(),[])
                terms.assert_called_once_with(broad=True,limit=6,extra=('electric violin',))
                self.assertEqual([c.args for c in search.await_args_list],
                                 [('zeta',1),('zeta',2),('Jazz Fusion',1),('Jazz Fusion',2)])
                self.assertEqual(scraper.discovery_queries_attempted,4)
                self.assertEqual(scraper.coverage_stats['query_responses'],[
                    {'keyword':'zeta','page':1,'rows':100},
                    {'keyword':'zeta','page':2,'rows':1},
                    {'keyword':'Jazz Fusion','page':2,'rows':0}])
                self.assertIn('auction search ValueError',scraper.content_failures)


if __name__ == '__main__':
    unittest.main()
