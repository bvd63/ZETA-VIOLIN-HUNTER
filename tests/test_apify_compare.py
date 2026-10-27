"""Offline actor plan and comparison contract; no provider calls or state writes."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apify_compare import ACTORS, compare, plan, search_url

URL = 'https://www.facebook.com/marketplace/chicago/search/?query=zeta+violin&exact=false&radius=500'
CONTRACTS = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/apify_actor_input_contracts_2026_10_27.json').read_text())['actors']


def doc(items, **overrides):
    context = {'actor':'official', 'collection':{'details':True,'bound_kind':'results','bound':20,'availability_scope':'provider_default'}, 'query_urls':[URL], 'status':'SUCCEEDED', 'started_at':'2026-10-27T10:00:00Z', 'actual_cost_usd':.0123, 'coverage_complete':True}
    context.update(overrides)
    return {'context':context, 'items':items}


def row(n, **overrides):
    data={'itemUrl':f'https://www.facebook.com/marketplace/item/{n}/?token=private',
          'listingTitle':'Zeta Jazz Fusion violin','description':{'text':'Used violin'},
          'isLive':True,'isSold':False,'isPending':False}
    data.update(overrides)
    return data


class ActorPlanTests(unittest.TestCase):
    def test_payloads_conform_to_saved_top_level_input_schema(self):
        # Saved schemas define top-level types/enum but omit array item schemas.
        for actor, name in ACTORS.items():
            with self.subTest(actor=actor):
                payload = plan(actor,URL,5,1)['input']
                schema = CONTRACTS[name]['inputSchema']
                self.assertTrue(set(payload) <= set(schema['properties']))
                self.assertTrue(set(schema.get('required',[])) <= set(payload))
                for key,value in payload.items():
                    prop=schema['properties'][key]
                    types={'array':list,'boolean':bool,'integer':int,'string':str,'object':dict}
                    self.assertIsInstance(value,types[prop['type']],key)
                    if prop.get('enum') is not None:
                        self.assertIn(value,prop['enum'])
                self.assertNotIn('cookies',payload)
                self.assertFalse(any(repr(k).lower().find('token') >= 0 for k in payload))
        self.assertEqual(plan('memo',URL,60)['input']['maxItems'],60)
        self.assertEqual(plan('memo',URL,60)['collection']['availability_scope'],'in_stock')
        self.assertEqual(plan('curious',URL,5,2)['collection']['bound_kind'],'pages')
        self.assertEqual(plan('curious',URL)['collection']['availability_scope'],'provider_default')
        self.assertEqual(plan('official',URL)['collection']['availability_scope'],'provider_default')
        self.assertEqual(plan('black',URL)['collection']['availability_scope'],'all')
        self.assertEqual(plan('memo',URL)['input']['startUrls'],[search_url(URL)])
        self.assertFalse(plan('memo',URL)['input']['monitoringMode'])
        self.assertEqual(plan('memo',URL)['input']['maxListingAgeHours'],0)
        curious=plan('curious',URL,5,2)
        self.assertEqual(curious['input']['maxPagesPerUrl'],2)
        self.assertNotIn('maxResults',curious['input'])
        self.assertIn('not results',curious['note'])

    def test_plan_and_compare_do_not_call_provider_or_database(self):
        with patch('database.connect', side_effect=AssertionError('database')), \
             patch('httpx.Client.request', side_effect=AssertionError('HTTP')), \
             patch('httpx.AsyncClient.request', side_effect=AssertionError('HTTP')):
            for actor in ACTORS:
                plan(actor,URL)
            self.assertTrue(compare(doc([row(1)]),doc([row(2)]))['comparable'])

    def test_limits_and_credentials_are_rejected_without_echo(self):
        for kwargs in ({'limit':0},{'limit':61},{'limit':True},{'max_pages':0},{'max_pages':6}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): plan('memo',URL,**kwargs)
        for url in (URL+'&token=SECRET', URL+'&daysSinceListed=1', URL.replace('exact=false','exact=true'),
                    'https://facebook.com.evil.example/marketplace/chicago/search/?query=zeta',
                    'https://www.facebook.com/marketplace/chicago/search/?query=zeta+password+SECRET'):
            with self.subTest(url=url),self.assertRaises(ValueError): plan('official',url)
        run=subprocess.run([sys.executable,'apify_compare.py','--plan','official','--url',URL+'&token=SECRET'],
                           capture_output=True,text=True,check=False)
        self.assertEqual(run.returncode,2)
        self.assertNotIn('SECRET',run.stdout+run.stderr)


class CompareTests(unittest.TestCase):
    def test_unique_eligible_counts_errors_and_sensitive_fields_redacted(self):
        base=doc([row(10), row(10),row(11,isSold=True)])
        cand=doc([row(10),row(20,description={'text':'SECRET seller contact'}),
                  row(21,listingTitle='Zeta pickup',description={'text':'pickup only'}),
                  row(22,description=None,isLive=None),row(23,description=None)])
        result=compare(base,cand)
        self.assertTrue(result['comparable'])
        self.assertEqual(result['additional_actor_output_eligible_urls'],[
            'https://www.facebook.com/marketplace/item/20/', 'https://www.facebook.com/marketplace/item/23/'])
        self.assertEqual(result['baseline']['counts']['duplicates'],1)
        self.assertEqual(result['baseline']['counts']['errors'],0)
        self.assertEqual(result['baseline']['counts']['invalid_rows'],0)
        self.assertEqual(result['candidate']['counts']['missing_description'],2)
        self.assertEqual(result['candidate']['counts']['not_available_or_unknown'],1)
        output=json.dumps(result)
        self.assertNotIn('SECRET',output)
        self.assertNotIn('token=',output)
        self.assertNotIn('zeta+violin',output)
        self.assertIn('not independently confirmed',result['note'])
        self.assertEqual(result['paired_observation']['counts'],{'overlap':1,'candidate_only':2,'baseline_only':0})
        self.assertEqual(result['paired_observation']['scope_differences'],{})

    def test_valid_alternative_contracts_have_observations_without_false_equivalence(self):
        baseline=doc([row(1),row(2)])
        for actor in ('curious','black','memo'):
            with self.subTest(actor=actor):
                configured=plan(actor,URL,20,1)
                candidate=doc([row(2),row(3),row(3)],actor=configured['actor'],collection=configured['collection'])
                result=compare(baseline,candidate)
                self.assertFalse(result['comparable'])
                self.assertIsNone(result['additional_actor_output_eligible_urls'])
                self.assertEqual(result['incomparable_reasons'],['different collection context'])
                observed=result['paired_observation']
                self.assertEqual(observed['counts'],{'overlap':1,'candidate_only':1,'baseline_only':1})
                self.assertEqual(observed['overlap_urls'],['https://www.facebook.com/marketplace/item/2/'])
                self.assertEqual(observed['candidate_only_urls'],['https://www.facebook.com/marketplace/item/3/'])
                self.assertEqual(observed['baseline_only_urls'],['https://www.facebook.com/marketplace/item/1/'])
                differences=observed['scope_differences']
                if actor=='curious':
                    self.assertEqual(differences['bound_kind'],{'baseline':'results','candidate':'pages'})
                    self.assertEqual(differences['bound'],{'baseline':20,'candidate':1})
                else:
                    self.assertEqual(differences['availability_scope']['candidate'],
                                     'all' if actor=='black' else 'in_stock')
                self.assertEqual(result['candidate']['counts']['duplicates'],1)
                self.assertIn('not evidence of superiority',observed['note'])
                self.assertNotIn('token=',json.dumps(result))

    def test_alternative_observation_retains_all_non_scope_guards(self):
        configured=plan('curious',URL,20,1)
        for overrides,items in [({'query_urls':[URL.replace('chicago','seattle')]},[row(3)]),
                ({'status':'FAILED'},[row(3)]),({'started_at':'2026-10-27T11:00:01Z'},[row(3)]),
                ({'actual_cost_usd':None},[row(3)]),({'coverage_complete':False},[row(3)]),
                ({},[{'error':'SECRET failure'}]),({},[None])]:
            with self.subTest(overrides=overrides,items=items):
                context={'actor':configured['actor'],'collection':configured['collection'],**overrides}
                result=compare(doc([row(1)]),doc(items,**context))
                self.assertIsNone(result['paired_observation'])
                self.assertTrue(result['paired_observation_blockers'])
                self.assertNotIn('SECRET',json.dumps(result))

    def test_missing_context_different_query_failed_and_time_window(self):
        cases=[({},'missing or invalid query URLs'),
               ({'actor':None},'missing or unknown actor'),
               ({'actor':'unknown secret'},'missing or unknown actor'),
               ({'collection':None},'missing or invalid collection context'),
               ({'collection':{'details':True,'bound_kind':'pages','bound':1,'availability_scope':'all'}},'different collection context'),
               ({'collection':{'details':True,'bound_kind':'results','bound':6,'availability_scope':'all'}},'different collection context'),
               ({'collection':{'details':True,'bound_kind':'results','bound':20,'availability_scope':'in_stock'}},'different collection context'),
               ({'query_urls':[URL.replace('chicago','seattle')]},'different query URLs'),
               ({'started_at':'2026-10-27T11:00:01Z'},'start times more than one hour apart'),
               ({'status':'FAILED'},'both runs must be SUCCEEDED'),
               ({'started_at':'2026-10-27T10:00:00'},'missing or invalid start time')]
        for changes,reason in cases:
            with self.subTest(changes=changes):
                other={'context':{},'items':[]} if not changes else doc([],**changes)
                out=compare(doc([row(1)]),other)
                self.assertFalse(out['comparable'])
                self.assertIn(reason,out['incomparable_reasons'])
                self.assertIsNone(out['additional_actor_output_eligible_urls'])
                self.assertIsNone(out['paired_observation'])
        self.assertTrue(compare(doc([]),doc([],started_at='2026-10-27T11:00:00Z'))['comparable'])
        inconsistent=compare(doc([]),doc([],actor='curious'))
        self.assertFalse(inconsistent['comparable'])
        self.assertIn('collection context inconsistent with actor contract',inconsistent['incomparable_reasons'])
        self.assertIsNone(inconsistent['paired_observation'])
        inconsistent=compare(doc([]),doc([],actor='memo'))
        self.assertFalse(inconsistent['comparable'])
        self.assertIsNone(inconsistent['paired_observation'])

    def test_error_rows_make_comparison_inconclusive(self):
        result=compare(doc([{'error':'SECRET failure'}]),doc([row(2)]))
        self.assertFalse(result['comparable'])
        self.assertIsNone(result['additional_actor_output_eligible_urls'])
        self.assertIsNone(result['paired_observation'])
        self.assertEqual(result['baseline']['counts']['errors'],1)
        self.assertNotIn('SECRET',json.dumps(result))

    def test_input_file_limit_and_missing_fixture_are_not_silently_accepted(self):
        from apify_compare import read_document
        with tempfile.TemporaryDirectory() as d:
            missing=Path(d)/'missing.json'
            with self.assertRaises(OSError): read_document(missing)
            large=Path(d)/'large.json'
            large.write_bytes(b' '*(10*1024*1024+1))
            with self.assertRaises(ValueError): read_document(large)
            result=subprocess.run([sys.executable,'apify_compare.py','--compare',str(large),str(missing)],
                                  capture_output=True,text=True,check=False)
            self.assertEqual(result.returncode,2)
            self.assertNotIn(str(large),result.stderr)

    def test_missing_cost_invalid_rows_and_incomplete_coverage_are_inconclusive(self):
        cases=[({'actual_cost_usd':None},[], 'actual cost missing or invalid'),
               ({'actual_cost_usd':10**400},[], 'actual cost missing or invalid'),
               ({'started_at':'0001-01-01T00:00:00+23:00'},[], 'missing or invalid start time'),
               ({'coverage_complete':None},[], 'coverage completeness not explicitly confirmed'),
               ({'coverage_complete':False},[], 'coverage completeness not explicitly confirmed'),
               ({},[None], 'actor output contains invalid rows'),
               ({'collection':{'details':True,'bound_kind':'results','bound':1,
                               'availability_scope':'provider_default'}},[row(2)], 'result limit reached; output may be truncated')]
        for context, items, reason in cases:
            with self.subTest(reason=reason):
                result=compare(doc([]),doc(items,**context))
                self.assertFalse(result['comparable'])
                self.assertIn(reason,result['incomparable_reasons'])
                self.assertIsNone(result['additional_actor_output_eligible_urls'])
                self.assertIsNone(result['paired_observation'])
                if items and items[0] is not None:
                    self.assertEqual(len(result['candidate']['actor_output_eligible_urls']),1)

    def test_missing_cost_and_untrusted_urls_are_not_misreported(self):
        candidate=doc([row(1,itemUrl='https://evil.example/marketplace/item/1/'),row(2)],actual_cost_usd=None)
        result=compare(doc([]),candidate)
        self.assertIsNone(result['candidate']['context']['actual_cost_usd'])
        self.assertEqual(result['candidate']['counts']['invalid_rows'],1)
        self.assertFalse(result['comparable'])
        self.assertIsNone(result['additional_actor_output_eligible_urls'])
        self.assertEqual(result['candidate']['actor_output_eligible_urls'],['https://www.facebook.com/marketplace/item/2/'])
        bad=compare(doc([]),doc([],query_urls=[URL+'&access_token=SECRET']))
        self.assertFalse(bad['comparable'])
        self.assertNotIn('SECRET',json.dumps(bad))


if __name__ == '__main__': unittest.main()
