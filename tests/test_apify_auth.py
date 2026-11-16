import asyncio
import json
import time
import contextlib
import io
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from config import Config
from apify_auth import check_access, mark_rejected
from provider_state import read, write


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=patch('database.DB_PATH',str(Path(self.temp.name)/'auth.db'));self.db.start()
        self.key=patch.object(Config,'APIFY_TOKEN','test-private-token');self.key.start()
    def tearDown(self):
        self.key.stop();self.db.stop();self.temp.cleanup()
    def run_check(self,status):
        calls=[]
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req) or httpx.Response(status))) as client:
                return await check_access(client)
        return asyncio.run(go()),calls
    def test_legacy_meter_failure_checks_token_before_any_actor_or_budget(self):
        write('apify:account',{'error':'HTTPStatusError'})
        ok,calls=self.run_check(401)
        self.assertFalse(ok);self.assertEqual([r.method for r in calls],['GET'])
        self.assertEqual([r.url.path for r in calls],['/v2/users/me'])
        state=read('apify:authentication')
        self.assertNotIn(Config.APIFY_TOKEN,str(state));self.assertEqual(state['status'],401)
        with __import__('provider_state').store() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM provider_reservations').fetchone()[0],0)
    def test_known_bad_token_does_not_repeat_requests_after_restart(self):
        mark_rejected(401)
        self.assertEqual(self.run_check(200),(False,[]))
    def test_replacement_token_is_not_blocked_by_old_token_refusal(self):
        mark_rejected(401)
        with patch.object(Config,'APIFY_TOKEN','new-test-token'):
            self.assertEqual(self.run_check(200),(True,[]))
    def test_valid_replacement_rechecks_legacy_meter_failure(self):
        write('apify:account',{'error':'HTTPStatusError'})
        mark_rejected(401)
        with patch.object(Config,'APIFY_TOKEN','new-test-token'):
            ok,calls=self.run_check(200)
            self.assertTrue(ok);self.assertEqual(len(calls),1)
            self.assertEqual(read('apify:authentication')['status'],200)
    def test_same_token_can_recover_after_read_only_recheck_without_budget_reset(self):
        mark_rejected(401)
        state=read('apify:authentication');state['checked']=time.time()-7*3600
        write('apify:authentication',state)
        ok,calls=self.run_check(200)
        self.assertTrue(ok);self.assertEqual([r.method for r in calls],['GET'])
        self.assertEqual(read('apify:authentication')['status'],200)
    def test_scoped_403_is_not_assumed_invalid_for_actor_access(self):
        write('apify:account',{'error':'HTTP 403'})
        ok,calls=self.run_check(403)
        self.assertTrue(ok);self.assertEqual(len(calls),1)
        self.assertEqual(read('apify:authentication',{}),{})
    def test_healthy_account_does_not_add_auth_requests_or_modify_guards(self):
        write('apify:account',{'error':''})
        self.assertEqual(self.run_check(200),(True,[]))
        self.assertEqual(read('apify:authentication',{}),{})
    def test_invalid_credentials_keep_verified_cached_violins_without_actor_post(self):
        from scrapers.apify_facebook import ApifyFacebookScraper,normalize,store
        item=normalize({'url':'https://www.facebook.com/marketplace/item/123456/',
                        'title':'Zeta Jazz Fusion JV5 violin','description':'Used Zeta electric violin',
                        'isLive':True,'isSold':False,'isPending':False})
        with store() as conn:
            conn.execute('INSERT INTO apify_items VALUES(?,?,?,?)',(item['url'],json.dumps(item),json.dumps(item),time.time()))
            conn.commit()
        mark_rejected(401)
        scraper=ApifyFacebookScraper()
        async def run():
            with patch.object(Config,'APIFY_DISCOVERY_TASK_ID','testDiscovery123'),patch.object(Config,'APIFY_DETAILS_TASK_ID','testDetails123'),patch.object(scraper,'make_client',side_effect=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(lambda _:self.fail('No HTTP or paid POST')),**kw)):
                return await scraper.search()
        ready=asyncio.run(run())
        self.assertEqual([x['url'] for x in ready],[item['url']])
        self.assertEqual(scraper.coverage_stats['cached_verified_details'],1)
        with store() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM apify_jobs').fetchone()[0],0)
    def test_external_auth_error_cannot_block_other_source_repairs_or_hide_test_failures(self):
        import deploy_check
        def check(command,*,check,env):
            if command[2]=='apify_preview':raise subprocess.CalledProcessError(10,command)
        output=io.StringIO()
        with patch('deploy_check.subprocess.run',side_effect=check),contextlib.redirect_stdout(output):
            deploy_check.main()
        self.assertIn('BLOCKED (401), not verified',output.getvalue())
        for code,module in ((1,'apify_preview'),(10,'tests.test_apify')):
            def fail(command,*,check,env):
                if command[2]==module:raise subprocess.CalledProcessError(code,command)
            with patch('deploy_check.subprocess.run',side_effect=fail),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):deploy_check.main()

if __name__=='__main__':unittest.main()
