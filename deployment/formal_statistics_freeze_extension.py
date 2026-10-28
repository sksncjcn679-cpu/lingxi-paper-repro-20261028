"""Pre-formal metadata for unequal-length state-applicable chain subsets."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.util
import json

from formal_statistics import quality_safety_bound

OUT = Path('/evidence/formal_20261019')
STATS = OUT / 'analysis/stats'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    actual = [json.loads(line) for line in (OUT / 'raw/task_runs.jsonl').read_text().splitlines()]
    if any(row['phase'] != 'public_calibration' for row in actual):
        raise RuntimeError('extension must freeze before any formal outcomes')
    old_source = STATS / 'sources_before_unequal_chain_extension/formal_statistics.py'
    spec = importlib.util.spec_from_file_location('statistics_before_unequal', old_source)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    comparisons = []
    for scenario in ('all_pass', 'mixed_loss', 'mixed_gain'):
        pairs = []
        for domain in range(4):
            for chain in range(5):
                for task in range(6):
                    for repeat in range(3):
                        a = not (scenario == 'mixed_gain' and chain == 1 and task == 2)
                        b = not (scenario == 'mixed_loss' and chain == 2 and task == 4)
                        pairs.append({'domain': str(domain), 'chain_id': f'{domain}-{chain}',
                            'task_id': f'{domain}-{chain}-{task}', 'repeat': repeat,
                            'a': {'success': a}, 'b': {'success': b}})
        before, after = old.quality_safety_bound(pairs), quality_safety_bound(pairs)
        fields = ('quality_difference_one_sided_lower95', 'quality_conservative_ci95', 'can_exclude_loss_over_5pp')
        identical = all(before[field] == after[field] for field in fields)
        if not identical:
            raise AssertionError('balanced 120-task main bound changed')
        comparisons.append({'synthetic_scenario': scenario, 'balanced_main_safety_values_identical': identical})
    corpus = [json.loads(line) for line in (OUT / 'corpus/automatic_validated_v1/tasks.jsonl').read_text().splitlines()]
    chain_counts = Counter((r['domain'], r['chain_id']) for r in corpus if r['split'] == 'formal' and r['state_spec']['applicable'])
    by_domain = defaultdict(list)
    for (domain, chain), count in sorted(chain_counts.items()):
        by_domain[domain].append({'chain_id': chain, 'tasks': count})
    factors = {domain: len(chains) * max(r['tasks'] for r in chains) / sum(r['tasks'] for r in chains)
               for domain, chains in by_domain.items()}
    previous = json.loads((STATS / 'statistical_plan.json').read_text())
    updated = {**previous, 'schema_version': 2, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'supersedes_for_formal_freeze': 'statistical_plan.json (retained unchanged)',
        'quality_method': 'same paired chain-any-loss/gain exact domain bounds; unequal chain lengths multiply each domain bound by n_chains * max_chain_tasks / domain_tasks, capped at one; balanced main bound is numerically identical',
        'state_applicable_subset_cases': sum(chain_counts.values()),
        'state_applicable_subset_chain_counts': dict(by_domain),
        'state_applicable_domain_weight_inflation': factors,
        'main_safety_bound_unchanged': True, 'synthetic_balanced_regressions': comparisons,
        'no_formal_outcomes_used': True, 'formal_terminal_units_at_extension': 0,
        'public_power_addendum': 'statistical_plan_addendum_native_public_v1.json',
        'noninferiority_claim': 'WITHHELD_PROSPECTIVELY',
        'old_source_archive': str(old_source.relative_to(OUT)),
        'old_source_sha256': digest(old_source),
        'source_files': {name: digest(Path('/drivers') / name) for name in
            ('formal_statistics.py', 'formal_statistics_prepare.py', 'formal_statistics_test.py',
             'formal_statistics_update_public.py', 'formal_statistics_freeze_extension.py')}}
    with (STATS / 'statistical_plan_v2.json').open('x') as stream:
        json.dump(updated, stream, ensure_ascii=False, indent=2)
    with (STATS / 'statistical_plan_addendum_unequal_chain_v2.json').open('x') as stream:
        json.dump({'created_utc': updated['created_utc'], 'formal_outcomes_used': False,
            'scope': 'protocol-prescribed 108 state-applicable subset; no main endpoint, comparison, sample size or quality gate change',
            'balanced_main_bound_unchanged': comparisons, 'state_cases': sum(chain_counts.values()),
            'new_statistical_plan_sha256': digest(STATS / 'statistical_plan_v2.json'),
            'old_statistical_plan_sha256': digest(STATS / 'statistical_plan.json'),
            'noninferiority_wording': 'WITHHELD_PROSPECTIVELY'}, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': 'PRE_FORMAL_EXTENSION_FROZEN', 'state_subset_cases': sum(chain_counts.values()),
                      'balanced_main_unchanged': True, 'formal_outcomes_used': False}))


if __name__ == '__main__':
    main()
