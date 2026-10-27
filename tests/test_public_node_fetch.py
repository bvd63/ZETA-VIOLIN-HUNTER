"""Offline tests of the restricted Node fetch and Gumtree recovery."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from public_node_fetch import allowed_url, fetch, same_target, MAX_BYTES
from scrapers.gumtree import GumtreeScraper

URL = 'https://www.gumtree.com/search?search_category=all&q=zeta'


class Process:
    def __init__(self, output=b'', code=0, hang=False):
        self.output, self.returncode, self.hang, self.killed = output, None, hang, False
        self.code = code
    async def communicate(self):
        if self.hang and not self.killed:
            await asyncio.Event().wait()
        self.returncode = self.code if not self.killed else -9
        return self.output, b''
    def kill(self):
        self.killed = True


def output(**changes):
    return json.dumps({'status':200,'body':'<html>ok</html>','url':URL,**changes}).encode()


class NodeTests(unittest.IsolatedAsyncioTestCase):
    def test_allowlist(self):
        for url in (URL, 'https://gumtree.com/p/violins/zeta/123'):
            self.assertTrue(allowed_url(url))
        for url in ('http://www.gumtree.com/search', 'https://www.gumtree.com.evil.test/search',
                    'https://gumtree.com@evil.test/search', 'https://gumtree.com:444/search',
                    'https://gumtree.com/admin', 'https://gumtree.com/p/x/not-an-id',
                    'https://[bad/search'):
            self.assertFalse(allowed_url(url))

    async def run_fake(self, proc):
        with patch('public_node_fetch._node_path',return_value='/fake/node'), \
             patch('public_node_fetch.asyncio.create_subprocess_exec',AsyncMock(return_value=proc)) as spawn:
            result = await fetch(URL)
        self.assertEqual(spawn.await_args.args[-1], URL)
        return result

    async def test_valid_and_invalid_subprocess_responses(self):
        self.assertEqual(await self.run_fake(Process(output())), (200,'<html>ok</html>',URL))
        for proc in (Process(output(),code=1), Process(b'not json'), Process(output(status='200')),
                     Process(output(url='https://evil.test/search')), Process(output(body='x'*(MAX_BYTES+1))),
                     Process(output(extra='unexpected')), Process(output(url=URL.replace('q=zeta','q=other')))):
            with self.subTest(proc=proc), self.assertRaises(ValueError):
                await self.run_fake(proc)

    async def test_timeout_and_cancel_kill_and_reap(self):
        for cancel in (False, True):
            proc=Process(hang=True)
            with patch('public_node_fetch._node_path',return_value='/fake/node'), \
                 patch('public_node_fetch.asyncio.create_subprocess_exec',AsyncMock(return_value=proc)):
                task=asyncio.create_task(fetch(URL,timeout=.01 if not cancel else 10))
                if cancel:
                    await asyncio.sleep(0)
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                    await task
            self.assertTrue(proc.killed)
            self.assertEqual(proc.returncode,-9)

    async def test_disallowed_url_never_spawns(self):
        with patch('public_node_fetch.asyncio.create_subprocess_exec',new_callable=AsyncMock) as spawn:
            with self.assertRaises(ValueError):
                await fetch('https://evil.test/search')
            spawn.assert_not_awaited()

    async def test_deadline_includes_waiting_for_capacity(self):
        permits = asyncio.Semaphore(2)
        procs = [Process(hang=True), Process(hang=True)]
        with patch('public_node_fetch._LIMIT', permits), \
             patch('public_node_fetch._node_path',return_value='/fake/node'), \
             patch('public_node_fetch.asyncio.create_subprocess_exec',AsyncMock(side_effect=procs)) as spawn:
            tasks = [asyncio.create_task(fetch(URL,timeout=10)) for _ in procs]
            try:
                while spawn.await_count < 2:
                    await asyncio.sleep(0)
                with self.assertRaises(asyncio.TimeoutError):
                    await fetch(URL,timeout=.01)
                self.assertEqual(spawn.await_count,2)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        self.assertTrue(all(proc.killed for proc in procs))
        self.assertEqual(permits._value,2)

    def test_redirects_preserve_query_or_item_identity(self):
        self.assertTrue(same_target(URL, 'https://gumtree.com/search?q=zeta&search_category=all'))
        self.assertFalse(same_target(URL, 'https://gumtree.com/search?q=other&search_category=all'))
        self.assertFalse(same_target(URL, 'https://gumtree.com/p/violins/zeta/123'))
        self.assertTrue(same_target('https://gumtree.com/p/violins/old/123', 'https://www.gumtree.com/p/violins/new/123'))
        self.assertFalse(same_target('https://gumtree.com/p/violins/old/123', 'https://gumtree.com/p/violins/new/456'))


class GumRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch('database.DB_PATH', str(Path(self.tmp.name)/'node.db'))
        self.db_patch.start()

    async def asyncTearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_valid_node_recovery_preserves_all_queries_and_health(self):
        fixture=(Path(__file__).parent/'fixtures/gumtree_cards_2026.html').read_text()
        scraper=GumtreeScraper()
        scraper.reset_health()
        async def requested(request): scraper.requests_attempted += 1
        async def responded(response): scraper.failures.append('HTTP 247 (gumtree.com)')
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(247)),
            event_hooks={'request':[requested],'response':[responded]})
        class Browser:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def fetch(self,*args): raise AssertionError('node should recover')
        with patch.object(scraper,'make_client',return_value=client), \
             patch('scrapers.gumtree.PublicSearchBrowser',return_value=Browser()), \
             patch('scrapers.gumtree.node_fetch',AsyncMock(return_value=(200,fixture,URL))) as node, \
             patch('scrapers.gumtree.market_queries',return_value=['Zeta violin','Zeta JV5']), \
             patch('asyncio.sleep',new_callable=AsyncMock):
            await scraper.search()
        self.assertEqual(node.await_count,2)
        self.assertIn('q=Zeta%20violin',node.await_args_list[0].args[0])
        self.assertIn('q=Zeta%20JV5',node.await_args_list[1].args[0])
        self.assertEqual(scraper.requests_attempted,6)
        self.assertEqual(scraper.requests_ok,2)
        self.assertEqual(scraper.requests_recovered,4)
        self.assertEqual(scraper.health_error(),'')
        self.assertGreater(scraper.fetched,0)


if __name__ == '__main__':
    unittest.main()
