"""Postrun descriptive scatter supplement required by protocol6.6; no reruns."""
from collections import defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path('paper_experiments_20261019_pilot/formal_20261019')
DEST = ROOT / 'analysis/scalability_scatter_supplement_v4'
SOURCES = {}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    SOURCES[path] = sha(path)
    with path.open(encoding='utf-8') as stream:
        for line, value in enumerate(stream, 1):
            if value.strip():
                yield line, json.loads(value)


def table(path):
    SOURCES[path] = sha(path)
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def write(name, values):
    with (DEST / name).open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, list(values[0]))
        writer.writeheader(); writer.writerows(values)


def save(fig, name):
    fig.tight_layout()
    for ext in ('pdf', 'svg', 'png'):
        fig.savefig(DEST / f'{name}.{ext}', dpi=600, bbox_inches='tight')
    plt.close(fig)


def main():
    if DEST.exists():
        raise RuntimeError('new supplement directory required')
    DEST.mkdir()
    shutil.copyfile(__file__, DEST / 'helper.py')
    plt.rcParams.update({'font.size': 9, 'font.family': 'DejaVu Sans', 'mathtext.fontset': 'dejavusans', 'pdf.fonttype': 42, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False})
    rng = np.random.default_rng(20261019)
    stress = []
    for path in sorted((ROOT / 'raw').glob('stress_v3_L*_K*_batches.jsonl')):
        for line, row in rows(path):
            stress.append({k: row[k] for k in ('length', 'concurrency', 'batch', 'throughput_tasks_per_sec', 'tasks_planned', 'tasks_recorded')}
                          | {'source': path.relative_to(ROOT).as_posix(), 'line': line})
    assert len(stress) == 900
    summary = table(ROOT / 'tables/stress_chain_concurrency.csv')
    assert len(summary) == 30
    fig, axes = plt.subplots(2, 3, figsize=(10, 6))
    for ax, length in zip(axes.flat, (1, 2, 4, 8, 16, 32)):
        for concurrency in (1, 2, 4, 8, 16):
            batch = [r for r in stress if r['length'] == length and r['concurrency'] == concurrency]
            assert len(batch) == 30 and all(r['tasks_planned'] == r['tasks_recorded'] for r in batch)
            values = np.array([r['throughput_tasks_per_sec'] for r in batch])
            s = next(r for r in summary if int(r['length']) == length and int(r['concurrency']) == concurrency)
            assert np.isclose(values.mean(), float(s['throughput_mean_tasks_per_sec']), rtol=1e-12)
            ax.scatter(concurrency * np.exp(rng.uniform(-.035, .035, len(values))), values, marker='o', s=14, alpha=.65, color='0.25', edgecolors='none', rasterized=False)
            ax.vlines(concurrency, float(s['throughput_ci95_low']), float(s['throughput_ci95_high']), color='#183153', lw=1.5)
            ax.plot(concurrency, values.mean(), 's', color='#183153', ms=4)
        ax.set(xscale='log', xticks=(1, 2, 4, 8, 16), title=f'Chain length {length}', xlabel='Concurrency', ylabel='Tasks / second')
        ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    fig.suptitle('All900 recorded pressure batches; squares and95% intervals from original analysis', y=1.02)
    save(fig, 'pressure_batch_scatter')
    write('pressure_points.csv', stress)
    state = []
    for batch in (1, 2, 3):
        path = ROOT / f'raw/state_paths_batch_{batch}.jsonl'
        for line, row in rows(path):
            state.append({k: row[k] for k in ('batch', 'path', 'condition', 'logical_bytes', 'iteration', 'producer_pid', 'consumer_pid', 'task_wall_ns', 'handoff_roundtrip_ns', 'success')}
                         | {'source': path.relative_to(ROOT).as_posix(), 'line': line})
    assert len(state) == 25200 and all(r['success'] and r['task_wall_ns'] > 0 for r in state)
    groups = defaultdict(list)
    for r in state:
        groups[(r['path'], r['condition'], r['logical_bytes'])].append(r)
    assert len(groups) == 42
    state_summary = []
    bootstrap = np.random.default_rng(20261019)
    for (path, condition, size), group in sorted(groups.items()):
        batch_means = []
        for batch in (1, 2, 3):
            values = [r['task_wall_ns'] / 1e6 for r in group if r['batch'] == batch]
            assert len(values) == 200
            batch_means.append(np.mean(values))
        draws = np.array(batch_means)[bootstrap.integers(0, 3, size=(10000, 3))].mean(axis=1)
        lo, hi = np.percentile(draws, [2.5, 97.5])
        state_summary.append(dict(path=path, condition=condition, logical_bytes=size, transfers=600,
            recorded_batches=3, mean_full_path_wall_ms=float(np.mean(batch_means)), ci95_low_ms=float(lo), ci95_high_ms=float(hi)))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, condition in zip(axes, ('cold', 'hot')):
        for path, color, marker, style in zip(('copy', 'shm', 'memfd'), ('#183153', '#087f8c', '#9557a4'), ('o', 's', '^'), ('-', '--', ':')):
            observations = [r for r in state if r['path'] == path and r['condition'] == condition]
            ax.scatter([r['logical_bytes'] / 1024 * np.exp(rng.uniform(-.025, .025)) for r in observations],
                       [r['task_wall_ns'] / 1e6 for r in observations], color=color, alpha=.12, s=3, rasterized=True)
            cells = sorted((r for r in state_summary if r['path'] == path and r['condition'] == condition), key=lambda r: r['logical_bytes'])
            x = [r['logical_bytes'] / 1024 for r in cells]
            ax.plot(x, [r['mean_full_path_wall_ms'] for r in cells], marker=marker, linestyle=style, ms=4, label=path, color=color)
            ax.vlines(x, [r['ci95_low_ms'] for r in cells], [r['ci95_high_ms'] for r in cells], color=color)
        ax.set(xscale='log', yscale='log', title=condition, xlabel='Logical payload (KiB)', ylabel='Full-path wall time (ms)')
        ax.legend()
    fig.suptitle('All25200 state transfers; mean and descriptive3-batch bootstrap95% intervals', y=1.02)
    save(fig, 'state_size_scatter')
    write('state_points.csv', state); write('state_mean_cluster_intervals.csv', state_summary)
    query = []
    for path in sorted((ROOT / 'retrieval_input_correction_v2/raw').glob('retrieval_n*_build*.jsonl')):
        for line, row in rows(path):
            if 'query_id' not in row:
                continue
            assert row['error'] is None and row['latency_ns'] > 0
            query.append({k: row[k] for k in ('pool_size', 'build', 'mode', 'query_id', 'source_task_id', 'chain_id', 'latency_ns')}
                         | {'source': path.relative_to(ROOT).as_posix(), 'line': line})
    assert len(query) == 14400
    retrieval = table(ROOT / 'retrieval_input_correction_v2/tables/retrieval_quality_scale.csv')
    modes = sorted({r['mode'] for r in query})
    fig, axes = plt.subplots(2, 3, figsize=(10, 6))
    for ax, mode in zip(axes.flat, modes):
        cells = sorted((r for r in retrieval if r['mode'] == mode), key=lambda r: int(r['pool_size']))
        for cell in cells:
            pool = int(cell['pool_size'])
            values = np.array([r['latency_ns'] / 1e6 for r in query if r['mode'] == mode and r['pool_size'] == pool])
            assert len(values) == 720 and np.isclose(np.percentile(values, 95), float(cell['latency_p95_ms']), rtol=1e-12)
            ax.scatter(pool * np.exp(rng.uniform(-.035, .035, len(values))), values, marker='o', s=8, alpha=.4, color='0.2', edgecolors='none', rasterized=False)
            ax.vlines(pool, float(cell['latency_p95_ci95_low_ms']), float(cell['latency_p95_ci95_high_ms']), color='#087f8c', lw=1.4)
            ax.plot(pool, float(cell['latency_p95_ms']), 's', color='#087f8c', ms=4)
        ax.set(xscale='log', yscale='log', title=mode, xlabel='Candidate pool size', ylabel='Query latency (ms)')
    axes.flat[-1].axis('off')
    fig.suptitle('All14400 corrected retrieval observations; p95 and original95% intervals', y=1.02)
    save(fig, 'retrieval_scale_scatter'); write('retrieval_points.csv', query)
    assert all(sha(p) == value for p, value in SOURCES.items())
    audit = dict(status='GENERATED_REQUIRES_ACTUAL_PDF_RENDER_AND_VISUAL_QA', created_utc=datetime.now(timezone.utc).isoformat(),
        source_sha256=sha(Path(__file__)), python=platform.python_version(), numpy=np.__version__, matplotlib=matplotlib.__version__,
        pressure_points=900, pressure_cells=30, state_points=25200, state_cells=42, retrieval_points=14400, retrieval_cells=20,
        no_point_subsampling=True, x_jitter='Visual only, seed20261019; CSV retains original x values',
        pressure_intervals='Original archived30-batch bootstrap mean95%CI; unchanged',
        state_intervals='Postrun descriptive supplement required by protocol6.6: mean full-path wall time; resample3 recorded batches whole,10000 draws,seed20261019. Only3 clusters; limited interval reliability, not a new confirmatory test or a preregistration claim.',
        state_producer_pids_by_batch={str(b): sorted({r['producer_pid'] for r in state if r['batch'] == b}) for b in (1, 2, 3)},
        state_consumer_pids_by_batch={str(b): sorted({r['consumer_pid'] for r in state if r['batch'] == b}) for b in (1, 2, 3)},
        retrieval_intervals='Original corrected-v2 query-latency p95 intervals, same source/population;120 tasks/20 chains/240 query IDs/3 builds, not14400 independent cases',
        timing_boundary='State full-path wall includes preparation/handoff/release; it is not pure RTT. New phase supplement is excluded and never pooled.',
        paid_api_calls=0, timing_reruns=0, original_reports_or_raw_modified=False,
        input_sha256={p.relative_to(ROOT).as_posix(): h for p, h in SOURCES.items()},
        file_sha256={p.name: sha(p) for p in sorted(DEST.iterdir()) if p.is_file()})
    (DEST / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: audit[k] for k in ('status', 'pressure_points', 'state_points', 'retrieval_points')}))


if __name__ == '__main__':
    main()
