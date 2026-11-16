"""Typed missing-ID incident, primary identity/stock, variations, fallbacks, budget."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import database
import ebay_primary as api
from config import Config
from filters import classify
from offer_verifier import verify_offer, availability_conflict
from provider_state import store
from work_context import spending_scope

URL = 'https://www.ebay.com/itm/286693351748'
TARGET = api.target(URL)
ROW = {'itemId': 'v1|286693351748|0', 'legacyItemId': '286693351748', 'itemWebUrl': URL,
       'title': 'Zeta Jazz Fusion JV5', 'description': '<p>Violon Zeta électrique original cinq cordes. Case only has minor scuffs.</p>',
       'brand': 'Zeta', 'categoryPath': 'Musical Instruments & Gear|String|Orchestral|Violins',
       'condition': 'Used', 'price': {'value': '3500.00', 'currency': 'EUR'},
       'buyingOptions': ['AUCTION'], 'itemEndDate': '2099-01-01T00:00:00Z',
       'estimatedAvailabilities': [{'estimatedAvailabilityStatus': 'IN_STOCK', 'estimatedAvailableQuantity': 1}]}
ERROR = {'errors': [{'errorId': 11003, 'domain': 'API_BROWSE', 'category': 'REQUEST', 'message': 'The specified legacy item Id was not found.'}]}


class PrimaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(database, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
        self.dbpatch.start()
        self.config = patch.multiple(Config, EBAY_CLIENT_ID='fixture', EBAY_CLIENT_SECRET='fixture-secret')
        self.config.start()
        self.original_client = httpx.AsyncClient
        api._AUTH = None

    async def asyncTearDown(self):
        api._AUTH = None
        self.config.stop(); self.dbpatch.stop(); self.temp.cleanup()

    def fake_clients(self, handler):
        return patch('ebay_primary.httpx.AsyncClient', side_effect=lambda **kw:
                     self.original_client(transport=httpx.MockTransport(handler), **kw))

    def handler(self, detail, status=200, calls=None):
        def respond(request):
            if calls is not None: calls.append(request)
            if request.url.path.endswith('/oauth2/token'):
                return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            self.assertEqual(request.url.host, 'api.ebay.com')
            self.assertEqual(request.url.path, '/buy/browse/v1/item/get_item_by_legacy_id')
            return httpx.Response(status, json=detail)
        return respond

    def test_only_exact_official_hosts_and_item_paths_bind_id_and_market(self):
        self.assertEqual(api.target('https://www.ebay.fr/itm/title/123?var=456'),
                         {'id': '123', 'variation': '456', 'market': 'EBAY_FR'})
        for suffix, market in api.MARKETS.items():
            self.assertEqual(api.target(f'https://www.ebay.{suffix}/itm/123')['market'], 'EBAY_' + market)
        for url in ('https://ebay.com.evil.test/itm/123', 'https://evil-ebay.com/itm/123',
                    'https://user:pass@ebay.com/itm/123', 'https://www.ebay.com:444/itm/123',
                    'https://www.ebay.com/itm/123/not-item', 'https://www.ebay.com/itm/123?var=',
                    'https://www.ebay.com/itm/123?var=2&var=3', 'https://www.ebay.com/sch/123'):
            self.assertIsNone(api.target(url), url)

    def test_primary_title_description_metadata_and_local_price_replace_index_without_year_gate(self):
        item = api.parse_primary(ROW, TARGET)
        self.assertEqual(item['state'], 'live')
        self.assertIn('original cinq cordes', item['listing']['description'])
        self.assertEqual(item['listing']['price'], '3500 EUR')
        self.assertEqual(classify({**item['listing'], 'url': URL}), '')
        self.assertNotIn('<p>', item['listing']['description'])

    def test_actual_us_uk_violin_viola_and_not_found_controls(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/ebay_primary_controls_20261116.json').read_text())
        for row in fixture['calls']:
            t = {'id': row['params']['legacy_item_id'], 'variation': '0', 'market': row['marketplace']}
            parsed = api.parse_response(httpx.Response(row['http'], json=row['data'] if row['http'] == 200 else {'errors': row['errors']}), t)
            self.assertEqual(parsed['state'], 'held' if row['http'] == 404 else 'live')
            if row['http'] == 200:
                self.assertEqual(parsed['listing']['price'], '1135.16 GBP' if t['market'] == 'EBAY_GB' else row['data']['price']['value'].removesuffix('.00') + ' USD')
                reason = classify({**parsed['listing'], 'url': row['data']['itemWebUrl']})
                self.assertEqual(bool(reason), row['data']['title'] == 'Zeta Jazz Viola')

    def test_market_not_found_cannot_hide_other_country_but_terminal_stock_is_shared(self):
        gb = {**TARGET, 'market': 'EBAY_GB'}
        gb_url = URL.replace('ebay.com', 'ebay.co.uk')
        api.remember(TARGET, {'state': 'held', 'reason': '11003', 'listing': {}}, 1000)
        self.assertIsNone(api.hold(gb_url))
        terminal = api.parse_primary({**ROW, 'estimatedAvailabilities': [{'estimatedAvailabilityStatus': 'OUT_OF_STOCK'}]}, TARGET)
        api.remember(TARGET, terminal, 2000)
        self.assertEqual(api.hold(gb_url)['state'], 'held')
        api.remember(gb, {'state': 'unknown', 'reason': 'incomplete stock', 'listing': {}}, 2500)
        self.assertEqual(api.hold(gb_url)['state'], 'held')
        api.remember(gb, api.parse_primary(ROW, gb), 3000)
        self.assertIsNone(api.hold(gb_url))
        self.assertEqual(api.cached(gb_url)['state'], 'live')

    def test_full_primary_identity_is_independent_of_title_violin_and_index(self):
        value = {**ROW, 'title': 'Instrument', 'description': 'Violon électrique cinq cordes Zeta.'}
        parsed = api.parse_primary(value, TARGET)
        self.assertEqual(classify({**parsed['listing'], 'url': URL}), '')
        for value in ({**ROW, 'title': 'Zeta Jazz Viola'}, {**ROW, 'title': 'Zeta violin case only', 'description': 'Violin not included.', 'categoryPath': 'Cases'}):
            parsed = api.parse_primary(value, TARGET)
            self.assertTrue(classify({**parsed['listing'], 'url': URL}))

    def test_localized_primary_brand_model_metadata_is_used_without_invented_index_identity(self):
        for brand, model in (('Marque', 'Modèle'), ('Marke', 'Modell'), ('ブランド', 'モデル'), ('브랜드', '모델')):
            row = {**ROW, 'title': 'Instrument', 'brand': None, 'description': '',
                   'localizedAspects': [{'name': brand, 'value': 'Zeta'}, {'name': model, 'value': 'JV5'}, {'name': {}, 'value': 'bad'}]}
            parsed = api.parse_primary(row, TARGET)
            self.assertEqual(parsed['listing']['brand'], 'Zeta')
            self.assertEqual(parsed['listing']['model'], 'JV5')
            self.assertEqual(classify({**parsed['listing'], 'url': URL}), '')

    def test_wrong_id_link_variant_or_malformed_schema_cannot_prove_live_or_closed(self):
        for changes in ({'legacyItemId': True}, {'legacyItemId': '123'}, {'itemId': 'v1|123|0'},
                        {'itemWebUrl': URL.replace('ebay.com', 'ebay.com.evil.test')},
                        {'itemId': 'v1|286693351748|456'}, {'description': {}}, {'title': ''},
                        {'itemWebUrl': URL + '?var=456'}):
            self.assertIsNone(api.parse_primary({**ROW, **changes}, TARGET), changes)
        self.assertIsNone(api.parse_primary({}, TARGET))

    def test_stock_is_explicit_and_end_date_has_timezone(self):
        for changes, expected in (({'estimatedAvailabilities': []}, 'unknown'),
                                  ({'estimatedAvailabilities': [{'estimatedAvailabilityStatus': 'mystery'}]}, 'unknown'),
                                  ({'estimatedAvailabilities': [{'estimatedAvailabilityStatus': 'OUT_OF_STOCK'}]}, 'held'),
                                  ({'estimatedAvailabilities': [{'estimatedAvailabilityStatus': 'OUT_OF_STOCK'}, {'estimatedAvailabilityStatus': 'IN_STOCK'}]}, 'live'),
                                  ({'estimatedAvailabilities': [{'estimatedAvailabilityStatus': 'IN_STOCK'}, None]}, 'unknown'),
                                  ({'itemEndDate': '2000-01-01T00:00:00Z'}, 'held'),
                                  ({'itemEndDate': '2099-01-01'}, 'unknown')):
            self.assertEqual(api.parse_primary({**ROW, **changes}, TARGET)['state'], expected)

    def test_untyped_404_auth_quota_server_and_group_errors_are_not_sold(self):
        for status, body in ((404, {}), (403, ERROR), (429, ERROR), (500, ERROR),
                             (404, {'errors': [{**ERROR['errors'][0], 'domain': 'OTHER'}]}),
                             (404, {'errors': [{**ERROR['errors'][0], 'errorId': '11003'}]}),
                             (400, {'errors': [{**ERROR['errors'][0], 'errorId': 11006}]}),
                             (400, {'errors': [*ERROR['errors'], {**ERROR['errors'][0], 'errorId': 11011}]})):
            self.assertIsNone(api.parse_response(httpx.Response(status, json=body), TARGET), (status, body))
        self.assertIsNone(api.parse_response(httpx.Response(404, text='<h1>gateway not found</h1>'), TARGET))

    def test_conflicting_or_malformed_quantity_is_unknown_not_live_or_sold(self):
        for status, quantity in (('IN_STOCK', 0), ('OUT_OF_STOCK', 1), ('IN_STOCK', True), ('OUT_OF_STOCK', -1)):
            row = {**ROW, 'estimatedAvailabilities': [{'estimatedAvailabilityStatus': status, 'estimatedAvailableQuantity': quantity}]}
            self.assertEqual(api.parse_primary(row, TARGET)['state'], 'unknown')

    def test_typed_11003_and_temporary_11004_are_retry_holds_not_sold(self):
        for code, statuses in ((11003, (400, 404)), (11004, (400, 409))):
            for status in statuses:
                body = {'errors': [{**ERROR['errors'][0], 'errorId': code}]}
                parsed = api.parse_response(httpx.Response(status, json=body), TARGET)
                self.assertEqual(parsed['state'], 'held')
                self.assertIn(str(code), parsed['reason'])
                self.assertNotIn('sold', parsed['reason'].lower())

    def test_invalid_prices_never_become_false_amounts(self):
        for value in (True, 'nan', 'inf', '-1', {}, None):
            parsed = api.parse_primary({**ROW, 'price': {'value': value, 'currency': 'EUR'}}, TARGET)
            self.assertEqual(parsed['listing']['price'], 'N/A')

    def test_negative_durable_order_and_fresh_positive_recovery_are_scoped_to_variant(self):
        held = {'state': 'held', 'reason': '11003', 'listing': {}}
        api.remember(TARGET, held, 2000)
        api.remember(TARGET, api.parse_primary(ROW, TARGET), 1000)
        api.remember(TARGET, {'state': 'unknown', 'reason': 'incomplete', 'listing': {}}, 3000)
        self.assertEqual(api.cached(URL)['state'], 'held')
        var = {**TARGET, 'variation': '456'}
        api.remember(var, held, 2000)
        api.remember(TARGET, api.parse_primary(ROW, TARGET), 4000)
        self.assertEqual(api.cached(URL)['state'], 'live')
        self.assertEqual(api.cached(URL + '?var=456')['state'], 'held')
        self.assertIsNone(api.cached(URL + '?var=789'))
        for checked in (True, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError): api.remember(TARGET, held, checked)

    async def test_same_item_parallel_checks_share_one_get_without_reducing_native_search(self):
        calls = []
        with self.fake_clients(self.handler(ROW, calls=calls)):
            values = await asyncio.gather(*(api.fetch_primary(URL) for _ in range(8)))
        self.assertTrue(all(x['state'] == 'live' for x in values))
        self.assertEqual(sum(r.url.path.endswith('get_item_by_legacy_id') for r in calls), 1)
        self.assertEqual(sum(r.url.path.endswith('/oauth2/token') for r in calls), 1)
        request = calls[-1]
        self.assertEqual(request.headers['X-EBAY-C-MARKETPLACE-ID'], 'EBAY_US')
        self.assertNotIn('X-EBAY-C-ENDUSERCTX', request.headers)  # no Romanian-shipping exclusion
        self.assertEqual(dict(request.url.params), {'legacy_item_id': TARGET['id']})

    async def test_explicit_variation_uses_exact_parameter_and_cannot_hold_sibling(self):
        calls = []
        with self.fake_clients(self.handler(ERROR, 404, calls)):
            value = await api.fetch_primary(URL + '?var=456')
        self.assertEqual(value['state'], 'held')
        self.assertEqual(calls[-1].url.params['legacy_variation_id'], '456')
        self.assertIsNone(api.cached(URL))
        self.assertIsNone(api.cached(URL + '?var=789'))

    async def test_api_redirect_does_not_forward_bearer_or_create_negative(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('/oauth2/token'):
                return httpx.Response(200, json={'access_token': 'fixture-token', 'expires_in': 7200})
            return httpx.Response(302, headers={'Location': 'https://evil.test/steal'})
        with self.fake_clients(handler):
            self.assertIsNone(await api.fetch_primary(URL))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(x.url.host == 'api.ebay.com' for x in calls))
        self.assertIsNone(api.cached(URL))

    async def test_primary_enrichment_and_missing_index_incident_in_shared_verifier(self):
        async with self.original_client(transport=httpx.MockTransport(lambda _: self.fail('No HTML required'))) as client:
            with patch('offer_verifier.public_dns', AsyncMock(return_value=True)), self.fake_clients(self.handler(ROW)):
                item = {'url': URL, 'title': 'wrong index identity', 'source': 'search'}
                state, _ = await verify_offer(item, client)
                self.assertEqual(state, 'live')
                self.assertTrue(item['_primary_content_checked'])
                self.assertEqual(item['title'], ROW['title'])
                self.assertEqual(item['price'], '3500 EUR')
            api.remember(TARGET, {'state': 'held', 'reason': 'API_BROWSE11003', 'listing': {}}, time.time()+.001)
            with patch('offer_verifier.public_dns', AsyncMock(return_value=True)):
                item = {'url': URL, 'title': 'Zeta Electric Violin', 'source': 'search'}
                state, _ = await verify_offer(item, client)
                self.assertEqual(state, 'unknown'); self.assertTrue(availability_conflict(item, state))

    async def test_network_failure_new_candidate_keeps_existing_unknown_route(self):
        async with self.original_client(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
            with patch('offer_verifier.public_dns', AsyncMock(return_value=True)), patch('ebay_primary.fetch_primary', AsyncMock(side_effect=httpx.ConnectError('fixture'))):
                item = {'url': URL, 'title': 'Zeta Electric Violin', 'source': 'search'}
                state, _ = await verify_offer(item, client)
        self.assertEqual(state, 'unknown'); self.assertFalse(availability_conflict(item, state))

    async def test_old_negative_survives_failed_refresh_but_bound_html_live_can_recover(self):
        api.remember(TARGET, {'state': 'held', 'reason': 'API_BROWSE11003', 'listing': {}}, time.time()-600)
        async def run(html, status):
            async with self.original_client(transport=httpx.MockTransport(lambda _: httpx.Response(status, text=html))) as client:
                with patch('offer_verifier.public_dns', AsyncMock(return_value=True)), patch('ebay_primary.fetch_primary', AsyncMock(return_value=None)):
                    item = {'url': URL, 'title': 'Zeta Violin', 'source': 'search'}
                    state, why = await verify_offer(item, client)
                    return item, state
        item, state = await run('blocked', 403)
        self.assertTrue(availability_conflict(item, state))
        html = '<h1>Zeta Electric Violin</h1><main><p>Used Zeta violin for sale. $1500. Buy It Now</p></main>'
        item, state = await run(html, 200)
        self.assertEqual(state, 'live')
        self.assertEqual(api.cached(URL)['state'], 'live')

    async def test_other_item_or_variant_html_redirect_cannot_reopen_known_hold(self):
        api.remember(TARGET, {'state': 'held', 'reason': '11003', 'listing': {}}, time.time()-600)
        for destination in ('https://www.ebay.com/itm/123', URL+'?var=456', 'https://example.com/itm/286693351748'):
            async with self.original_client(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={'Location': destination}))) as client:
                with patch('offer_verifier.public_dns', AsyncMock(return_value=True)), patch('ebay_primary.fetch_primary', AsyncMock(return_value=None)):
                    item = {'url': URL}
                    state, _ = await verify_offer(item, client)
                    self.assertEqual(state, 'unknown'); self.assertTrue(availability_conflict(item, state))

    async def test_unavailable_candidate_is_retained_without_alert_seen_or_outbox(self):
        import main
        from discovery_queue import DiscoveryQueue
        item = {'id': 'incident', 'url': URL, 'title': 'Zeta Electric Violin', 'source': 'search'}
        db = database.Database()
        namespace = 'search:eBay-primary-test'
        try:
            with DiscoveryQueue(namespace) as queue: queue.put([item])
            async with self.original_client(transport=httpx.MockTransport(lambda _: self.fail('No HTML required'))) as client:
                with patch('offer_verifier.public_dns', AsyncMock(return_value=True)), self.fake_clients(self.handler(ERROR, 404)):
                    dropped = {}
                    values = await main._verify_search_hits([item], db, client, dropped, namespace)
            self.assertEqual(values, []); self.assertFalse(db.is_seen('incident')); self.assertEqual(db.pending(), [])
            self.assertEqual(dropped.get('availability_conflict'), 1)
            with DiscoveryQueue(namespace) as queue:
                self.assertEqual(queue.conn.execute('SELECT count(*) FROM discovery_queue WHERE namespace=?', (namespace,)).fetchone()[0], 1)
        finally: db.close()

    def test_detail_allowance_is_atomic_and_auxiliary_preserves_core_slots(self):
        with store(): pass
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(lambda _: api._reserve(), range(320))), 300)
        with spending_scope('core'):
            self.assertEqual(sum(api._reserve() for _ in range(110)), 100)
            self.assertFalse(api._reserve())
        with store() as c:
            self.assertEqual(c.execute("SELECT sum(units) FROM provider_reservations WHERE provider='ebay_primary'").fetchone()[0], 400)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='scraper_runs'").fetchone())


if __name__ == '__main__': unittest.main()
