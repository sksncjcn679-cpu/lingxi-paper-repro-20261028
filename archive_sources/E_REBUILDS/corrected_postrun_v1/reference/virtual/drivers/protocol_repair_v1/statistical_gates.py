"""New-study-only prospective power/budget gate and frozen v3 formal entry.

No runtime, driver, credential, environment, provider or historical-result import.
Run prospective only after the 144 public units terminate and real timing stops.
Run formal only after the independent complete formal freeze. Both write new
versioned files, never replace raw data or a previously produced analysis.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

STUDY = 'protocol_repair_20261020_v1'
OUT = Path('/evidence') / STUDY
HERE = Path(__file__).resolve().parent
STATS = OUT / 'analysis/stats/preformal_gate_v1'
SOURCE = 'a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562'
METHODS = {
    'formal_statistics.py': '7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373',
    'formal_statistics_protocol_aligned_v3.py': '43fffd1665f8e0266f1de04b25e22ba336d3c97919796c29ab07beb4d57811bc',
}
ARMS = ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')
PAIRS = (('T0', 'S0'), ('S0', 'SS0'), ('T0', 'T1'), ('S0', 'S1'), ('SS0', 'SS1'))
PRIMARY = ('provider_input_tokens', 'observable_ipc_bytes')
SEED = 20261020
INPUTS = ('protocol.yaml', 'manifests/formal_plan.json', 'manifests/budget_limits.json',
          'manifests/original_protocol.md', 'corpus/candidate_v2/tasks.jsonl')
RAW = ('task_runs.jsonl', 'provider_calls.jsonl', 'provider_attempt_starts.jsonl', 'task_unit_starts.jsonl')
IDENTITY = ('study_id', 'phase', 'task_id', 'chain_id', 'domain', 'arm', 'repeat',
            'model', 'topology', 'task_sha256', 'effective_contract_sha256')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def id_digest(ids):
    # Exact v3 identity serialization; no change to its population checks.
    return hashlib.sha256(json.dumps(sorted(set(ids)), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def unique(values, key):
    result = {}
    for row in values:
        value = row[key]
        if value in result:
            raise ValueError('duplicate ' + key + ': ' + str(value))
        result[value] = row
    return result


def checked_inputs():
    """Bind actual new corpus content, not legacy task-ID constants alone."""
    registration = load(OUT / 'public_pilot_v1/registration.json')
    if registration['source_sha256'] != SOURCE or not registration['not_a_formal_freeze']:
        raise ValueError('unexpected public source/registration identity')
    for name in INPUTS:
        path = OUT / name
        if registration['files'].get(str(path)) != digest(path):
            raise ValueError('registered new-study input changed: ' + name)
    protocol = load(OUT / 'protocol.yaml')  # registered JSON serialization is valid YAML
    if protocol['study_id'] != STUDY or protocol['source_sha256'] != SOURCE:
        raise ValueError('wrong study/source')
    primary = protocol['paired_primary']
    if (primary['pairs'] != [list(p) for p in PAIRS] or primary['endpoints'] != list(PRIMARY)
            or primary['sole_holm_family'] != 10 or primary['bootstrap_draws'] != 10000
            or primary['state_pair_population'] != 108 or primary['other_pair_population'] != 120
            or protocol['quality']['unacceptable_loss_percentage_points'] != 5):
        raise ValueError('prespecified analysis contract changed')
    tasks = rows(OUT / 'corpus/candidate_v2/tasks.jsonl')
    cases = unique(tasks, 'task_id')
    formal = [r for r in tasks if r['split'] == 'formal']
    public = [r for r in tasks if r['split'] == 'pilot']
    if len(tasks) != 144 or len(formal) != 120 or len(public) != 24:
        raise ValueError('new 144-case split required')
    chain_sizes = Counter((r['domain'], r['chain_id']) for r in formal)
    if len(chain_sizes) != 20 or set(chain_sizes.values()) != {6}:
        raise ValueError('formal population must remain 20 complete six-case chains')
    domains = Counter(domain for domain, _ in chain_sizes)
    if len(domains) != 4 or set(domains.values()) != {5}:
        raise ValueError('formal domain allocation changed')
    public_sizes = Counter((r['domain'], r['chain_id']) for r in public)
    if len(public_sizes) != 4 or set(public_sizes.values()) != {6}:
        raise ValueError('public cohort must have one six-case chain per domain')
    applicable = sorted(r['task_id'] for r in formal if r['state_spec']['applicable'])
    if len(applicable) != 108:
        raise ValueError('expected pre-treatment 108 applicable cases')
    if len({(r['domain'], r['chain_id']) for r in formal if r['task_id'] in applicable}) != 20:
        raise ValueError('108 applicable cases must retain the registered20 chains')
    plan = load(OUT / 'manifests/formal_plan.json')
    planned = unique(plan, 'run_id')
    contracts = {u['effective_contract_sha256'] for u in plan}
    if len(contracts) != 1:
        raise ValueError('effective output contracts mixed')
    for unit in plan:
        case = cases[unit['task_id']]
        if (unit['study_id'] != STUDY or unit['task_sha256'] != case['content_sha256']
                or unit['chain_id'] != case['chain_id'] or unit['domain'] != case['domain']
                or ((unit['phase'] == 'public_calibration') != (case['split'] == 'pilot'))):
            raise ValueError('plan/corpus identity mismatch')
    main = [u for u in plan if u['phase'] == 'main']
    keys = Counter((u['task_id'], u['arm'], u['repeat']) for u in main)
    expected = {(r['task_id'], arm, repeat) for r in formal for arm in ARMS for repeat in range(3)}
    if set(keys) != expected or any(n != 1 for n in keys.values()) or len(main) != 2160:
        raise ValueError('six-arm three-repeat main plan incomplete')
    if len({(u['model'], u['topology']) for u in main}) != 1:
        raise ValueError('main confirmation must have exactly one model/topology')
    public_ids = {u['run_id'] for u in plan if u['phase'] == 'public_calibration'}
    if len(public_ids) != 144 or public_ids != set(registration['public_unit_ids']):
        raise ValueError('public plan differs from its pre-outcome registration')
    return {'protocol': protocol, 'plan': plan, 'planned': planned, 'main': main,
            'tasks': tasks, 'applicable': applicable, 'public_ids': public_ids,
            'input_sha256': {name: digest(OUT / name) for name in INPUTS},
            'public_registration_sha256': digest(OUT / 'public_pilot_v1/registration.json')}


def checked_raw(context, public_only):
    data = {name: rows(OUT / 'raw' / name) for name in RAW}
    for name, values in data.items():
        for row in values:
            unit = context['planned'].get(row['run_id'])
            if unit is None or any(row.get(k) != unit[k] for k in IDENTITY):
                raise ValueError('foreign/changed raw identity in ' + name)
            # Task-unit starts are emitted by Recorder(OUT, unit, SECRET):
            # their source is linked through the registered plan/source, while
            # execute() adds actual source/model_evidence to the other tables.
            if (name != 'task_unit_starts.jsonl' and
                    (row.get('source_sha256') != SOURCE or row.get('model_evidence') != 'real_provider')):
                raise ValueError('non-provider or wrong source raw evidence')
            if public_only and row['run_id'] not in context['public_ids']:
                raise ValueError('formal/foreign starts or outcomes present before prospective gate')
    terminal = unique(data['task_runs.jsonl'], 'run_id')
    started = unique(data['task_unit_starts.jsonl'], 'run_id')
    calls = unique(data['provider_calls.jsonl'], 'request_id')
    attempts = unique(data['provider_attempt_starts.jsonl'], 'request_id')
    if set(started) != set(terminal) or set(calls) != set(attempts):
        raise ValueError('unmatched starts: preserve pending/failed evidence; do not analyze during execution')
    for request_id, call in calls.items():
        if call['run_id'] != attempts[request_id]['run_id']:
            raise ValueError('request attribution differs between start and terminal')
    observed_calls = Counter(c['run_id'] for c in calls.values())
    if not set(observed_calls) <= set(terminal):
        raise ValueError('provider calls without terminal task: execution is not quiescent')
    for run_id, task in terminal.items():
        if (type(task.get('success')) is not bool or task.get('provider_attempts') != observed_calls[run_id]
                or observed_calls[run_id] > 3):
            raise ValueError('terminal success/attempt accounting incomplete')
    if public_only and (set(terminal) != context['public_ids'] or len(attempts) > 432):
        raise ValueError('prospective gate requires complete public144 within registered cap')
    return data


def aggregate(values):
    known = [v for v in values if finite(v)]
    return {'total': sum(known) if values and len(known) == len(values) else None,
            'observed_subtotal': sum(known) if known else None,
            'observations': len(values), 'known': len(known), 'missing': len(values) - len(known)}


def usage_component(call, component):
    usage = call.get('usage_raw')
    if call.get('usage_source') != 'provider_reported' or not isinstance(usage, dict):
        return None
    if component in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        value = usage.get(component)
    else:
        details = usage.get('prompt_tokens_details')
        cached = details.get('cached_tokens') if isinstance(details, dict) else None
        prompt = usage.get('prompt_tokens')
        if not finite(cached) or not finite(prompt) or cached > prompt:
            return None
        value = cached if component == 'cached_input_tokens' else prompt - cached
    return value if finite(value) else None


def budget_report(context, data):
    """Observed attempts include failures; absent failed usage stays unknown."""
    public = data['task_runs.jsonl']
    calls = data['provider_calls.jsonl']
    limits = load(OUT / 'manifests/budget_limits.json')
    cap = limits['maximum_attempts_per_task']
    components = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                  'cached_input_tokens', 'uncached_input_tokens')
    observed = {}
    for arm in ARMS:
        units = [r for r in public if r['arm'] == arm]
        attempts = [r for r in calls if r['arm'] == arm]
        if len(units) != 24:
            raise ValueError('public arm allocation changed')
        observed[arm] = {'task_units': len(units), 'provider_attempts': len(attempts),
                         'attempts_per_task': len(attempts) / len(units),
                         'task_wall_sec': aggregate([r.get('wall_sec') for r in units]),
                         'provider_wait_wall_sec': aggregate([r.get('wall_sec') for r in attempts]),
                         'usage': {k: aggregate([usage_component(r, k) for r in attempts]) for k in components}}
    forecasts = []
    groups = Counter((u['phase'], u['model'], u['arm']) for u in context['plan']
                     if u['phase'] != 'public_calibration')
    for (phase, model, arm), count in sorted(groups.items()):
        basis = observed[arm]
        tokens = {}
        for component in components:
            total = basis['usage'][component]['total']
            tokens[component] = None if total is None else count * total / basis['task_units']
        wall = basis['task_wall_sec']['total']
        forecasts.append({'phase': phase, 'model': model, 'arm': arm, 'task_units': count,
            'expected_provider_attempts': count * basis['attempts_per_task'],
            'expected_tokens': tokens,
            'expected_serial_task_wall_sec': None if wall is None else count * wall / basis['task_units'],
            'maximum_provider_attempts': count * cap,
            'maximum_requested_output_tokens': count * cap * limits['max_output_tokens_per_request'],
            'maximum_attempt_timeout_sec': count * cap * limits['timeout_seconds'],
            'forecast_scope': 'same-model/arm native public mean; extrapolation, not a measured formal effect'
                if phase == 'main' else 'main-model native public arm proxy; model/framework/intervention/IPC overhead may differ',
            'currency_cost': None, 'currency_status': 'UNKNOWN'})
    return {'created_utc': now(), 'study_id': STUDY, 'basis': 'only new public144 actual provider attempts/usage',
        'formal_outcomes_used': False, 'observed': observed, 'forecasts': forecasts,
        'remaining_planned_task_units': sum(r['task_units'] for r in forecasts),
        'expected_remaining_provider_attempts': sum(r['expected_provider_attempts'] for r in forecasts),
        'expected_remaining_tokens': {k: aggregate([r['expected_tokens'][k] for r in forecasts]) for k in components},
        'expected_serial_task_wall_sec': aggregate([r['expected_serial_task_wall_sec'] for r in forecasts]),
        'public_attempt_errors': sum(r.get('status') != 'OK' for r in calls),
        'public_missing_usage_calls': sum(not isinstance(r.get('usage_raw'), dict) for r in calls),
        'actual_currency_cost': None, 'actual_currency_status': 'UNKNOWN',
        'currency_budget_gate': 'UNKNOWN_UNVERIFIED_PRICES_AND_UNSPECIFIED_CURRENCY_CEILING',
        'verified_unit_prices': {'uncached_input': None, 'cached_input': None, 'output': None, 'per_request': None},
        'currency_formula': 'sum(uncached_input*input_price + cached_input*cached_price + output*output_price + request_fee)',
        'retry_allowance': 'observed attempt mean already includes public retries; separate maximum-attempt ceiling is not a token or currency upper bound',
        'time_scope': 'planning serial sum; 60-second request timeouts exclude local work/backoff/cleanup and are not an end-to-end upper bound',
        'limits': limits, 'missing_policy': 'null; observed subtotal is explicitly partial, never total billing',
        'gate_authorizes_provider_execution': False,
        'budget_limitation': 'finite technical attempt/request-byte/reported-token caps do not verify money affordability; no price/discount or failed-request bill is inferred'}


def power_report(context, data):
    """Predeclared loss/ICC sensitivity, no estimate from any formal outcome."""
    import numpy as np
    from scipy.stats import beta
    public = data['task_runs.jsonl']
    bytask = defaultdict(dict)
    for row in public:
        bytask[row['task_id']][row['arm']] = row
    losses = [not all(r['success'] for r in arms.values()) for arms in bytask.values()]
    failures = sum(losses)
    upper = 1.0 if failures == len(losses) else float(beta.ppf(.95, failures + 1, len(losses) - failures))
    # The task-binomial value above has no arbitrary-chain-dependence coverage.
    # Add a conservative domain-simultaneous chain-any-failure envelope: each
    # public domain has only one chain, so uncertainty must remain very wide.
    public_by_domain = defaultdict(list)
    for task_id, arms in bytask.items():
        public_by_domain[next(iter(arms.values()))['domain']].append(task_id)
    domain_bounds = []
    for domain, task_ids in sorted(public_by_domain.items()):
        any_failure = any(not all(r['success'] for r in bytask[task_id].values()) for task_id in task_ids)
        domain_bounds.append({'domain': domain, 'chains': 1, 'task_weight': len(task_ids) / 24,
            'chain_any_arm_failure': any_failure,
            'chain_failure_probability_upper95_simultaneous': 1.0 if any_failure else 1 - .05 / len(public_by_domain)})
    chain_upper = sum(r['task_weight'] * r['chain_failure_probability_upper95_simultaneous'] for r in domain_bounds)
    paired = []
    for a, b in PAIRS:
        paired.append({'baseline': a, 'target': b, 'tasks': len(bytask),
            'paired_losses': sum(arms[a]['success'] and not arms[b]['success'] for arms in bytask.values()),
            'paired_gains': sum(not arms[a]['success'] and arms[b]['success'] for arms in bytask.values()),
            'baseline_successes': sum(arms[a]['success'] for arms in bytask.values()),
            'target_successes': sum(arms[b]['success'] for arms in bytask.values())})
    rng = np.random.default_rng(SEED)
    simulations, chains, per_domain = 10000, 20, 5
    sensitivity = []
    best_lower = -(1 - (.05 / 4) ** (1 / per_domain))
    applicable_sizes = Counter((r['domain'], r['chain_id']) for r in context['tasks']
                              if r['task_id'] in context['applicable'])
    by_domain = defaultdict(list)
    for (domain, _), count in applicable_sizes.items():
        by_domain[domain].append(count)
    best_applicable_lower = -sum(sum(sizes) / 108 * min(1.0,
        len(sizes) * max(sizes) / sum(sizes) * (1 - (.05 / len(by_domain)) ** (1 / len(sizes))))
        for sizes in by_domain.values())
    for failure in sorted(set([0.0, .01, .05, .10, failures / len(losses), upper, chain_upper])):
        for icc in (0.0, .2, .5, .9):
            for difference in (0.0, -.02, -.05):
                gain = failure * (1 - failure)
                loss = min(1 - gain, gain - difference)
                probabilities = np.asarray([loss, gain, 1 - loss - gain])
                active = probabilities > 0
                if icc == 0:
                    pp = np.broadcast_to(probabilities, (simulations, chains, 3))
                else:
                    pp = np.zeros((simulations, chains, 3))
                    pp[..., active] = rng.dirichlet(probabilities[active] * (1 / icc - 1), size=(simulations, chains))
                lost = rng.binomial(6, pp[..., 0])
                remain = 6 - lost
                cond_gain = np.divide(pp[..., 1], 1 - pp[..., 0], out=np.zeros_like(pp[..., 1]), where=pp[..., 0] < 1)
                gained = rng.binomial(remain, np.clip(cond_gain, 0, 1))
                counts = (lost > 0).reshape(simulations, 4, per_domain).sum(axis=2)
                bounds = beta.ppf(1 - .05 / 4, counts + 1, per_domain - counts)
                bounds[counts == per_domain] = 1.0
                lower = -bounds.mean(axis=1)
                probability = float(np.mean(lower > -.05))
                sensitivity.append({'formal_chains': chains, 'formal_tasks': 120,
                    'assumed_failure_rate': failure, 'assumed_within_chain_icc': icc,
                    'true_quality_difference': difference, 'simulations': simulations,
                    'mean_observed_difference': float(((gained - lost) / 6).mean()),
                    'noninferiority_power': probability,
                    'mc_upper95_if_zero': 1 - .05 ** (1 / simulations) if probability == 0 else None,
                    'safety_lower_coverage': float(np.mean(lower <= difference)),
                    'best_possible_safety_lower': best_lower})
    return {'created_utc': now(), 'study_id': STUDY, 'seed': SEED, 'public_units': 144,
        'public_unique_tasks': 24, 'public_chains': 4, 'formal_outcomes_used': False,
        'public_paired_quality': paired, 'public_any_arm_failure_task_fraction': failures / len(losses),
        'heuristic_task_independence_binomial_upper95_for_sensitivity_only': upper,
        'heuristic_bound_has_arbitrary_chain_dependence_coverage': False,
        'conservative_public_chain_any_failure_upper95': chain_upper,
        'conservative_public_domain_bounds': domain_bounds,
        'pilot_limitation': 'one chain/domain; ICC and population failure/discordance not estimated. Task-binomial upper is a task-independence heuristic sensitivity value, not a valid chain-correlated CI/safety bound. The separate chain-any-failure simultaneous-domain envelope allows arbitrary within-chain dependence and remains wide. Public SS state override does not alter formal applicability.',
        'simulation_model': 'paired loss/gain/same Dirichlet-multinomial six-case chain, ICC grid; four domains with five chains each; simultaneous-domain exact chain-any-loss safety bound',
        'risk_margin': -.05, 'one_sided_alpha': .05, 'minimum_effect_of_interest': .05,
        'structural_power_upper_bound': 0.0 if best_lower <= -.05 else None,
        'structural_power_reason': 'even zero paired loss in all20 chains cannot exclude -5pp under the fixed conservative bound; 108-case unequal-chain inflation cannot improve it',
        'best_possible_all120_safety_lower': best_lower, 'sensitivity': sensitivity,
        'best_possible_state108_safety_lower': best_applicable_lower,
        'state108_tasks_per_domain_chain': dict(by_domain),
        'decision': 'KEEP_120_FORMAL_TASKS_20_CHAINS_AND_WITHHOLD_NONINFERIORITY_WORDING',
        'quality_noninferiority_wording': 'WITHHELD_PROSPECTIVELY', 'automatic_expansion': False,
        'authority': 'original protocol section6.3 and registered new protocol quality.if_underpowered'}


def prospective():
    context = checked_inputs()
    data = checked_raw(context, public_only=True)
    # Check sources before creating anything. Never import a real driver.
    sources = {name: HERE.parent / name for name in METHODS}
    for name, path in sources.items():
        if digest(path) != METHODS[name]:
            raise ValueError('verified numerical method source changed: ' + name)
    power = power_report(context, data)
    budget = budget_report(context, data)
    STATS.mkdir(parents=True, exist_ok=False)
    source_dir = STATS / 'sources'
    source_dir.mkdir()
    for name, path in sources.items():
        with (source_dir / name).open('xb') as stream:
            stream.write(path.read_bytes())
    with (source_dir / 'statistical_gates.py').open('xb') as stream:
        stream.write(Path(__file__).read_bytes())
    for name in RAW:
        with (STATS / ('public_' + name)).open('xb') as stream:
            stream.write((OUT / 'raw' / name).read_bytes())
    write_new(STATS / 'prospective_power.json', power)
    write_new(STATS / 'preformal_budget_estimate.json', budget)
    contract = {'schema_version': 1, 'created_utc': now(), 'study_id': STUDY,
        'source_sha256': SOURCE, 'input_sha256': context['input_sha256'],
        'public_registration_sha256': context['public_registration_sha256'],
        'formal_tasks': 120, 'formal_chains': 20,
        'formal_task_ids_sha256': id_digest(u['task_id'] for u in context['main']),
        'applicable_task_ids': context['applicable'], 'applicable_task_ids_sha256': id_digest(context['applicable']),
        'seed': SEED, 'pairs': [list(p) for p in PAIRS], 'primary_endpoints': list(PRIMARY),
        'sole_holm_family': 10, 'active_confirmatory_families': 1,
        'population': 'S0_SS0:108 frozen applicable tasks; remaining four pairs:all120',
        'bootstrap_draws': 10000, 'sign_flip': 'exact chain sign flip; 20 chains, 2^20 enumerations',
        'numerical_source_sha256': METHODS, 'quality_risk_margin': -.05,
        'quality_method': 'unchanged v3 kernel: conservative domain-exact chain-any-loss/gain safety bound, unequal-chain max-weight inflation',
        'exploratory_phases': 'all preplanned nonpublic phases separately; cross_model/multiprocess within-phase pairs have raw p only; external framework system references stay separate',
        'matched_phase_effects': 'eight ablations plus two codecs vs matched ablation_baseline; four memory conditions vs matched empty control;14 exploratory comparisons, no added Holm family',
        'quality_noninferiority_wording': power['quality_noninferiority_wording'],
        'decision': power['decision'], 'formal_outcomes_used': False,
        'missing_quality': 'all planned units; absent terminal outcome is failure',
        'missing_cost': 'null; incomplete all-planned endpoint has no CI/p; no zero or success-only replacement',
        'extrapolated_budget_is_not_currency_verification': True,
        'files': {str(path): digest(path) for path in sorted(STATS.rglob('*')) if path.is_file()}}
    write_new(STATS / 'statistical_contract.json', contract)
    gate = {'created_utc': now(), 'status': 'PROSPECTIVE_STATISTICS_REGISTERED_NONINFERIORITY_WITHHELD',
        'study_id': STUDY, 'formal_outcomes_used': False, 'formal_freeze': False,
        'statistics': 'PASS_FIXED_120_WITH_WITHHELD_WORDING',
        'currency_budget': budget['currency_budget_gate'], 'provider_execution_authorized': False,
        'files': {str(path): digest(path) for path in sorted(STATS.rglob('*')) if path.is_file()}}
    write_new(STATS / 'gate.json', gate)
    return {'status': gate['status'], 'contract': str(STATS / 'statistical_contract.json'),
            'currency_budget': gate['currency_budget'], 'formal_units_executed_by_this_command': 0}


def load_verified_v3(contract):
    """Execute the unchanged v3 wrapper, rebinding only new registered ID hashes."""
    directory = STATS / 'sources'
    for name, expected in METHODS.items():
        if digest(directory / name) != expected or contract['numerical_source_sha256'].get(name) != expected:
            raise ValueError('frozen numerical source changed')
    def module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value
    frozen = module('_repair_frozen_statistics', directory / 'formal_statistics.py')
    previous = sys.modules.get('formal_statistics')
    sys.modules['formal_statistics'] = frozen
    try:
        wrapper = module('_repair_v3', directory / 'formal_statistics_protocol_aligned_v3.py')
    finally:
        if previous is None:
            sys.modules.pop('formal_statistics', None)
        else:
            sys.modules['formal_statistics'] = previous
    wrapper.FORMAL_TASK_IDS_SHA256 = contract['formal_task_ids_sha256']
    wrapper.APPLICABLE_TASK_IDS_SHA256 = contract['applicable_task_ids_sha256']
    return wrapper


def exploratory_only(value):
    """Remove every adjusted-p/family interpretation from auxiliary analyses."""
    if isinstance(value, list):
        return [exploratory_only(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: exploratory_only(item) for key, item in value.items()
              if key not in ('p_holm', 'holm_family', 'holm_family_size',
                             'primary_family_test_count', 'active_confirmatory_families')}
    if 'p_raw' in result:
        result['significance_scope'] = 'exploratory_raw_p_only_outside_sole_main_ten_test_family'
    if 'comparisons' in result or 'strata' in result:
        result['active_confirmatory_family'] = False
    return result


def matched_effect(kernel, plan_a, rows_a, plan_b, rows_b, label_a, label_b, seed):
    """Original report.paired logic; fixed planned pairing, no success filter."""
    def key(row):
        return tuple(row[k] for k in ('task_id', 'repeat', 'model', 'topology'))
    def keyed(values):
        result = {}
        for row in values:
            if key(row) in result:
                raise ValueError('duplicate matched-phase key')
            result[key(row)] = row
        return result
    pa, pb = keyed(plan_a), keyed(plan_b)
    aa, bb = keyed(rows_a), keyed(rows_b)
    if not pa or set(pa) != set(pb):
        raise ValueError('preplanned matched phases do not match: ' + label_a + '/' + label_b)
    result = {'baseline': label_a, 'target': label_b, 'planned_paired_units': len(pa),
        'baseline_terminal_units': len(aa), 'target_terminal_units': len(bb),
        'status': 'INCOMPLETE', 'active_confirmatory_family': False,
        'inference_scope': 'preplanned matched60 exploratory comparison; raw p only',
        'quality_noninferiority_wording': 'WITHHELD_PROSPECTIVELY'}
    if set(aa) != set(pa) or set(bb) != set(pb):
        result['effect_numbers'] = 'WITHHELD_UNTIL_BOTH_PHASES_TERMINAL'
        return result
    pairs = []
    for item in sorted(pa):
        a, b = dict(aa[item]), dict(bb[item])
        a['quality_success'], b['quality_success'] = int(a['success']), int(b['success'])
        p = pa[item]
        if any(p[k] != pb[item][k] for k in ('chain_id', 'domain', 'task_sha256')):
            raise ValueError('matched phases changed task/chain content')
        pairs.append({'task_id': p['task_id'], 'chain_id': p['chain_id'], 'domain': p['domain'],
                      'repeat': p['repeat'], 'a': a, 'b': b})
    result['quality'] = kernel.cluster_bootstrap(pairs, 'quality_success', seed)
    result['quality'].update(kernel.quality_safety_bound(pairs))
    result['metrics'] = {}
    for index, metric in enumerate(PRIMARY + ('wall_sec', 'local_state_materialized_bytes')):
        valid = [p for p in pairs if finite(p['a'].get(metric)) and finite(p['b'].get(metric))]
        if len(valid) != len(pairs):
            result['metrics'][metric] = {'status': 'INCOMPLETE', 'planned_pairs': len(pairs),
                'measured_pairs': len(valid), 'p_raw': None,
                'reason': 'unknown cost retained; no zero/success-only substitution'}
        else:
            stats = kernel.cluster_bootstrap(valid, metric, seed + 100 + index)
            stats['efficiency_interpretation_requires_quality_constraint'] = True
            stats['both_success_paired_units'] = sum(p['a']['success'] and p['b']['success'] for p in pairs)
            stats['early_failure_warning'] = stats['both_success_paired_units'] < len(pairs)
            result['metrics'][metric] = stats
    result['status'] = ('COMPLETE' if all(v['status'] == 'COMPLETE' for v in result['metrics'].values())
                        else 'COMPLETE_TASKS_UNKNOWN_COST')
    return exploratory_only(result)


def exploratory_phases(context, data, kernel):
    """All frozen phases retained separately; public never joins a formal pool."""
    plan_by_phase, rows_by_phase = defaultdict(list), defaultdict(list)
    for unit in context['plan']:
        if unit['phase'] != 'public_calibration':
            plan_by_phase[unit['phase']].append(unit)
    for row in data['task_runs.jsonl']:
        if row['phase'] != 'public_calibration':
            rows_by_phase[row['phase']].append(row)
    phases = {}
    for index, (phase, plan) in enumerate(sorted(plan_by_phase.items())):
        if phase == 'main':
            continue
        actual = rows_by_phase[phase]
        complete = {r['run_id'] for r in actual} == {r['run_id'] for r in plan}
        summary = {'status': 'COMPLETE' if complete else 'INCOMPLETE',
            'planned_units': len(plan), 'terminal_units': len(actual),
            'failed_terminal_units': sum(not r['success'] for r in actual),
            'active_confirmatory_family': False,
            'inference_scope': 'exploratory phase; no Holm adjustment/confirmatory family',
            'quality_noninferiority_wording': 'WITHHELD_PROSPECTIVELY'}
        if complete:
            summary['arm_summaries'] = {arm: {'task_units': sum(r['arm'] == arm for r in actual),
                'successes': sum(r['arm'] == arm and r['success'] for r in actual),
                'raw_distribution': [{k: r.get(k) for k in ('task_id', 'chain_id', 'domain', 'repeat',
                    'success', *PRIMARY, 'wall_sec', 'local_state_materialized_bytes')}
                    for r in actual if r['arm'] == arm]}
                for arm in sorted({r['arm'] for r in plan})}
            if phase in ('cross_model', 'multiprocess'):
                summary['within_phase_pairs'] = exploratory_only(kernel.analyze(actual, plan, SEED + 1000 + index))
            if phase.startswith('external_'):
                summary['comparability'] = 'SYSTEM_REFERENCE_ONLY_PENDING_INDEPENDENT_EQUIVALENCE_GATE; different topology, no mechanism-attribution test'
        else:
            summary['effect_numbers'] = 'WITHHELD_UNTIL_FULL_PHASE_TERMINAL; planned denominator retained'
        phases[phase] = summary
    matched = {}
    for index, phase in enumerate(sorted(plan_by_phase)):
        baseline = ('ablation_baseline' if phase.startswith(('ablation_', 'serialization_'))
                    and phase != 'ablation_baseline' else 'memory_condition_none'
                    if phase in ('memory_condition_related', 'memory_condition_unrelated',
                                 'memory_condition_stale', 'memory_condition_wrong') else None)
        if baseline:
            matched[phase] = matched_effect(kernel, plan_by_phase[baseline], rows_by_phase[baseline],
                plan_by_phase[phase], rows_by_phase[phase], baseline, phase, SEED + 2000 + index)
    return {'formal_phase_statistics': phases, 'matched_phase_effects': matched,
            'scope': 'all planned formal phases, no public or historical pooling; every nonmain result exploratory'}


def formal(version):
    if not version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in version):
        raise ValueError('version must be a simple new filename component')
    context = checked_inputs()
    contract_path = STATS / 'statistical_contract.json'
    contract = load(contract_path)
    if (contract['study_id'] != STUDY or contract['source_sha256'] != SOURCE
            or contract['input_sha256'] != context['input_sha256']
            or contract['public_registration_sha256'] != context['public_registration_sha256']
            or contract['applicable_task_ids'] != context['applicable']
            or contract['quality_noninferiority_wording'] != 'WITHHELD_PROSPECTIVELY'):
        raise ValueError('new-study prospective contract identity changed')
    freeze = load(OUT / 'manifests/formal-freeze.json')
    if freeze['source_sha256'] != SOURCE:
        raise ValueError('formal source freeze differs')
    required = [OUT / name for name in INPUTS]
    required += [OUT / 'public_pilot_v1/registration.json', contract_path, STATS / 'gate.json',
                 STATS / 'prospective_power.json', STATS / 'preformal_budget_estimate.json',
                 Path(__file__).resolve(), STATS / 'sources/statistical_gates.py']
    required += [STATS / 'sources' / name for name in METHODS]
    for path in required:
        if freeze['files'].get(str(path)) != digest(path):
            raise ValueError('missing/changed formal freeze binding: ' + str(path))
    for name, expected in contract['files'].items():
        path = Path(name)
        if not path.resolve().is_relative_to(STATS.resolve()) or digest(path) != expected:
            raise ValueError('prospective file identity changed')
    if digest(Path(__file__)) != digest(STATS / 'sources/statistical_gates.py'):
        raise ValueError('formal analysis entry changed since prospective registration')
    data = checked_raw(context, public_only=False)
    formal_starts = [r for r in data['task_unit_starts.jsonl'] if r['phase'] != 'public_calibration']
    if formal_starts:
        # Existing receipts supply UTC timestamps; never repair their chronology.
        stamp = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
        first_start = min(stamp(r['start_utc']) for r in formal_starts)
        freeze_stamp = freeze.get('frozen_at_utc', freeze.get('created_utc'))
        if (freeze_stamp is None or stamp(contract['created_utc']) >= first_start
                or stamp(freeze_stamp) >= first_start):
            raise ValueError('prospective statistical registration/full freeze must precede every formal start')
    for name in RAW:
        frozen_public = rows(STATS / ('public_' + name))
        actual_public = [r for r in data[name] if r['phase'] == 'public_calibration']
        if actual_public != frozen_public:
            raise ValueError('public evidence changed after prospective gate')
    runs = [r for r in data['task_runs.jsonl'] if r['phase'] == 'main']
    numerical = load_verified_v3(contract)
    report = numerical.analyze(runs, context['main'], context['applicable'], seed=contract['seed'])
    report.update(exploratory_phases(context, data, numerical.frozen))
    report.update({'study_id': STUDY, 'analysis_version': 'protocol_repair_v1_verified_v3_numerics',
        'analysis_correction': 'new study registered before formal outcomes; verified v3 numerical algorithms unchanged',
        'formal_freeze_sha256': digest(OUT / 'manifests/formal-freeze.json'),
        'statistical_contract_sha256': digest(contract_path), 'input_sha256': context['input_sha256'],
        'raw_input_sha256': {name: digest(OUT / 'raw' / name) for name in RAW},
        'no_historical_outcomes_used': True,
        'collection_status': 'COMPLETE' if len(runs) == 2160 else 'INCOMPLETE_ALL_PLANNED_DENOMINATORS_RETAINED',
        'quality_noninferiority_wording': 'WITHHELD_PROSPECTIVELY'})
    target = OUT / 'analysis/stats' / (version + '.json')
    write_new(target, report)
    return {'status': report['collection_status'], 'output': str(target),
            'sole_holm_family': 10, 'state_population': 108, 'other_populations': 120}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prospective', 'formal'))
    parser.add_argument('--version', default='formal_primary_v1')
    args = parser.parse_args()
    print(json.dumps(prospective() if args.command == 'prospective' else formal(args.version), ensure_ascii=False))


if __name__ == '__main__':
    main()
