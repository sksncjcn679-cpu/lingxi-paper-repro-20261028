"""Rendering only. Inputs are saved point/CI tables; no estimators or raw readers."""
from __future__ import annotations

from pathlib import Path

COLORS = {'T0': '#183153', 'T1': '#183153', 'S0': '#087f8c', 'S1': '#087f8c',
          'SS0': '#6356a5', 'SS1': '#6356a5'}
ARMS = ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')


def pyplot():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 8, 'axes.labelsize': 8, 'axes.titlesize': 8,
                         'legend.fontsize': 6, 'pdf.fonttype': 42, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False})
    return plt


def point(ax, x, value, low=None, high=None, label=None, color='#183153', marker='o'):
    if value is None:
        ax.text(x, .5, 'UNKNOWN', rotation=90, ha='center', va='center',
                transform=ax.get_xaxis_transform(), fontsize=6)
        return
    if low is not None and high is not None:
        # Preserve saved endpoints even if a point lies outside its interval.
        ax.vlines(x, low, high, color=color, linewidth=.9)
        ax.hlines((low, high), x-.04, x+.04, color=color, linewidth=.9)
        ax.plot(x, value, marker=marker, color=color, label=label)
    else:
        ax.plot(x, value, marker=marker, color=color, label=label)


def paired_effects(rows, width, height):
    plt = pyplot(); fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 6.4)), squeeze=False)
    for ax, metric, label in zip(axes[:, 0], ('provider_input_tokens', 'observable_ipc_bytes', 'local_state_materialized_bytes'),
                                 ('Paired input token difference', 'Paired observable IPC difference (B)',
                                  'Local materialized bytes difference (exploratory)')):
        selected = [r for r in rows if r['metric'] == metric]
        for i, row in enumerate(selected):
            point(ax, i, row.get('paired_difference'), row.get('ci95_low'), row.get('ci95_high'))
        ax.axhline(0, color='0.5', lw=.7)
        ax.set(xticks=range(len(selected)), xticklabels=[r['baseline']+'→'+r['target'] for r in selected], ylabel=label)
        ax.grid(axis='y', alpha=.2)
    return fig


def domain_quality(rows, width, height):
    plt = pyplot(); domains = ('service', 'migration', 'resources', 'network_permissions')
    fig, axes = plt.subplots(3, 2, figsize=(width, max(height, 6.4)), squeeze=False)
    for ax, domain in zip(axes.flat, domains):
        selected = {r['arm']: r for r in rows if r.get('level') == domain}
        for i, arm in enumerate(ARMS):
            row = selected.get(arm, {})
            point(ax, i, row.get('value'), row.get('ci95_low'), row.get('ci95_high'),
                  color=COLORS[arm], marker='s' if arm.endswith('1') else 'o')
        ax.set(title=domain.replace('_', ' '), xticks=range(6), xticklabels=ARMS,
               ylabel='Observed task quality', ylim=(-.02, 1.02))
        ax.grid(axis='y', alpha=.2)
    failures = [r for r in rows if r.get('kind') == 'recorded_failure_type_count']
    bottom = [0] * 6
    for error in sorted({r['error_type'] for r in failures}):
        values = [next((r['failed_terminal_units'] for r in failures if r['arm'] == arm and r['error_type'] == error), 0) for arm in ARMS]
        axes[2, 0].bar(range(6), values, bottom=bottom, label=error)
        bottom = [a+b for a, b in zip(bottom, values)]
    axes[2, 0].set(xticks=range(6), xticklabels=ARMS, ylabel='Recorded failed task units')
    axes[2, 0].legend(fontsize=4); axes[2, 1].axis('off')
    axes[2, 1].text(0, .5, 'Error types use explicit recorded types.\nUnknown types remain unknown.\nCounts do not rescore quality.', fontsize=6)
    return fig


def timing_saved(rows, width, height):
    """Plot saved SDK-share and saved task quantiles; never manufacture a stack."""
    plt = pyplot(); fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 7.0)))
    exclusive = [r for r in rows if r.get('kind') == 'recorded_exclusive_partition_mass']
    categories = list(dict.fromkeys(r['category'] for r in exclusive))
    if categories and all(r.get('value_sec') is not None for r in exclusive):
        bottom = [0.] * len(ARMS)
        for category in categories:
            selected = {r['arm']: r for r in exclusive if r['category'] == category}
            if set(selected) != set(ARMS):
                raise ValueError('incomplete recorded exclusive partition')
            values = [selected[arm]['value_sec'] for arm in ARMS]
            axes[0].bar(range(6), values, bottom=bottom, label=category)
            bottom = [a+b for a, b in zip(bottom, values)]
        axes[0].legend(fontsize=5, ncol=2)
    else:
        axes[0].text(.5, .5, 'UNKNOWN exclusive partition', transform=axes[0].transAxes, ha='center')
    axes[0].set(xticks=range(6), xticklabels=ARMS, ylabel='Exclusive wall mass (s)')
    share = {r['arm']: r for r in rows if r.get('metric') == 'provider_wait_fraction'}
    for i, arm in enumerate(ARMS):
        row = share.get(arm, {})
        point(axes[1], i, row.get('value'), row.get('ci95_low'), row.get('ci95_high'), color=COLORS[arm])
    axes[1].set(xticks=range(6), xticklabels=ARMS, ylabel='Client SDK wait / task wall')
    wall = {r['arm']: r for r in rows if r.get('metric') == 'wall_sec'}
    for quantile, marker in (('p50', 'o'), ('p95', 's'), ('p99', '^')):
        for i, arm in enumerate(ARMS):
            point(axes[2], i, wall.get(arm, {}).get(quantile), color=COLORS[arm], marker=marker,
                  label=quantile if i == 0 else None)
    axes[2].set(xticks=range(6), xticklabels=ARMS, ylabel='Saved task wall quantile (s)')
    axes[2].legend()
    for ax in axes:
        ax.grid(axis='y', alpha=.2)
    return fig


def exclusive_stack(rows, width, height):
    """Accept already-saved exclusive-span arm summaries; nested traces forbidden."""
    plt = pyplot(); fig, ax = plt.subplots(figsize=(width, height))
    categories = list(dict.fromkeys(r['category'] for r in rows))
    bottom = [0.] * len(ARMS)
    for category in categories:
        selected = {r['arm']: r for r in rows if r['category'] == category}
        if any(arm not in selected or selected[arm].get('value_sec') is None for arm in ARMS):
            raise ValueError('incomplete saved exclusive decomposition; do not replace with zero')
        values = [selected[arm]['value_sec'] for arm in ARMS]
        ax.bar(range(6), values, bottom=bottom, label=category)
        bottom = [a+b for a, b in zip(bottom, values)]
    ax.set(xticks=range(6), xticklabels=ARMS, ylabel='Saved exclusive task wall spans (s)')
    ax.legend(fontsize=5, ncol=2); return fig


def retrieval_curves(rows, width, height):
    plt = pyplot(); fig, axes = plt.subplots(2, 2, figsize=(width, height))
    for ax, metric, title in zip(axes.flat, ('recall_at_10', 'mrr', 'false_retrieval', 'latency_p95_ms'),
            ('Answerable Recall@10', 'Answerable MRR@10', 'No-answer false retrieval', 'Native query p95 (ms)')):
        for mode in sorted({r['mode'] for r in rows}):
            group = sorted((r for r in rows if r['mode'] == mode), key=lambda r: r['pool_size'])
            x = [r['pool_size'] for r in group]
            # A missing point breaks the line. It is never replaced by zero.
            y = [r.get(metric) if r.get(metric) is not None else float('nan') for r in group]
            ax.plot(x, y, marker='o', ms=3, label=mode)
            suffix = '_ms' if metric == 'latency_p95_ms' else ''
            prefix = 'latency_p95' if metric == 'latency_p95_ms' else metric
            for xx, row in zip(x, group):
                lo, hi = row.get(prefix+'_ci95_low'+suffix), row.get(prefix+'_ci95_high'+suffix)
                if lo is not None and hi is not None:
                    ax.vlines(xx, lo, hi, linewidth=.8)
        ax.set(xscale='log', title=title, xlabel='Prepared candidate pool size')
        ax.grid(alpha=.2)
    axes[0, 0].legend(fontsize=5); return fig


def overview(rows, width, height):
    plt = pyplot(); from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    fig, ax = plt.subplots(figsize=(width, height)); ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis('off')
    positions = {'planner': (.02, .75), 'retriever': (.35, .75), 'executor': (.68, .75),
                 'reviewer': (.68, .4), 'memory': (.35, .05), 'state_exchange': (.02, .4),
                 'runtime': (.02, .05), 'UDS_router': (.35, .4)}
    for name, (x, y) in positions.items():
        ax.add_patch(FancyBboxPatch((x, y), .21, .16, boxstyle='round,pad=.01', fill=False, linewidth=1))
        ax.text(x+.105, y+.08, name.replace('_', '\n'), ha='center', va='center', fontsize=6)
    for row in rows:
        x1, y1 = positions[row['from']]; x2, y2 = positions[row['to']]
        ax.add_patch(FancyArrowPatch((x1+.105, y1+.08), (x2+.105, y2+.08),
                                    arrowstyle='->', mutation_scale=10, shrinkA=26, shrinkB=26))
    return fig


def external_reference(rows, width, height):
    plt = pyplot(); fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 5.2)))
    systems = ('native_T0', 'autogen', 'langgraph')
    for ax, metric in zip(axes, ('quality_success', 'provider_input_tokens', 'wall_sec')):
        for i, system in enumerate(systems):
            row = next(r for r in rows if r['system'] == system and r['metric'] == metric)
            point(ax, i, row['value'], row.get('ci95_low'), row.get('ci95_high'))
        ax.set(xticks=range(3), xticklabels=systems, ylabel=metric)
        ax.grid(axis='y', alpha=.2)
    return fig


def export(draw, rows, destination, stem, height=5.2):
    plt = pyplot(); exports = []
    for label, width in (('single', 3.35), ('double', 7.0)):
        figure = draw(rows, width, height if label == 'double' else height*1.12)
        figure.set_layout_engine('constrained')
        for ext in ('pdf', 'svg', 'png'):
            path = Path(destination) / f'{stem}_{label}.{ext}'
            if path.exists():
                raise RuntimeError('render destination already exists')
            figure.savefig(path, dpi=600)
            exports.append(path)
        plt.close(figure)
    return exports
