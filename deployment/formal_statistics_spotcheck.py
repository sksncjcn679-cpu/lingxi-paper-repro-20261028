"""Thirty preselected formal units; automatic review, explicitly no human raters."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import argparse
import hashlib
import json
from formal_measurement_audit import audit, TABLES

OUT = Path('/evidence/formal_20261019')
PLAN = OUT / 'analysis/stats/automatic_spotcheck_plan.json'


def rank(unit):
    return hashlib.sha256(('automatic-spotcheck-20261019/' + unit['run_id']).encode()).hexdigest()


def prepare():
    path = OUT / 'manifests/formal_plan.json'
    units = [u for u in json.loads(path.read_text()) if u['phase'] == 'main']
    domains = sorted({u['domain'] for u in units})
    arms = ['T0', 'S0', 'SS0', 'T1', 'S1', 'SS1']
    chosen = []
    for domain in domains:
        for arm in arms:
            chosen.append(min((u for u in units if u['domain'] == domain and u['arm'] == arm), key=rank))
    # Six additional units keep five samples per arm and near-balanced domains.
    extra_domains = [domains[0], domains[0], domains[1], domains[1], domains[2], domains[3]]
    for arm, domain in zip(arms, extra_domains):
        ids = {u['run_id'] for u in chosen}
        chosen.append(min((u for u in units if u['arm'] == arm and u['domain'] == domain and u['run_id'] not in ids), key=rank))
    result = {'created_utc': datetime.now(timezone.utc).isoformat(), 'sampled_units': len(chosen),
        'selection': 'frozen-plan hash ranking; one per domain x arm then six preassigned extras; no outcome-based selection',
        'seed': 20261019, 'plan_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'human_raters': 0, 'review_method': 'AUTOMATIC_RECORD_CONSISTENCY_REVIEW',
        'user_authorization': '采用自动验证，取消人工评分并记录协议变更',
        'inter_rater_agreement': 'NOT_APPLICABLE_NO_HUMAN_RATINGS',
        'coverage_by_arm': dict(Counter(u['arm'] for u in chosen)),
        'coverage_by_domain': dict(Counter(u['domain'] for u in chosen)), 'units': chosen}
    assert len(chosen) == 30 and len({u['run_id'] for u in chosen}) == 30
    with PLAN.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return {'status': 'PRESELECTED_BEFORE_FORMAL_OUTCOMES', 'sampled_units': 30, 'plan': str(PLAN)}


def run():
    plan = json.loads(PLAN.read_text())
    ids = {u['run_id'] for u in plan['units']}
    path = OUT / 'raw/task_runs.jsonl'
    complete = {json.loads(line)['run_id'] for line in path.read_text().splitlines()}
    if not ids <= complete:
        return {'status': 'PENDING_SELECTED_UNITS', 'selected_completed': len(ids & complete), 'planned_spotcheck': 30}
    target = OUT / 'analysis/stats/automatic_spotcheck_v1'
    if target.exists():
        raise RuntimeError('spotcheck archive exists; never overwrite')
    (target / 'raw').mkdir(parents=True)
    for table in TABLES:
        original = OUT / 'raw' / (table + '.jsonl')
        if not original.exists():
            continue
        with original.open() as source, (target / 'raw' / (table + '.jsonl')).open('x') as subset:
            for line in source:
                if json.loads(line).get('run_id') in ids:
                    subset.write(line)
    result = audit(target)
    result.update({'human_raters': 0, 'review_method': plan['review_method'],
        'user_authorization': plan['user_authorization'], 'inter_rater_agreement': plan['inter_rater_agreement'],
        'selection_plan_sha256': hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        'scope': '30 preselected formal task execution units; automated raw-record, provider usage, fact, IPC, span, consumer and cleanup audit; not human semantic scoring'})
    with (OUT / 'analysis/stats/automatic_spotcheck_report.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return {'status': result['status'], 'sampled_units': result['terminal_units'],
            'measurement_errors': len(result['measurement_errors']), 'human_raters': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'run'])
    args = parser.parse_args()
    result = prepare() if args.command == 'prepare' else run()
    print(json.dumps(result))
    raise SystemExit(result['status'] == 'FAIL')
