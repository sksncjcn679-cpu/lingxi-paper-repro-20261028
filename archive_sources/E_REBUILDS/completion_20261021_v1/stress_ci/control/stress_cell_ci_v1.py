"""Authorized postcollection, whole-batch stress intervals; no runtime/provider import."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
from pathlib import Path

import numpy as np


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def observed_number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Missing, negative or nonfinite observed cost; no zero substitution')
    if integer and not isinstance(value, int):
        raise ValueError('An integer measured counter is required')
    return value


def pooled_quantiles(values, batch_labels, multiplicities):
    """Exact linear pooled p95, each source value repeated by its batch multiplicity."""
    order = np.argsort(values, kind='stable')
    sorted_values, sorted_labels = values[order], batch_labels[order]
    h = (len(values) - 1) * .95
    lower, upper, fraction = math.floor(h), math.ceil(h), h - math.floor(h)
    answer = np.empty(len(multiplicities), dtype=float)
    for start in range(0, len(multiplicities), 64):
        selected = multiplicities[start:start + 64, sorted_labels]
        cumulative = np.cumsum(selected, axis=1, dtype=np.int32)
        assert np.all(cumulative[:, -1] == len(values))
        lo = np.argmax(cumulative > lower, axis=1)
        hi = np.argmax(cumulative > upper, axis=1)
        brackets = np.column_stack((sorted_values[lo], sorted_values[hi]))
        answer[start:start + len(selected)] = np.quantile(brackets, fraction, axis=1, method='linear')
    return answer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--method', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    spec, method = read(args.inputs), read(args.method)
    assert sha(args.method) == spec['method_sha256']
    assert method['draws'] == 10000 and method['seed'] == 20261021
    for relative, item in spec['files'].items():
        path = args.inputs.parent / relative
        if path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise ValueError('Input hash/size mismatch: ' + relative)
    with (args.inputs.parent / 'original_points.csv').open(encoding='utf-8-sig', newline='') as stream:
        original = list(csv.DictReader(stream))
    old = {(int(r['length']), int(r['concurrency'])): r for r in original}
    grid = [(l, k) for l in (1, 2, 4, 8, 16, 32) for k in (1, 2, 4, 8, 16)]
    assert len(original) == len(old) == 30 and set(old) == set(grid)
    cells, comparisons = [], []
    for length, concurrency in grid:
        stem = f'stress_v3_L{length}_K{concurrency}'
        times, labels, identifiers = [], [], set()
        batch_wire = np.zeros(30, dtype=np.int64)
        batch_proxy = np.zeros(30, dtype=np.int64)
        batch_sizes = np.zeros(30, dtype=np.int64)
        successes = consumed = 0
        expected_ids = {batch: {f'{stem}-b{batch:02}-c{i:02}-s{step:02}' for i in range(concurrency) for step in range(length)} for batch in range(30)}
        path = args.inputs.parent / 'raw' / (stem + '.jsonl')
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                r = json.loads(line)
                assert r['length'] == length and r['concurrency'] == concurrency
                batch = r['batch']
                assert type(batch) is int and 0 <= batch < 30
                identifier = r['task_id']
                assert identifier not in identifiers
                identifiers.add(identifier)
                assert identifier in expected_ids[batch]
                metrics = r['metrics']
                assert isinstance(metrics, dict) and type(r['success']) is bool and type(r['state_consumed']) is bool
                times.append(observed_number(r['wall_ns'], integer=True) / 1e9)
                labels.append(batch)
                batch_sizes[batch] += 1
                batch_proxy[batch] += observed_number(metrics['text_tokens'], integer=True)
                batch_wire[batch] += observed_number(metrics['ipc_framed_bytes'], integer=True) + observed_number(metrics['ipc_ack_bytes'], integer=True)
                successes += int(r['success'])
                consumed += int(r['state_consumed'])
        assert len(times) == length * concurrency * 30
        assert np.all(batch_sizes == length * concurrency)
        batch_path = args.inputs.parent / 'raw' / (stem + '_batches.jsonl')
        batches = [json.loads(line) for line in batch_path.open(encoding='utf-8')]
        assert len(batches) == 30 and {r['batch'] for r in batches} == set(range(30))
        for r in batches:
            assert r['length'] == length and r['concurrency'] == concurrency
            assert r['tasks_planned'] == r['tasks_recorded'] == length * concurrency
        latency = np.asarray(times, dtype=float)
        observed = {
            'planned_batches': 30, 'recorded_batches': 30,
            'planned_tasks': length * concurrency * 30, 'recorded_tasks': len(times),
            'successes': successes, 'failures': len(times) - successes, 'state_consumed': consumed,
            'task_latency_p50_sec': float(np.quantile(latency, .5)),
            'task_latency_p95_sec': float(np.quantile(latency, .95)),
            'task_latency_p99_sec': float(np.quantile(latency, .99)),
            'message_proxy_tokens_total': int(batch_proxy.sum()),
            'native_observable_frame_ack_bytes_total': int(batch_wire.sum()),
            'message_proxy_tokens_per_chain': int(batch_proxy.sum()) / (concurrency * 30),
            'native_observable_frame_ack_bytes_per_chain': int(batch_wire.sum()) / (concurrency * 30),
        }
        for field, value in observed.items():
            reference = int(old[length, concurrency][field]) if isinstance(value, int) else float(old[length, concurrency][field])
            if value != reference:
                raise ValueError(f'Original point changed: {length},{concurrency},{field}')
        comparisons.append({'length': length, 'concurrency': concurrency, 'all_fields_exact_equal': True, 'observed_fields': observed})
        cells.append((length, concurrency, latency, np.asarray(labels), batch_wire, batch_proxy, observed))
    # The complete30-cell point gate passes before any resampling begins.
    args.out.mkdir(exist_ok=False)
    write(args.out / 'point_identity.json', {'status': 'ALL30_CELLS_ORIGINAL_POINTS_AND_DENOMINATORS_EXACT', 'cells': comparisons})
    streams = np.random.SeedSequence(20261021).spawn(30)
    rows, check_counts = [], []
    for index, (length, concurrency, latency, labels, wire, proxy, points) in enumerate(cells):
        rng = np.random.default_rng(streams[index])
        draws = rng.integers(0, 30, size=(10000, 30))
        counts = np.zeros((10000, 30), dtype=np.int16)
        for j in range(30):
            counts[:, j] = np.count_nonzero(draws == j, axis=1)
        p95 = pooled_quantiles(latency, labels, counts)
        for draw_index in (0, 1, 2):
            literal = np.concatenate([latency[labels == batch] for batch in draws[draw_index]])
            assert float(np.quantile(literal, .95, method='linear')) == p95[draw_index]
        wire_draws = counts @ wire / (30 * concurrency)
        proxy_draws = counts @ proxy / (30 * concurrency)
        row = {'length': length, 'concurrency': concurrency, **points,
               'p95_ci95_low_sec': float(np.quantile(p95, .025)), 'p95_ci95_high_sec': float(np.quantile(p95, .975)),
               'frame_ack_bytes_per_chain_ci95_low': float(np.quantile(wire_draws, .025)),
               'frame_ack_bytes_per_chain_ci95_high': float(np.quantile(wire_draws, .975)),
               'message_proxy_tokens_per_chain_ci95_low': float(np.quantile(proxy_draws, .025)),
               'message_proxy_tokens_per_chain_ci95_high': float(np.quantile(proxy_draws, .975)),
               'bootstrap_draws': 10000, 'master_seed': 20261021, 'cell_stream_index': index,
               'sampling_unit': 'complete_original_runtime_batch', 'status': 'POSTCOLLECTION_POINTWISE_CI',
               'provider_tokens': None}
        assert row['p95_ci95_low_sec'] <= row['p95_ci95_high_sec']
        assert row['frame_ack_bytes_per_chain_ci95_low'] <= row['frame_ack_bytes_per_chain_ci95_high']
        rows.append(row)
        check_counts.append({'length': length, 'concurrency': concurrency, 'literal_replication_draws_checked': [0, 1, 2], 'exact_equal': True})
        print(json.dumps({'completed_cell': index + 1, 'cells': 30}), flush=True)
    write(args.out / 'method_used.json', method)
    write(args.out / 'source_manifest_used.json', spec)
    result = {'status': 'COMPLETE_POSTCOLLECTION_CI_ORIGINAL_POINTS_UNCHANGED', 'rows': rows,
              'cells': 30, 'original_task_rows': sum(r['recorded_tasks'] for r in rows), 'all_planned_failures_retained': True,
              'point_identity_gate_passed_before_bootstrap': True, 'literal_replication_checks': check_counts,
              'master_seed': 20261021, 'draws_per_cell': 10000, 'new_model_or_benchmark_calls': 0,
              'postcollection_not_initial_frozen_method': True, 'slope_coefficient_CIs_added': False,
              'original_throughput_estimates_or_CIs_modified': False, 'no_cross_cell_simultaneous_band': True,
              'python': platform.python_version(), 'numpy': np.__version__, 'script_sha256': sha(__file__),
              'method_sha256': sha(args.method), 'input_manifest_sha256': sha(args.inputs)}
    write(args.out / 'results.json', result)
    with (args.out / 'stress_cell_ci.csv').open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    outputs = ['stress_cell_ci.csv', 'results.json', 'point_identity.json', 'method_used.json', 'source_manifest_used.json']
    write(args.out / 'analysis_receipt.json', {'status': result['status'], 'cells': 30,
        'script_sha256': sha(__file__), 'method_sha256': sha(args.method), 'input_manifest_sha256': sha(args.inputs),
        'output_sha256': {name: sha(args.out / name) for name in outputs}, 'new_model_API_benchmark_calls': 0})


if __name__ == '__main__':
    main()
