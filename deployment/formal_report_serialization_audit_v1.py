"""Independently distinguish report JSON serialization from numerical changes.

The prior exact-byte failure remains unchanged. This postrun audit cannot certify
visual quality, study validity, or completion of the research protocol.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

OUT = Path('paper_experiments_20261019_pilot/formal_20261019').resolve()
OVERLAY_SHA = '8bc0747441900db0372f1a1cd81521073fcb4e3de1faf986e6d61046c20818be'
DEST = OUT / 'analysis/report_numeric_serialization_audit_v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json(path):
    def reject(value):
        raise ValueError('non-finite JSON')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON object key')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=reject)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def inventory(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and (p.suffix == '.csv' or p.name == 'abstract_metrics.json'
                or (p.name.startswith('statistics_') and p.suffix == '.json'))}


def compare(left, right, counts):
    counts['visited_nodes'] += 1
    if type(left) is not type(right):
        counts['typed_value_differences'] += 1
    elif isinstance(left, dict):
        if set(left) != set(right):
            counts['typed_value_differences'] += 1
            return
        if list(left) != list(right):
            counts['object_member_order_differences'] += 1
        for key in left:
            compare(left[key], right[key], counts)
    elif isinstance(left, list):
        if len(left) != len(right):
            counts['typed_value_differences'] += 1
            return
        for a, b in zip(left, right):
            compare(a, b, counts)
    elif left != right:
        counts['typed_value_differences'] += 1


def main():
    if DEST.exists():
        raise RuntimeError('new audit directory required')
    overlay_path = OUT / 'analysis/final_render_selection_v3.json'
    if digest(overlay_path) != OVERLAY_SHA:
        raise RuntimeError('reviewed layout selection changed')
    overlay = strict_json(overlay_path)
    original = OUT / overlay['original_data_report_root']
    publication = OUT / overlay['report_root']
    recovery_path = OUT / overlay['report_recovery_root'] / 'manifest.json'
    failure_path = recovery_path.with_name('numerical_identity.json')
    recovery = strict_json(recovery_path)
    failure = strict_json(failure_path)
    step = recovery['steps'][0]
    if recovery['status'] != 'ERROR_RETAINED_FOR_DIAGNOSIS' or len(recovery['steps']) != 1 or \
       step['status'] != 'PASS' or step['exit_code'] != 0 or step['timed_out'] or \
       recovery['final_render_selection_sha256'] != OVERLAY_SHA or \
       failure['status'] != 'FAIL_EXACT_BYTES':
        raise RuntimeError('actual rendered report and retained failure prerequisites differ')
    before = {str(p.relative_to(OUT).as_posix()): digest(p) for p in
              (overlay_path, recovery_path, failure_path,
               OUT / overlay['original_visual_qa'], OUT / overlay['original_report_recovery_manifest'])}
    expected = overlay['numeric_reference_sha256']
    old = inventory(original)
    new = inventory(publication)
    if old != expected or set(old) != set(new) or len(old) != 42:
        raise RuntimeError('numeric file inventory or old bound report changed')
    changed = [name for name in old if old[name] != new[name]]
    if changed != ['analysis/abstract_metrics.json']:
        raise RuntimeError('unexpected byte differences beyond abstract JSON')
    name = changed[0]
    left, right = strict_json(original / name), strict_json(publication / name)
    counts = dict(visited_nodes=0, typed_value_differences=0, object_member_order_differences=0)
    compare(left, right, counts)
    left_canonical, right_canonical = canonical(left), canonical(right)
    if counts['typed_value_differences'] or left_canonical != right_canonical:
        raise RuntimeError('abstract JSON contains an actual value or type change')
    if counts['object_member_order_differences'] == 0:
        raise RuntimeError('expected object-order serialization explanation not established')
    if inventory(original) != old or inventory(publication) != new or any(
            digest(OUT / path) != value for path, value in before.items()):
        raise RuntimeError('report or retained evidence changed during audit')
    result = dict(status='PASS_NUMERICAL_IDENTITY_WITH_RETAINED_BYTE_GUARD_FAILURE',
        created_utc=datetime.now(timezone.utc).isoformat(), source_sha256=digest(Path(__file__)),
        final_render_selection_sha256=OVERLAY_SHA,
        report_recovery_manifest_sha256=digest(recovery_path),
        retained_exact_byte_failure_sha256=digest(failure_path),
        retained_controller_status=recovery['status'], actual_report_child_exit_code=step['exit_code'],
        original_report_root=overlay['original_data_report_root'], publication_report_root=overlay['report_root'],
        numeric_file_count=42, exact_byte_identical_files=41,
        sole_serialization_difference=name, abstract_comparison=counts,
        abstract_canonical_sha256=hashlib.sha256(left_canonical).hexdigest(),
        original_numeric_sha256=old, publication_numeric_sha256=new, evidence_sha256=before,
        diagnosis='Object member ordering differs; all parsed types/values and canonical JSON bytes match. CSV and statistics files match exact bytes.',
        disposition='Accept these actually generated figures for independent visual QA without rerendering; retain the stricter byte-guard failure. This postrun exception changes no data, effects, populations or analysis.',
        prior_selection_bytes_modified=False, paid_api_calls=0, raw_modified=False,
        visual_qa='NOT_PERFORMED_BY_THIS_AUDIT', goal_completion='NOT_CERTIFIED')
    DEST.mkdir()
    with (DEST / 'audit.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: result[k] for k in ('status', 'numeric_file_count', 'exact_byte_identical_files', 'abstract_comparison')}))


if __name__ == '__main__':
    main()
