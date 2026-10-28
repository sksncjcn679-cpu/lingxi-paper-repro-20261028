"""Exercise existing wrapper assertions with NEW planned IDs and synthetic outcomes.

No historical outcome, provider, runtime, environment file or credential access.
"""
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
import unittest, json, sys, importlib, hashlib, io
sys.path.append('/drivers')
import statistical_gates as gate
context=gate.checked_inputs()
contract=gate.load(gate.STATS/'statistical_contract.json')
v3=gate.load_verified_v3(contract)
sys.modules['formal_statistics_protocol_aligned_v3']=v3
old=importlib.import_module('formal_statistics_protocol_aligned_v3_test')

class NewPopulationComposition(old.PopulationComposition):
    def setUp(self):
        self.plan=context['main'];self.applicable=context['applicable']
        self.runs=[{**u,'success':False,'provider_input_tokens':None,'observable_ipc_bytes':None} for u in self.plan]
        self.calls=[];self.missing_state_metric=False
    def test_registered_seed_reaches_both_populations(self):
        with patch.object(v3.frozen,'analyze',side_effect=self.stub):
            v3.analyze(self.runs,self.plan,self.applicable,seed=20261020)
        self.assertEqual([r['seed'] for r in self.calls],[20261020,20261020])

class NewEntryBoundaries(unittest.TestCase):
    def setUp(self):
        self.data={name:gate.rows(gate.OUT/'raw'/name) for name in gate.RAW}
    def checked(self,data):
        with patch.object(gate,'rows',side_effect=lambda p:deepcopy(data[Path(p).name])):
            return gate.checked_raw(context,public_only=True)
    def test_foreign_identity_and_unmatched_start_rejected(self):
        for field in ['study_id','task_sha256','effective_contract_sha256','run_id']:
            data=deepcopy(self.data);data['task_runs.jsonl'][0][field]='foreign'
            with self.subTest(field=field),self.assertRaises(ValueError):self.checked(data)
        data=deepcopy(self.data);data['provider_calls.jsonl'].pop()
        with self.assertRaisesRegex(ValueError,'unmatched'):self.checked(data)
    def test_real_start_schema_does_not_invent_model_evidence(self):
        data=deepcopy(self.data)
        for r in data['task_unit_starts.jsonl']:
            r.pop('source_sha256',None);r.pop('model_evidence',None)
        self.assertEqual(len(self.checked(data)['task_unit_starts.jsonl']),144)
        data['provider_calls.jsonl'][0]['model_evidence']='fixture'
        with self.assertRaises(ValueError):self.checked(data)
    def test_unknown_failed_usage_is_not_total_zero_or_currency(self):
        data=deepcopy(self.data);r=data['provider_calls.jsonl'][0]
        arm=r['arm'];r['usage_raw']=None;r['usage_source']='none';r['status']='ERROR'
        result=gate.budget_report(context,data)
        component=result['observed'][arm]['usage']['prompt_tokens']
        self.assertIsNone(component['total']);self.assertGreater(component['observed_subtotal'],0)
        self.assertEqual(component['missing'],1)
        self.assertIsNone(result['actual_currency_cost'])
        self.assertTrue(all(v is None for v in result['verified_unit_prices'].values()))
        self.assertFalse(result['gate_authorizes_provider_execution'])
    def test_missing_cached_usage_does_not_become_zero(self):
        data=deepcopy(self.data)
        for r in data['provider_calls.jsonl']:
            r['usage_raw']['prompt_tokens_details']={'cached_tokens':0}
        r=data['provider_calls.jsonl'][0];arm=r['arm'];r['usage_raw']['prompt_tokens_details']=None
        result=gate.budget_report(context,data)
        self.assertIsNone(result['observed'][arm]['usage']['cached_input_tokens']['total'])
        self.assertEqual(result['observed'][arm]['usage']['cached_input_tokens']['missing'],1)

suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(NewPopulationComposition),
                         unittest.defaultTestLoader.loadTestsFromTestCase(NewEntryBoundaries)])
stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
dest=gate.OUT/'analysis/stats/preformal_validation_v1';dest.mkdir(exist_ok=False)
(dest/'unittest_transcript.txt').write_text(stream.getvalue())
receipt={'status':'PASS' if result.wasSuccessful() else 'FAIL_RETAINED','tests_run':result.testsRun,
         'failures':len(result.failures),'errors':len(result.errors),'formal_outcomes_read':False,
         'public_usage_used_only_for_identity_and_missingness_boundary_fixtures':True,
         'wrapper_assertions':'existing4 tests, new planned120/108 IDs; synthetic failed/missing outcomes',
         'files':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),
                  Path('/drivers/formal_statistics_protocol_aligned_v3_test.py'),gate.STATS/'statistical_contract.json']}}
(dest/'audit.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(stream.getvalue());print(json.dumps(receipt))
sys.exit(0 if result.wasSuccessful() else 1)
