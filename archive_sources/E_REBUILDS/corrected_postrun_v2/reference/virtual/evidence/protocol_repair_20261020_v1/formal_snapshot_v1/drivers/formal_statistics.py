"""Frozen protocol section 6 statistics. Never infer missing cost as zero.

Public API: analyze(task_runs: list[dict], planned_units: list[dict], seed=20261019).
Use one experiment phase at a time; separate model/topology strata are returned.
The caller selects the frozen primary phase, never selects on success or effect.
"""
from collections import defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import math

import numpy as np
from scipy.stats import beta

PAIRS = [('T0', 'S0'), ('S0', 'SS0'), ('T0', 'T1'), ('S0', 'S1'), ('SS0', 'SS1')]
PRIMARY = ['provider_input_tokens', 'observable_ipc_bytes']
SECONDARY = ['wall_sec', 'provider_wait_sec', 'local_state_materialized_bytes']
KEY = ('task_id', 'arm', 'repeat', 'model', 'topology')
BOOTSTRAPS = 10000
PERMUTATIONS = 100000


def _key(row):
    return tuple(row[k] for k in KEY)


def _unique(rows):
    result = {}
    for row in rows:
        key = _key(row)
        if key in result:
            raise ValueError(f'duplicate planned/result key: {key}')
        result[key] = row
    return result


def holm(pvalues):
    """Holm step-down adjustment; missing tests remain in the planned family."""
    array = np.asarray([1.0 if p is None else p for p in pvalues], dtype=float)
    order = np.argsort(array, kind='stable')
    adjusted = np.empty(len(array))
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(array) - rank) * array[index]))
        adjusted[index] = running
    return [None if pvalues[i] is None else float(adjusted[i]) for i in range(len(array))]


def paired_signflip(chain_differences, weights=None, seed=20261019, draws=PERMUTATIONS):
    """Two-sided paired sign flip after chain aggregation, exact through 20 chains."""
    values = np.asarray(chain_differences, dtype=float)
    if not len(values):
        return {'p_raw': None, 'status': 'NO_CHAINS'}
    weights = np.full(len(values), 1 / len(values)) if weights is None else np.asarray(weights)
    values = values * weights
    observed = float(abs(values.sum()))
    if np.all(values == 0):
        return {'p_raw': 1.0, 'draws': 2 ** len(values) if len(values) <= 20 else draws,
                'method': 'exact_chain_sign_flip' if len(values) <= 20 else 'monte_carlo_chain_sign_flip'}
    rng = np.random.default_rng(seed)
    exact = len(values) <= 20
    total = 2 ** len(values) if exact else max(PERMUTATIONS, int(draws))
    exceed = 0
    tolerance = max(1e-12, observed * 1e-12)
    for start in range(0, total, 8192):
        size = min(8192, total - start)
        if exact:
            codes = np.arange(start, start + size, dtype=np.uint64)
            signs = 2 * ((codes[:, None] >> np.arange(len(values), dtype=np.uint64)) & 1).astype(np.int8) - 1
        else:
            signs = rng.integers(0, 2, size=(size, len(values)), dtype=np.int8) * 2 - 1
        statistics = np.einsum('ij,j->i', signs, values)
        exceed += int(np.count_nonzero(np.abs(statistics) >= observed - tolerance))
    pvalue = exceed / total if exact else (exceed + 1) / (total + 1)
    return {'p_raw': pvalue, 'draws': total, 'extreme_draws': exceed,
            'method': 'exact_chain_sign_flip' if exact else 'monte_carlo_chain_sign_flip'}


def _chain_arrays(task_pairs, metric):
    """Average repeats per task first; preserve task and chain identities."""
    bytask = defaultdict(list)
    for pair in task_pairs:
        bytask[(pair['domain'], pair['chain_id'], pair['task_id'])].append(pair)
    chains = defaultdict(list)
    task_values = []
    for (domain, chain, task), repeats in sorted(bytask.items()):
        a = float(np.mean([p['a'][metric] for p in repeats]))
        b = float(np.mean([p['b'][metric] for p in repeats]))
        value = {'domain': domain, 'chain_id': chain, 'task_id': task, 'a': a, 'b': b,
                 'difference': b - a, 'repeat_count': len(repeats)}
        chains[(domain, chain)].append(value)
        task_values.append(value)
    grouped = defaultdict(list)
    for (domain, chain), values in sorted(chains.items()):
        grouped[domain].append({'chain_id': chain, 'n_tasks': len(values),
                                'a': float(np.mean([v['a'] for v in values])),
                                'b': float(np.mean([v['b'] for v in values]))})
    return grouped, task_values


def cluster_bootstrap(task_pairs, metric, seed=20261019, draws=BOOTSTRAPS):
    """Sample chains independently inside every domain, 10,000 replicates."""
    grouped, task_values = _chain_arrays(task_pairs, metric)
    if not task_values:
        return {'status': 'NO_PAIRS'}
    rng = np.random.default_rng(seed)
    draws = max(BOOTSTRAPS, draws)
    numerator_a = np.zeros(draws)
    numerator_b = np.zeros(draws)
    denominator = np.zeros(draws)
    chain_differences, chain_weights = [], []
    n_tasks = len(task_values)
    for domain, rows in sorted(grouped.items()):
        lengths = np.asarray([r['n_tasks'] for r in rows], dtype=float)
        aa = np.asarray([r['a'] for r in rows])
        bb = np.asarray([r['b'] for r in rows])
        selected = rng.integers(0, len(rows), size=(draws, len(rows)))
        numerator_a += (aa[selected] * lengths[selected]).sum(axis=1)
        numerator_b += (bb[selected] * lengths[selected]).sum(axis=1)
        denominator += lengths[selected].sum(axis=1)
        chain_differences.extend((bb - aa).tolist())
        chain_weights.extend((lengths / n_tasks).tolist())
    a = float(np.mean([v['a'] for v in task_values]))
    b = float(np.mean([v['b'] for v in task_values]))
    bootdiff = (numerator_b - numerator_a) / denominator
    relative = np.divide(numerator_b - numerator_a, numerator_a,
                         out=np.full(draws, np.nan), where=numerator_a != 0)
    result = {'status': 'COMPLETE', 'baseline_mean': a, 'target_mean': b, 'paired_difference': b - a,
              'relative_change': (b - a) / a if a else None,
              'difference_ci95': np.quantile(bootdiff, [.025, .975]).tolist(),
              'relative_change_ci95': np.nanquantile(relative, [.025, .975]).tolist() if np.any(np.isfinite(relative)) else None,
              'bootstrap_draws': draws, 'bootstrap_scope': 'domain-stratified chain resampling; repeats averaged within task',
              'unique_tasks': n_tasks, 'chain_count': sum(len(r) for r in grouped.values()),
              'chains_per_domain': {k: len(v) for k, v in grouped.items()},
              'task_pair_distribution': task_values}
    result.update(paired_signflip(chain_differences, chain_weights, seed))
    return result


def _binomial_upper(k, n, alpha):
    return 1.0 if k == n else float(beta.ppf(1 - alpha, k + 1, n - k))


def quality_safety_bound(task_pairs, alpha=.05):
    """Conservative paired exact safety bound valid under arbitrary within-chain dependence.

    A chain's mean quality loss cannot exceed its indicator of any paired loss.
    Clopper-Pearson upper limits for that indicator are formed independently per
    domain with Bonferroni simultaneous coverage, then weighted by planned tasks.
    For unequal chain lengths, multiply each domain bound by n_chains * max_size /
    domain_tasks (capped at one): the weighted loss indicator sum is no greater
    than this factor times the unweighted chain loss rate. For balanced original
    main/public chains the factor is exactly one, retaining the original bound.
    Gains are ignored for the lower bound. This remains nondegenerate at all-pass
    and never counts repeats as new cases.
    """
    chains = defaultdict(list)
    for pair in task_pairs:
        chains[(pair['domain'], pair['chain_id'])].append(pair)
    domains = defaultdict(list)
    for (domain, chain), pairs in sorted(chains.items()):
        ids = {p['task_id'] for p in pairs}
        domains[domain].append({'chain_id': chain, 'tasks': len(ids),
                                'any_loss': any(p['a']['success'] and not p['b']['success'] for p in pairs),
                                'any_gain': any(not p['a']['success'] and p['b']['success'] for p in pairs)})
    total_tasks = sum(r['tasks'] for rows in domains.values() for r in rows)
    lower, upper, one_sided_lower = 0.0, 0.0, 0.0
    details = []
    for domain, rows in sorted(domains.items()):
        n, loss, gain = len(rows), sum(r['any_loss'] for r in rows), sum(r['any_gain'] for r in rows)
        domain_tasks = sum(r['tasks'] for r in rows)
        weight = domain_tasks / total_tasks
        unequal_length_factor = n * max(r['tasks'] for r in rows) / domain_tasks
        loss_one = min(1.0, unequal_length_factor * _binomial_upper(loss, n, alpha / len(domains)))
        loss_two = min(1.0, unequal_length_factor * _binomial_upper(loss, n, alpha / (2 * len(domains))))
        gain_two = min(1.0, unequal_length_factor * _binomial_upper(gain, n, alpha / (2 * len(domains))))
        one_sided_lower -= weight * loss_one
        lower -= weight * loss_two
        upper += weight * gain_two
        details.append({'domain': domain, 'chains': n, 'chains_any_paired_loss': loss,
                        'chains_any_paired_gain': gain, 'task_weight': weight,
                        'loss_probability_upper_one_sided': loss_one,
                        'chain_task_counts': [r['tasks'] for r in rows],
                        'unequal_chain_length_conservative_factor': unequal_length_factor})
    return {'quality_difference_one_sided_lower95': one_sided_lower,
            'quality_conservative_ci95': [lower, upper], 'risk_margin': -.05,
            'can_exclude_loss_over_5pp': one_sided_lower > -.05,
            'method': 'paired chain-any-loss/gain Clopper-Pearson bounds, simultaneous domain Bonferroni; unequal-length max-weight inflation when needed; arbitrary within-chain dependence',
            'scope': 'upper/lower probability bounds are conservative, gain magnitude is not credited in safety bound',
            'domains': details}


def analyze(task_runs, planned_units, seed=20261019):
    """All planned-unit analysis; no success filtering in confirmatory endpoints."""
    planned, actual = _unique(planned_units), _unique(task_runs)
    extra = set(actual) - set(planned)
    if extra:
        raise ValueError(f'{len(extra)} unplanned result keys; select one frozen phase before analysis')
    strata = defaultdict(list)
    for row in planned.values():
        for required in ('chain_id', 'domain'):
            if required not in row:
                raise ValueError(f'missing planned {required}')
        strata[(row['model'], row['topology'])].append(row)
    report = {'schema_version': 1, 'seed': seed, 'planned_units': len(planned),
              'observed_units': len(actual), 'missing_units': len(planned) - len(actual),
              'missing_quality_policy': 'count as failure using all planned denominator',
              'missing_cost_policy': 'unknown; never imputed zero; confirmation INCOMPLETE',
              'strata': [], 'quality_noninferiority_wording': 'WITHHELD_BY_PROSPECTIVE_GATE'}
    for (model, topology), units in sorted(strata.items()):
        index = {_key(p): p for p in units}
        results = []
        for pair_index, (arm_a, arm_b) in enumerate(PAIRS):
            paired = []
            for key, plan in sorted(index.items()):
                if plan['arm'] != arm_a:
                    continue
                counterpart = dict(plan, arm=arm_b)
                if _key(counterpart) not in index:
                    continue
                aa = actual.get(key)
                bb = actual.get(_key(counterpart))
                a = dict(aa) if aa else {}
                b = dict(bb) if bb else {}
                a['success'] = bool(a.get('success', False))
                b['success'] = bool(b.get('success', False))
                paired.append({**{k: plan[k] for k in ('task_id', 'chain_id', 'domain')},
                               'repeat': plan['repeat'], 'a': a, 'b': b,
                               'a_observed': aa is not None, 'b_observed': bb is not None})
            result = {'baseline': arm_a, 'target': arm_b, 'planned_paired_units': len(paired), 'metrics': {}}
            if not paired:
                result['status'] = 'ARMS_NOT_PLANNED'
                results.append(result)
                continue
            for p in paired:
                p['a']['quality_success'] = int(p['a']['success'])
                p['b']['quality_success'] = int(p['b']['success'])
            result['quality'] = cluster_bootstrap(paired, 'quality_success', seed + pair_index)
            result['quality'].update(quality_safety_bound(paired))
            result['quality']['all_planned_unit_denominator_per_arm'] = len(paired)
            result['quality']['observed_paired_units'] = sum(p['a_observed'] and p['b_observed'] for p in paired)
            for metric_index, metric in enumerate(PRIMARY + SECONDARY):
                valid = [p for p in paired if all(isinstance(p[arm].get(metric), (int, float))
                         and math.isfinite(p[arm][metric]) and p[arm][metric] >= 0 for arm in ('a', 'b'))]
                if len(valid) != len(paired):
                    result['metrics'][metric] = {'status': 'INCOMPLETE', 'planned_paired_units': len(paired),
                                                 'measured_paired_units': len(valid), 'p_raw': None,
                                                 'reason': 'unknown planned-unit costs retained; no zero substitution'}
                    continue
                stats = cluster_bootstrap(valid, metric, seed + 100 + pair_index * 10 + metric_index)
                stats['all_planned_unit_denominator_per_arm'] = len(paired)
                stats['both_success_paired_units'] = sum(p['a']['success'] and p['b']['success'] for p in paired)
                stats['efficiency_interpretation_requires_quality_constraint'] = True
                stats['early_failure_warning'] = stats['both_success_paired_units'] < len(paired)
                both = [p for p in valid if p['a']['success'] and p['b']['success']]
                _, success_values = _chain_arrays(both, metric)
                if success_values:
                    ba = float(np.mean([v['a'] for v in success_values]))
                    bb = float(np.mean([v['b'] for v in success_values]))
                    stats['both_success_auxiliary'] = {
                        'status': 'EXPLORATORY_SELECTION_ON_POST_EXECUTION_SUCCESS',
                        'paired_units': len(both), 'unique_tasks': len(success_values),
                        'baseline_mean': ba, 'target_mean': bb, 'difference': bb - ba,
                        'relative_change': (bb - ba) / ba if ba else None,
                        'selection_bias': 'conditions on both arms succeeding; excludes potentially expensive/harmful failures; never replaces all-planned primary result'}
                else:
                    stats['both_success_auxiliary'] = {'status': 'NO_BOTH_SUCCESS_PAIRS', 'paired_units': 0}
                result['metrics'][metric] = stats
            result['status'] = 'COMPLETE' if all(result['metrics'][m]['status'] == 'COMPLETE' for m in PRIMARY) else 'INCOMPLETE'
            results.append(result)
        family = [(r, m) for r in results for m in PRIMARY]
        pvalues = [r.get('metrics', {}).get(m, {}).get('p_raw') for r, m in family]
        adjusted = holm(pvalues)
        for (result, metric), adj in zip(family, adjusted):
            if metric in result.get('metrics', {}):
                result['metrics'][metric]['p_holm'] = adj
                result['metrics'][metric]['holm_family_size'] = 10
                result['metrics'][metric]['holm_family'] = 'five prespecified pairs x two primary cost endpoints'
        report['strata'].append({'model': model, 'topology': topology, 'comparisons': results})
    return report


def _read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = analyze(_read(args.runs), _read(args.plan))
    report['input_sha256'] = {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (args.runs, args.plan)}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
