"""Original protocol 4.1 population correction; frozen analysis stays unchanged.

analyze(runs, plan, applicable_task_ids, seed=20261019) returns the compatible
strata/comparisons/metrics structure with ONE mixed-population ten-test Holm
family. The full120 frozen replay remains separately marked archive_only.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json

import formal_statistics as frozen

FROZEN_STATISTICS_SHA256 = '7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373'
FORMAL_TASK_IDS_SHA256 = '6c6a5dc0568cf33ce59b64251e3cec119e356bb07a31ca4afcdd0c5c21c276ef'
APPLICABLE_TASK_IDS_SHA256 = 'c39c2b604622aad618171935116bfb1dc4b1ca6be063e0ab32a5addc2770ac52'
STATE_PAIR = ('S0', 'SS0')
PAIRS = [('T0','S0'), STATE_PAIR, ('T0','T1'), ('S0','S1'), ('SS0','SS1')]
PRIMARY = ['provider_input_tokens', 'observable_ipc_bytes']


def _digest_ids(ids):
    return hashlib.sha256(json.dumps(sorted(set(ids)), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _verify_inputs(plan, applicable):
    if hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest() != FROZEN_STATISTICS_SHA256:
        raise ValueError('frozen statistical method source changed')
    ids = {unit['task_id'] for unit in plan}
    if len(ids) != 120 or _digest_ids(ids) != FORMAL_TASK_IDS_SHA256:
        raise ValueError('v3 primary API requires the registered 120-task main population')
    if len(applicable) != 108 or _digest_ids(applicable) != APPLICABLE_TASK_IDS_SHA256:
        raise ValueError('applicability must be the registered pre-outcome 108-task label set')
    if not applicable <= ids:
        raise ValueError('state applicability IDs are not within main population')
    if len(plan) != 2160 or any(unit.get('phase', 'main') != 'main' for unit in plan):
        raise ValueError('v3 confirmation is main phase only, six arms and three frozen repeats')
    groups = Counter((u['task_id'], u['arm'], u['repeat']) for u in plan)
    expected = {(task, arm, repeat) for task in ids for arm in ('T0','S0','SS0','T1','S1','SS1') for repeat in (0,1,2)}
    if set(groups) != expected or any(count != 1 for count in groups.values()):
        raise ValueError('registered main plan pairing/repeats incomplete or duplicated')
    if len({(u['model'], u['topology']) for u in plan}) != 1:
        raise ValueError('main confirmation has one frozen model/topology; other phases remain separate')


def _pair_key(comparison):
    return comparison['baseline'], comparison['target']


def _stratum_key(stratum):
    return stratum['model'], stratum['topology']


def _archive(full):
    """Keep every original numerical value, override confirmatory interpretation."""
    archived = deepcopy(full)
    archived['analysis_status'] = 'archive_only'
    archived['active_confirmatory_family'] = False
    archived['interpretation'] = ('frozen all120 implementation replay; numerical p_holm values are retained for provenance only; '
                                  'not a second confirmatory route and never selected against v3')
    for stratum in archived['strata']:
        stratum['analysis_status'] = 'archive_only'
        for comparison in stratum['comparisons']:
            comparison['significance_scope'] = 'archive_only'
            for metric in comparison.get('metrics', {}).values():
                if 'holm_family' in metric:
                    metric['archived_original_holm_family_label'] = metric['holm_family']
                    metric['holm_family'] = 'archive_only_frozen_all120_replay'
                metric['significance_scope'] = 'archive_only_no_confirmatory_claim'
                if 'p_holm' in metric:
                    metric['p_holm_interpretation'] = 'archive_only_numerical_value_retained_not_used_for_confirmation'
    return archived


def _descriptive_comparison(comparison, scope):
    value = deepcopy(comparison)
    value['significance_scope'] = scope
    value['active_confirmatory_family'] = False
    for metric in value.get('metrics', {}).values():
        for key in ('p_holm', 'holm_family', 'holm_family_size'):
            metric.pop(key, None)
        metric['significance_scope'] = 'descriptive_p_raw_only_no_additional_confirmatory_family'
    return value


def analyze(runs, plan, applicable_task_ids, seed=20261019):
    """One original-protocol primary family; no filter on observed outcomes/state use.

    Numerical methods and seed offsets are supplied by the exact frozen module.
    Only the S0_SS0 population and joint Holm inputs differ from its all120 call.
    """
    runs, plan = list(runs), list(plan)
    applicable = set(applicable_task_ids)
    _verify_inputs(plan, applicable)
    all120 = frozen.analyze(runs, plan, seed=seed)
    subset_plan = [unit for unit in plan if unit['task_id'] in applicable]
    subset_runs = [unit for unit in runs if unit['task_id'] in applicable]
    # Exact same seed and pair/endpoint indices as the frozen call. Subset
    # adjusted p-values are not used or exposed as a second confirmatory family.
    state108 = frozen.analyze(subset_runs, subset_plan, seed=seed)
    subset_by_stratum = {_stratum_key(s): s for s in state108['strata']}
    primary = deepcopy(all120)
    primary.update({'schema_version': 3, 'analysis_version': 'original_protocol_aligned_v3',
        'analysis_status': 'sole_primary_family',
        'analysis_correction': 'implementation omission corrected to original protocol section4.1; metadata registered during collection before formal effect inspection',
        'research_protocol_change': False,
        'primary_holm_rule': 'one joint family of 10: S0_SS0 two endpoints on frozen108, other four pairs eight endpoints on all120',
        'primary_family_test_count': 10, 'active_confirmatory_families': 1,
        'state_applicability_basis': 'frozen pre-treatment state_spec.applicable; never success or observed state consumption',
        'formal_unique_tasks':120,'state_applicable_unique_tasks':108,
        'applicable_task_ids_sha256':APPLICABLE_TASK_IDS_SHA256,
        'source_method_sha256':FROZEN_STATISTICS_SHA256,
        'quality_noninferiority_wording':'WITHHELD_BY_PROSPECTIVE_GATE'})
    deployment = []
    for stratum in primary['strata']:
        key = _stratum_key(stratum)
        subset = subset_by_stratum[key]
        subset_comparisons = {_pair_key(c): c for c in subset['comparisons']}
        full_comparisons = {_pair_key(c): c for c in stratum['comparisons']}
        deployment.append({'model':stratum['model'],'topology':stratum['topology'],
            'comparison':_descriptive_comparison(full_comparisons[STATE_PAIR],
                'all120_overall_deployment_strategy_descriptive_not_pure_state_increment')})
        comparisons = []
        for pair in PAIRS:
            comparison = deepcopy(subset_comparisons[pair] if pair == STATE_PAIR else full_comparisons[pair])
            n = 108 if pair == STATE_PAIR else 120
            comparison.update({'population':'predeclared_state_applicable' if n==108 else 'all_formal_tasks',
                'planned_unique_tasks':n,'planned_chains':20,'planned_repeats':3,
                'population_selection':'pre-outcome frozen applicability labels only' if n==108 else 'all frozen formal tasks',
                'significance_scope':'sole_protocol_aligned_primary_family'})
            for name, metric in comparison.get('metrics', {}).items():
                if name not in PRIMARY:
                    metric['significance_scope'] = 'secondary_exploratory_no_additional_confirmatory_family'
            comparisons.append(comparison)
        family = [(comparison, endpoint) for comparison in comparisons for endpoint in PRIMARY]
        raw = [comparison.get('metrics', {}).get(endpoint, {}).get('p_raw') for comparison, endpoint in family]
        adjusted = frozen.holm(raw)
        if len(raw) != 10:
            raise AssertionError('primary Holm family must have exactly ten hypotheses')
        for (comparison, endpoint), value in zip(family, adjusted):
            metric = comparison['metrics'][endpoint]
            metric.update({'p_holm':value,'holm_family_size':10,
                'holm_family':'sole_protocol_aligned_mixed_population_ten_test_family',
                'significance_scope':'sole_protocol_aligned_primary_family',
                'primary_population_unique_tasks':comparison['planned_unique_tasks'],
                'hypothesis_id':comparison['baseline']+'_'+comparison['target']+'__'+endpoint})
        stratum['comparisons'] = comparisons
        stratum['primary_family_test_count'] = 10
        stratum['active_confirmatory_family'] = True
    descriptive_subset = deepcopy(state108)
    descriptive_subset.update({'analysis_status':'descriptive_only','active_confirmatory_family':False,
        'scope':'predeclared108 descriptions; state primary is reported only in top-level corrected strata; no second Holm family'})
    for stratum in descriptive_subset['strata']:
        stratum['comparisons'] = [_descriptive_comparison(c,'predeclared108_descriptive_no_second_confirmatory_family')
                                   for c in stratum['comparisons']]
    primary['all120_frozen_replay_archive'] = _archive(all120)
    primary['full120_state_deployment_descriptive'] = deployment
    primary['state_applicable_descriptive'] = descriptive_subset
    return primary
