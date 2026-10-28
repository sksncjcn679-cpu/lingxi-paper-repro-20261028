"""Verify the completed isolated rebuild without rewriting its byte-guard failure."""
from __future__ import annotations
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
from PIL import Image
import formal_reproduce as rebuild

OUT = Path('/evidence/formal_20261019')
ROOT = Path('/evidence/rebuild_formal_20261019_v1')
REPRODUCER_SHA = 'afc4eedd758ae75bf89ef45aa52a962233a7d1bad02144b50e9a657375b29ae6'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def main():
    destination = ROOT / 'postcheck_v1'
    if destination.exists():
        raise RuntimeError('new postcheck destination required')
    destination.mkdir()
    record = dict(status='RUNNING', started_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256=sha(Path(__file__)), paid_api_calls=0, raw_modified=False,
        original_failure_relabelled=False, goal_completion='NOT_CERTIFIED')
    def persist():
        (destination / 'audit.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    persist()
    try:
        if sha(Path(rebuild.__file__)) != REPRODUCER_SHA:
            raise RuntimeError('executed reproducer source changed')
        manifest_path = ROOT / 'rebuild_manifest.json'
        manifest_sha = sha(manifest_path)
        manifest = load(manifest_path)
        if manifest['status'] != 'FAILED_RETAINED_FOR_DIAGNOSIS' or len(manifest['steps']) != 9 or any(
                s['status'] != 'EXIT_OK' or s['exit_code'] != 0 for s in manifest['steps']):
            raise RuntimeError('actual nine-step rebuild prerequisites differ')
        previous = manifest['report_numerical_identity']
        if previous['status'] != 'FAIL_NUMERICAL_IDENTITY' or not previous['same_relative_file_set'] or \
           previous['abstract_canonical_sha256'] != previous['expected_abstract_canonical_sha256']:
            raise RuntimeError('actual byte-guard diagnosis differs')
        original = OUT / 'analysis/postrun_verification_v1/report_v3'
        report = ROOT / 'report'
        old_numbers = rebuild.report_numeric_inventory(original)
        new_numbers = rebuild.report_numeric_inventory(report)
        differing = sorted(k for k in old_numbers if old_numbers[k] != new_numbers.get(k))
        if set(old_numbers) != set(new_numbers) or len(old_numbers) != 42:
            raise RuntimeError('numerical relative file set changed')
        # Abstract ordering was independently proved before this rebuild; no new
        # numeric/array/type normalization is permitted here.
        allowed = {'analysis/abstract_metrics.json', 'tables/table_01.csv', 'tables/table_06.csv'}
        if not set(differing).issubset(allowed) or any(old_numbers[k] != new_numbers[k] for k in old_numbers if k not in allowed):
            raise RuntimeError('unexpected numerical file difference')
        if rebuild.canonical_json_sha(original / rebuild.ABSTRACT_PATH) != rebuild.canonical_json_sha(report / rebuild.ABSTRACT_PATH):
            raise RuntimeError('abstract typed canonical values differ')
        changes = []
        view = ROOT / 'input_view'
        for name, column in (('table_01.csv', 'source'), ('table_06.csv', 'equivalence_audit')):
            header_a, rows_a = csv_rows(original / 'tables' / name)
            header_b, rows_b = csv_rows(report / 'tables' / name)
            if header_a != header_b or len(rows_a) != len(rows_b):
                raise RuntimeError('table columns or row counts differ')
            for ordinal, (a, b) in enumerate(zip(rows_a, rows_b), 1):
                if set(a) != set(b):
                    raise RuntimeError('table row field set differs')
                for key in a:
                    if a[key] == b[key]:
                        continue
                    if key != column:
                        raise RuntimeError('table data changed outside the exact provenance column')
                    old_path, new_path = Path(a[key]), Path(b[key])
                    relative = old_path.relative_to(OUT)
                    if new_path != view / relative or not old_path.is_file() or not new_path.is_file() or sha(old_path) != sha(new_path):
                        raise RuntimeError('relocated provenance does not point to an identical independent input copy')
                    changes.append(dict(table=name, row=ordinal, column=key,
                        original=a[key], rebuilt=b[key], identical_input_sha256=sha(old_path)))
        if len(changes) != 47:
            raise RuntimeError('unexpected scope of evidence-path relocation')
        inventory = load(ROOT / 'logs/input_copy_inventory.json')
        record['original_input_integrity'] = rebuild.unchanged_inputs(OUT, inventory)
        record['original_publication_integrity'] = rebuild.selected_render_gate(OUT)
        protected = ('raw', 'manifests', 'corpus',
            'retrieval_input_correction_v2/raw', 'retrieval_input_correction_v2/corpus',
            'retrieval_input_correction_v2/drivers', 'state_measurement_correction_v2/raw',
            'state_measurement_correction_v2/logs', 'state_measurement_correction_v3/raw')
        mode_records = []
        copied_files = 0
        for relative_root in protected:
            root = view / relative_root
            for path in [root, *sorted(root.rglob('*'))]:
                if path.is_symlink():
                    raise RuntimeError('input copy contains a symlink')
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode & 0o222:
                    raise RuntimeError('protected input copy retains write mode bits')
                relative = path.relative_to(view).as_posix()
                if path.is_file():
                    if relative not in inventory or sha(path) != inventory[relative]['sha256'] or path.samefile(OUT / relative):
                        raise RuntimeError('protected input is altered or is a hard link')
                    copied_files += 1
                mode_records.append(dict(relative=relative, mode=oct(mode)))
        pixels = []
        for old in sorted((original / 'figures').glob('figure_*.png')):
            new = report / 'figures' / old.name
            with Image.open(old) as a, Image.open(new) as b:
                a = a.convert('RGBA'); b = b.convert('RGBA')
                ah = hashlib.sha256(a.tobytes()).hexdigest(); bh = hashlib.sha256(b.tobytes()).hexdigest()
                if a.size != b.size or ah != bh:
                    raise RuntimeError('rebuilt plot pixels differ from the actually viewed publication report')
                pixels.append(dict(name=old.name, size=list(a.size), rgba_sha256=ah,
                    original_file_sha256=sha(old), rebuilt_file_sha256=sha(new)))
        if len(pixels) != 18:
            raise RuntimeError('expected eighteen complete plot pixel comparisons')
        exports = load(ROOT / 'exports/export_audit.json')
        if exports['status'] != 'PASS' or any(x['status'] != 'PASS' for x in exports['exports']):
            raise RuntimeError('rebuilt six-table lossless export did not pass')
        state = ROOT / 'input_view/state_measurement_correction_v3/analysis/recomputed_v2'
        if load(state / 'matrix_audit.json')['status'] != 'PASS' or load(state / 'stage_join_audit.json')['status'] != 'PASS':
            raise RuntimeError('rebuilt state supplement did not pass')
        if sha(manifest_path) != manifest_sha or rebuild.report_numeric_inventory(original) != old_numbers or rebuild.report_numeric_inventory(report) != new_numbers:
            raise RuntimeError('original failure or report changed during postcheck')
        record.update(status='PASS_REBUILD_VALUES_WITH_RETAINED_PATH_BYTE_GUARD_FAILURE',
            retained_rebuild_manifest_sha256=manifest_sha, retained_rebuild_status=manifest['status'],
            actual_step_count=9, all_step_exit_codes_zero=True,
            numerical_file_count=42, exact_byte_identical_numerical_files=sum(old_numbers[k] == new_numbers[k] for k in old_numbers),
            abstract_typed_canonical_sha256=rebuild.canonical_json_sha(report / rebuild.ABSTRACT_PATH),
            provenance_only_relocations=changes, original_numeric_sha256=old_numbers, rebuilt_numeric_sha256=new_numbers,
            protected_input_files=copied_files, protected_modes=mode_records,
            read_only_boundary='Verified mode bits and independent file contents only; root/CAP_DAC_OVERRIDE or later chmod can still write. No immutable-storage claim.',
            rebuilt_png_pixel_identity=pixels, visual_scope='All18 rebuilt PNG pixel arrays equal the actual report_v3 images already rendered/viewed; no new human review claimed.',
            exports_status=exports['status'], state_supplement_status='PASS',
            report_availability_status=load(report / 'analysis/report_availability.json')['status'],
            scope='All nine offline stages executed; only evidence-path relocation and independently approved JSON object order differ. The original strict byte-guard failure stays unchanged; paper validity and full protocol completion remain separate.')
    except BaseException as error:
        record.update(status='ERROR_RETAINED_NO_CERTIFICATION', error_type=type(error).__name__,
            error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        raise
    finally:
        record['finished_utc'] = datetime.now(timezone.utc).isoformat()
        persist()
    print(json.dumps({k: record.get(k) for k in ('status', 'actual_step_count', 'protected_input_files', 'numerical_file_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
