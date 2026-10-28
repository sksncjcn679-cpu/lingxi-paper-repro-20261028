"""Immutable pre-formal power addendum using newly calibrated native prompts."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json

import formal_statistics_prepare as preparation
from formal_measurement_audit import calibration_gate


def update(out, process_dry_out, version='native_public_v1'):
    out = Path(out)
    raw = out / 'raw/task_runs.jsonl'
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    if any(r.get('phase') != 'public_calibration' for r in rows):
        raise RuntimeError('prospective update requires pre-formal public-only raw table; refusing any formal-outcome access')
    gate = calibration_gate(out, process_dry_out)
    if not gate['freeze_allowed']:
        raise RuntimeError('native public measurement gate has not passed: ' + ','.join(gate['reasons']))
    stats = out / 'analysis/stats'
    stats.mkdir(exist_ok=True)
    snapshot = stats / f'{version}_task_runs_snapshot.jsonl'
    with snapshot.open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    preparation.PILOT = snapshot
    power = preparation.prospective()
    power.update({
        'scope': 'prospective addendum after native-representation public calibration, before any formal units',
        'public_provider_task_units': len(rows),
        'public_provider_attempts': sum(r['provider_attempts'] for r in rows),
        'pilot_scope': 'new real qwen3.7-plus public calibration with native lean/structured/reference prompts and identical complete business observations; no formal outcomes used',
        'pilot_limitation': 'one public chain per domain; ICC and precise discordance remain uncertain; binomial task upper bound is descriptive sensitivity range only because tasks in chains are dependent',
        'public_state_override_scope': 'public SS arms exercise state path for recorder calibration even where public case state-applicable flag is false; no formal applicability changed',
        'measurement_gate_status': gate['status'],
        'measurement_gate_auditor_sha256': hashlib.sha256(Path('/drivers/formal_measurement_audit.py').read_bytes()).hexdigest(),
        'update_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'historical_sensitivity_prior_retained': 'analysis/stats/prospective_power.json',
        'no_formal_outcomes_used': True,
        'decision_unchanged_before_main': True,
    })
    target = stats / f'prospective_power_{version}.json'
    with target.open('x') as stream:
        json.dump(power, stream, ensure_ascii=False, indent=2)
    with (stats / f'prospective_power_{version}.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(power['sensitivity'][0]))
        writer.writeheader()
        writer.writerows(power['sensitivity'])
    addendum = {'created_utc': datetime.now(timezone.utc).isoformat(),
        'authority': 'original protocol section 6.3; update uses public pilot only before formal matrix',
        'decision': power['decision'], 'noninferiority_wording': 'WITHHELD_PROSPECTIVELY',
        'formal_tasks': 120, 'formal_chains': 20, 'formal_outcomes_used': False,
        'public_snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'power_report_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'original_statistical_plan_unchanged': 'statistical_plan.json',
        'note': 'new public evidence updates sensitivity only; no choice of a more favorable test, endpoint, or sample size'}
    with (stats / f'statistical_plan_addendum_{version}.json').open('x') as stream:
        json.dump(addendum, stream, ensure_ascii=False, indent=2)
    return {'status': 'FROZEN_NATIVE_PUBLIC_PROSPECTIVE_ADDENDUM', 'public_tasks': power['public_unique_tasks'],
            'public_units': len(rows), 'decision': power['decision'], 'report': str(target)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='/evidence/formal_20261019')
    parser.add_argument('--process-dry-out', default='/evidence/formal_20261019/calibration/recorder_dry_v4')
    parser.add_argument('--version', default='native_public_v1')
    args = parser.parse_args()
    print(json.dumps(update(args.out, args.process_dry_out, args.version)))
