"""Synthetic wrapper tests prepared during collection; DO NOT run while timing.

These tests mock numerical computation, use no formal outcomes/provider, and
verify population composition, one Holm family, failure retention and archive.
"""
from pathlib import Path
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import formal_statistics_protocol_aligned_v3 as v3

OUT = Path('/evidence/formal_20261019')


class PopulationComposition(unittest.TestCase):
    def setUp(self):
        self.plan = [u for u in json.loads((OUT/'manifests/formal_plan.json').read_text()) if u['phase']=='main']
        self.applicable = json.loads((OUT/'analysis/stats/statistical_plan_protocol_alignment_v3.json').read_text())['applicable_task_ids']
        # Values are synthetic; never read task_runs or provider output.
        self.runs = [{**u,'success':False,'provider_input_tokens':None,'observable_ipc_bytes':None} for u in self.plan]
        self.calls = []
        self.missing_state_metric = False

    def stub(self, runs, plan, seed):
        n = len({u['task_id'] for u in plan})
        self.calls.append({'tasks':n,'runs':len(runs),'seed':seed,'failures':sum(not u['success'] for u in runs)})
        comparisons = []
        for index,(a,b) in enumerate(v3.PAIRS):
            metrics = {}
            for endpoint_index, endpoint in enumerate(v3.PRIMARY):
                raw = .02 + index*.01 + endpoint_index*.001
                if (a,b)==v3.STATE_PAIR:
                    raw = .000001 if n==120 else .12 + endpoint_index*.01
                if self.missing_state_metric and n==108 and (a,b)==v3.STATE_PAIR and endpoint_index==0:
                    raw = None
                metrics[endpoint] = {'status':'INCOMPLETE' if raw is None else 'COMPLETE',
                    'p_raw':raw,'p_holm':.99,'holm_family_size':10,'holm_family':'frozen original family',
                    'unique_tasks':n,'paired_difference':float(n)}
            comparisons.append({'baseline':a,'target':b,'planned_paired_units':n*3,
                'quality':{'unique_tasks':n,'chain_count':20},'metrics':metrics})
        return {'schema_version':1,'seed':seed,'planned_units':len(plan),'observed_units':len(runs),
            'strata':[{'model':plan[0]['model'],'topology':plan[0]['topology'],'comparisons':comparisons}],
            'quality_noninferiority_wording':'WITHHELD_BY_PROSPECTIVE_GATE'}

    def execute(self):
        with patch.object(v3.frozen,'analyze',side_effect=self.stub):
            return v3.analyze(self.runs,self.plan,self.applicable)

    def test_exact_mixed_family_and_seed(self):
        result = self.execute()
        comparisons = result['strata'][0]['comparisons']
        self.assertEqual([c['planned_unique_tasks'] for c in comparisons],[120,108,120,120,120])
        self.assertEqual(self.calls,[{'tasks':120,'runs':2160,'seed':20261019,'failures':2160},
                                    {'tasks':108,'runs':1944,'seed':20261019,'failures':1944}])
        raw = [c['metrics'][endpoint]['p_raw'] for c in comparisons for endpoint in v3.PRIMARY]
        self.assertEqual(raw[2:4],[.12,.13])
        adjusted = v3.frozen.holm(raw)
        self.assertEqual([c['metrics'][endpoint]['p_holm'] for c in comparisons for endpoint in v3.PRIMARY],adjusted)
        self.assertEqual(result['active_confirmatory_families'],1)

    def test_frozen_numeric_archive_and_descriptive_no_second_family(self):
        result = self.execute()
        archive = result['all120_frozen_replay_archive']
        self.assertEqual(archive['analysis_status'],'archive_only')
        old_state = archive['strata'][0]['comparisons'][1]
        self.assertEqual(old_state['metrics']['provider_input_tokens']['p_raw'],.000001)
        self.assertEqual(old_state['metrics']['provider_input_tokens']['p_holm'],.99)
        for stratum in result['state_applicable_descriptive']['strata']:
            for comparison in stratum['comparisons']:
                for metric in comparison['metrics'].values():
                    self.assertNotIn('p_holm',metric)
                    self.assertNotIn('holm_family_size',metric)
        deployment = result['full120_state_deployment_descriptive'][0]['comparison']
        self.assertEqual(deployment['quality']['unique_tasks'],120)
        self.assertNotIn('p_holm',deployment['metrics']['provider_input_tokens'])

    def test_wrong_applicability_rejected(self):
        with self.assertRaisesRegex(ValueError,'applicability'):
            v3.analyze(self.runs,self.plan,self.applicable[:-1])

    def test_unknown_state_cost_keeps_ten_planned_hypotheses(self):
        self.missing_state_metric = True
        result = self.execute()
        comparisons = result['strata'][0]['comparisons']
        metric = comparisons[1]['metrics']['provider_input_tokens']
        self.assertIsNone(metric['p_holm'])
        self.assertIsNone(metric['p_raw'])
        self.assertEqual(metric['holm_family_size'],10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
