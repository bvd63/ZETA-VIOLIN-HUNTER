"""Offline regressions for auction deadline and identity recovery."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import database
from scrapers.govdeals import (AllSurplusScraper, GovDealsScraper, GovDealsClient,
    IncompleteAuctionDetails, auction_end, asset_closed_reason, parse_asset,
    display_unavailable, display_hold, _remember_display)

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
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(database, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.dbpatch.start()

    async def asyncTearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

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


class DisplayVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(database, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.dbpatch.start()
        self.asset = {**ASSET, 'assetStatusCd': 'STA', 'canDisplayAsset': False,
            'assetLongDesc': 'Used Zeta Jazz Fusion JV5 electric violin with case.',
            'assetAuctionEndDate': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}
        self.url = 'https://www.govdeals.com/en/asset/42/32464'

    async def asyncTearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

    def test_primary_hidden_flag_is_not_live_or_sold_and_legacy_flags_stay_eligible(self):
        self.assertEqual(parse_asset(self.asset), {})
        self.assertEqual(asset_closed_reason(self.asset), '')
        for flag in (None, True, 0, 'false'):
            other = {**self.asset, 'canDisplayAsset': flag}
            self.assertFalse(display_unavailable(other))
            self.assertTrue(parse_asset(other))
        # No guessed status mapping for legacy records.
        self.assertTrue(parse_asset({**self.asset, 'assetStatusCd': 'AUC'}))
        for key in ('assetId', 'accountId'):
            self.assertFalse(display_unavailable({**self.asset, key: None}))

    async def verify(self, payload=None, status=200, url=None):
        from offer_verifier import verify_offer
        item = {'url': url or self.url, 'title': 'Zeta Jazz Fusion JV5',
                'description': 'Used electric violin with case'}
        with patch('offer_verifier.public_dns', AsyncMock(return_value=True)), \
             patch.object(GovDealsClient, 'initialize', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                    httpx.Response(status, json=payload) if status != 204 else httpx.Response(204))) as client:
                result = await verify_offer(item, client)
        return item, result

    async def test_hidden_then_unreadable_refresh_retains_hold_until_current_visible(self):
        from offer_verifier import availability_conflict
        item, result = await self.verify(self.asset)
        self.assertEqual(result[0], 'unknown')
        self.assertTrue(availability_conflict(item, result[0]))
        for status in (204, 403):
            item, result = await self.verify(status=status)
            self.assertEqual(result[0], 'unknown')
            self.assertTrue(availability_conflict(item, result[0]))
        # Missing flag cannot erase the primary negative, even with a future date.
        item, result = await self.verify({k:v for k,v in self.asset.items() if k != 'canDisplayAsset'})
        self.assertEqual(result[0], 'unknown')
        self.assertTrue(availability_conflict(item, result[0]))
        item, result = await self.verify({**self.asset, 'canDisplayAsset': True})
        self.assertEqual(result[0], 'live')
        self.assertFalse(availability_conflict(item, result[0]))
        self.assertFalse(display_hold('42', '32464'))

    async def test_empty_204_without_a_primary_negative_remains_unknown_not_held(self):
        from offer_verifier import availability_conflict
        item, result = await self.verify(status=204)
        self.assertEqual(result[0], 'unknown')
        self.assertFalse(availability_conflict(item, result[0]))
        self.assertFalse(display_hold('42', '32464'))

    async def test_bound_identity_business_and_restart_do_not_share_an_unrelated_hold(self):
        await self.verify(self.asset)
        self.assertTrue(display_hold('42', '32464'))  # A fresh DB connection reads durable state.
        for args in (('43', '32464'), ('42', '999'), ('42', '32464', 'AD', 2)):
            self.assertFalse(display_hold(*args))
        _, result = await self.verify({**self.asset, 'assetId': 99})
        self.assertEqual(result[0], 'unknown')
        self.assertFalse(display_hold('99', '32464'))
        _remember_display({**self.asset, 'assetId': None}, '50', '32464', 'GD', 1, time.time())
        self.assertFalse(display_hold('50', '32464'))

    def test_late_old_visible_response_cannot_erase_newer_hidden_observation(self):
        now = time.time()
        _remember_display(self.asset, '42', '32464', 'GD', 1, now)
        _remember_display({**self.asset, 'canDisplayAsset': True}, '42', '32464', 'GD', 1, now - 1)
        self.assertTrue(display_hold('42', '32464'))
        _remember_display({**self.asset, 'canDisplayAsset': True}, '42', '32464', 'GD', 1, now + .01)
        self.assertFalse(display_hold('42', '32464'))
        # Also retain a visible watermark when this lot was never held.
        other = {**self.asset, 'assetId': 43}
        _remember_display({**other, 'canDisplayAsset': True}, '43', '32464', 'GD', 1, now)
        _remember_display(other, '43', '32464', 'GD', 1, now - 1)
        self.assertFalse(display_hold('43', '32464'))

    async def test_hidden_native_details_keep_both_queues_without_an_access_failure(self):
        for cls in (GovDealsScraper, AllSurplusScraper):
            scraper = cls(); scraper.reset_health()
            with patch('scrapers.govdeals.market_queries', return_value=['zeta']), \
                 patch.object(GovDealsClient, 'initialize', AsyncMock()), \
                 patch.object(GovDealsClient, 'search', AsyncMock(return_value=[ROW])), \
                 patch.object(GovDealsClient, 'asset', AsyncMock(return_value=self.asset)), \
                 patch('scrapers.govdeals.stage', side_effect=lambda n,c,l:(c,0)), \
                 patch('scrapers.govdeals.complete') as complete:
                self.assertEqual(await scraper.search(), [])
            self.assertEqual(complete.call_args.args[0], cls.name)
            self.assertEqual(complete.call_args.args[1]['assetId'], 42)
            self.assertEqual(complete.call_args.args[2], 3600)
            self.assertEqual(scraper.coverage_stats['detail_visibility_held'], 1)
            self.assertEqual(scraper.coverage_stats['detail_unverified'], 0)
            self.assertEqual(scraper.content_failures, [])

    async def test_confirmed_closed_primary_keeps_precedence_over_hidden_or_cached_hold(self):
        await self.verify(self.asset)
        for status in ('SOLD', 'SOA'):
            item, result = await self.verify({**self.asset, 'assetStatusCd': status})
            self.assertEqual(result[0], 'dead')

    async def test_rediscovered_hidden_violin_stays_due_without_seen_or_pending_alert(self):
        import main
        from discovery_queue import DiscoveryQueue
        await self.verify(self.asset)
        item = {'url': self.url, 'title': 'Zeta Jazz Fusion JV5',
                'description': 'Used Zeta electric violin with case', 'platform': 'Fixture'}
        with DiscoveryQueue('search:AuctionVisibilityFixture') as queue:
            queue.put([item]); queue.claim(1)
        db = database.Database()
        try:
            dropped = {}
            # An unreadable refresh and newly created candidate must not lose
            # the prior bound negative or become a labelled-UNKNOWN alert.
            with patch('offer_verifier.public_dns', AsyncMock(return_value=False)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:
                        self.fail('No HTTP is needed after the DNS refusal'))) as client:
                    result = await main._verify_search_hits([item], db, client, dropped,
                                                           'search:AuctionVisibilityFixture')
            self.assertEqual(result, [])
            self.assertEqual(db.pending(), [])
            self.assertFalse(db.was_url_alerted(self.url))
            self.assertEqual(dropped, {'availability_conflict': 1})
            with DiscoveryQueue('search:AuctionVisibilityFixture') as queue:
                self.assertEqual(queue.conn.execute('SELECT count(*) FROM discovery_queue').fetchone()[0], 1)
                self.assertEqual(len(queue.claim(1, now=time.time() + 3601)), 1)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
