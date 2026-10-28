"""Chinese presentation of saved figure CSVs. No raw readers or estimators.

The active B8 plotting/statistical modules are neither imported nor modified.
Figures 1/2/3/4/7/9 require a completed, hash-bound report receipt. Historical
5/6/8 require the already approved offline asset manifest. Unknown cells and
saved CI endpoints stay unknown/unchanged; only labels, font and layout differ.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ARMS = ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')
COLORS = {'T0': '#183153', 'T1': '#183153', 'S0': '#087f8c',
          'S1': '#087f8c', 'SS0': '#6356a5', 'SS1': '#6356a5'}
GREYS = ('#172f50', '#4677a2', '#579e86', '#8a699e', '#bd874d')
DOMAINS = {'service': '服务', 'migration': '迁移', 'resources': '资源',
           'network_permissions': '网络与权限'}
MODES = {'semantic': '语义检索', 'keyword': '关键词检索', 'tag': '标签检索',
         'hybrid': '混合检索', 'hybrid_feedback': '反馈混合检索'}
DISTRIBUTIONS = {'gaussian': '高斯分布', 'sparse': '稀疏分布',
                 'heavy_tail': '重尾分布', 'near_tie': '近并列分布'}
CATEGORIES = {'channel_delivery': '通道交付', 'cleanup': '清理', 'codec': '编解码',
              'memory': '记忆操作', 'memory_index': '记忆索引',
              'provider_wait': '客户端SDK等待', 'retrieval': '检索',
              'router_ipc': '路由IPC', 'runtime_and_recording_other': '其他运行与记录',
              'setup': '初始化', 'state': '状态操作', 'tool': '工具', 'verify': '校验'}
NODES = {'planner': '规划角色', 'retriever': '检索角色', 'executor': '执行角色',
         'reviewer': '审核角色', 'memory': '共享记忆', 'state_exchange': '状态交换',
         'runtime': '运行时', 'UDS_router': 'UDS路由'}
TITLES = {1: '图1　协作角色与信息交接', 2: '图2　五个预定配对的成本差',
          3: '图3　自动任务质量与记录失败构成', 4: '图4　记录耗时与SDK等待',
          5: '图5　独立离线状态传输路径', 6: '图6　量化载荷与完整排序保持',
          7: '图7　新批次检索质量与规模边界', 8: '图8　独立离线长链与并发',
          9: '图9　外部系统级参考'}
NOTES = {
    1: '方法示意，不是性能结果；UDS不表示主实验采用独立Agent进程。',
    2: '目标减基线；缺失点标未完成，不补0；状态配对108任务，其余120任务；本地物化为探索性。',
    3: '自动验证；原保守绝对质量区间；极小错误计数详见数据表；全部预定失败保留，不作质量不劣判断。',
    4: '互斥分区归属时间、SDK占比和任务分位数分开；宿主睡眠不扣除；不是纯推理或排队时延。',
    5: '历史独立离线批次；每单元n=600；实心实线p50、空心点线p95；形状区分路径，分位数不是CI。',
    6: '历史独立离线批次；每单元n=200；原Wilson 95%区间仅覆盖8候选完整排序。',
    7: '原审计FAIL与容量缺失保留；缺失点标未完成并断线，不补0；候选命中不等于实际采用或任务获益。',
    8: '历史独立离线批次；每单元30批；吞吐CI来自原批重采样，尾时延为保存分位数。',
    9: '固定60任务×2次重复；原保存区间；仅三个端点的系统级参考，不作IPC机制归因。',
}
NUMERIC = {
    'value', 'ci95_low', 'ci95_high', 'paired_difference', 'failed_terminal_units',
    'value_sec', 'p50', 'p95', 'p99', 'pool_size', 'missing_query_units',
    'recall_at_10', 'recall_at_10_ci95_low', 'recall_at_10_ci95_high',
    'mrr', 'mrr_ci95_low', 'mrr_ci95_high', 'false_retrieval',
    'false_retrieval_ci95_low', 'false_retrieval_ci95_high',
    'latency_p95_ms', 'latency_p95_ci95_low_ms', 'latency_p95_ci95_high_ms',
    'logical_bytes', 'n', 'p50_ms', 'p95_ms', 'unique_states',
    'decision_rate', 'wilson95_low', 'wilson95_high',
    'encoded_embedding_scale_bytes_mean', 'length', 'concurrency',
    'recorded_batches', 'throughput_mean_tasks_per_sec', 'throughput_ci95_low',
    'throughput_ci95_high', 'task_latency_p95_sec',
    'native_observable_frame_ack_bytes_per_chain',
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        original = list(csv.DictReader(f))
    result = []
    for row in original:
        decoded = dict(row)
        for name in row.keys() & NUMERIC:
            value = row[name]
            if value in ('', 'null', 'None', 'UNKNOWN'):
                decoded[name] = None
            else:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError(f'nonfinite saved value: {path.name}/{name}')
                decoded[name] = number
        result.append(decoded)
    return result, original


def configure(font, output):
    os.environ['MPLCONFIGDIR'] = str(output / 'mpl_cache')
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import font_manager, ft2font
    charmap = ft2font.FT2Font(str(font)).get_charmap()
    font_names = ft2font.FT2Font(str(font)).get_sfnt()
    font_version = next((value.decode('utf-16-be', errors='replace')
        for key, value in font_names.items() if key[0] == 3 and key[3] == 5), 'NOT_REPORTED')
    required = ''.join(TITLES.values()) + ''.join(NOTES.values()) + '未知首次物化复用物化成功率字节'
    missing = sorted({c for c in required if '\u4e00' <= c <= '\u9fff' and ord(c) not in charmap})
    if missing:
        raise ValueError('selected font lacks Chinese glyphs: ' + ''.join(missing))
    font_manager.fontManager.addfont(str(font))
    family = font_manager.FontProperties(fname=str(font)).get_name()
    matplotlib.rcParams.update({'font.family': family, 'font.size': 8,
        'axes.labelsize': 8, 'axes.titlesize': 9, 'legend.fontsize': 7,
        'axes.unicode_minus': True, 'pdf.fonttype': 42, 'svg.fonttype': 'path',
        'axes.spines.top': False, 'axes.spines.right': False})
    import matplotlib.pyplot as plt
    return plt, family, matplotlib.__version__, font_version


def point(ax, x, value, low=None, high=None, color='#183153', marker='o', label=None,
          missing_label='未观测'):
    if value is None:
        ax.text(x, .45, missing_label, rotation=90, ha='center', va='center',
                transform=ax.get_xaxis_transform(), fontsize=7)
        return
    if (low is None) != (high is None):
        raise ValueError('saved interval has only one endpoint')
    if low is not None:
        # Draw the exact saved endpoints, including intervals outside a point.
        ax.vlines(x, low, high, color=color, linewidth=.9)
        ax.hlines((low, high), x-.04, x+.04, color=color, linewidth=.9)
    ax.plot(x, value, marker=marker, color=color, label=label, ms=4)


def method(rows, plt, width, height):
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    fig, ax = plt.subplots(figsize=(width, height))
    positions = {'planner': (.02, .75), 'retriever': (.35, .75), 'executor': (.68, .75),
        'reviewer': (.68, .4), 'memory': (.35, .05), 'state_exchange': (.02, .4),
        'runtime': (.02, .05), 'UDS_router': (.35, .4)}
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis('off')
    for name, (x, y) in positions.items():
        ax.add_patch(FancyBboxPatch((x, y), .21, .16, boxstyle='round,pad=.01',
                                   fill=False, linewidth=1))
        ax.text(x+.105, y+.08, NODES[name], ha='center', va='center')
    for row in rows:
        x1, y1 = positions[row['from']]; x2, y2 = positions[row['to']]
        ax.add_patch(FancyArrowPatch((x1+.105, y1+.08), (x2+.105, y2+.08),
            arrowstyle='->', mutation_scale=10, shrinkA=27, shrinkB=27,
            connectionstyle='arc3,rad=0.45' if {row['from'], row['to']} == {'retriever', 'memory'} else 'arc3,rad=0'))
    return fig


def paired(rows, plt, width, height):
    fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 7.0)))
    labels = {'provider_input_tokens': '输入词元差',
        'observable_ipc_bytes': '运行时IPC差\n（B）',
        'local_state_materialized_bytes': '本地物化差\n（B，探索性）'}
    for ax, (metric, label) in zip(axes, labels.items()):
        selected = [r for r in rows if r['metric'] == metric]
        if len(selected) != 5:
            raise ValueError('five saved primary pairs required per panel')
        for i, r in enumerate(selected):
            point(ax, i, r.get('paired_difference'), r.get('ci95_low'), r.get('ci95_high'),
                  missing_label='未完成' if metric == 'provider_input_tokens' else '未观测')
        ax.axhline(0, color='0.5', lw=.7)
        ax.set(xticks=range(5), xticklabels=[r['baseline']+'→'+r['target'] for r in selected], ylabel=label)
        if metric == 'observable_ipc_bytes':
            # Exact saved values make the three small memory effects readable
            # beside the two large negative effects. No axis/data rescaling.
            detail = ['保存配对点与95%区间（B）']
            for r in selected:
                v, lo, hi = r.get('paired_difference'), r.get('ci95_low'), r.get('ci95_high')
                detail.append(r['baseline']+'→'+r['target']+': '+
                    (f'{v:.2f} [{lo:.2f}, {hi:.2f}]' if v is not None and lo is not None and hi is not None else '未观测'))
            ax.text(.98, .06, '\n'.join(detail), transform=ax.transAxes,
                    ha='right', va='bottom', fontsize=5.3 if width < 4 else 6,
                    bbox={'facecolor': 'white', 'edgecolor': '.7', 'alpha': .95, 'pad': 3})
        ax.set_xlim(-.35, 4.35)
        if width < 4:
            ax.tick_params(axis='x', labelrotation=30)
            for tick in ax.get_xticklabels():
                tick.set_ha('right')
        ax.grid(axis='y', alpha=.2)
    return fig


def quality(rows, plt, width, height):
    single = width < 4
    if single:
        fig, axes = plt.subplots(5, 1, figsize=(width, max(height, 9.2)))
        domain_axes, failure_ax, note_ax = axes[:4], axes[4], None
    else:
        fig, axes = plt.subplots(3, 2, figsize=(width, max(height, 7.8)))
        domain_axes, failure_ax, note_ax = list(axes.flat)[:4], axes[2, 0], axes[2, 1]
    for ax, (domain, title) in zip(domain_axes, DOMAINS.items()):
        selected = {r['arm']: r for r in rows if r.get('level') == domain}
        if set(selected) != set(ARMS):
            raise ValueError('all six arms required in each saved domain')
        for i, arm in enumerate(ARMS):
            r = selected[arm]
            point(ax, i, r.get('value'), r.get('ci95_low'), r.get('ci95_high'),
                  color=COLORS[arm], marker='s' if arm.endswith('1') else 'o')
        ax.set(title=title, xticks=range(6), xticklabels=ARMS,
               ylabel='自动任务成功率', ylim=(-.02, 1.02)); ax.grid(axis='y', alpha=.2)
        if single:
            ax.tick_params(axis='x', labelrotation=45)
            for tick in ax.get_xticklabels():
                tick.set_ha('right')
    failures = [r for r in rows if r.get('kind') == 'recorded_failure_type_count']
    bottom = {arm: 0 for arm in ARMS}
    for error in sorted({r['error_type'] for r in failures}):
        records = [r for r in failures if r['error_type'] == error]
        xs = [ARMS.index(r['arm']) for r in records]
        ys = [r['failed_terminal_units'] for r in records]
        if any(y is None for y in ys):
            raise ValueError('unknown recorded error count cannot become zero')
        failure_ax.bar(xs, ys, bottom=[bottom[r['arm']] for r in records], label='记录类型：'+('已中止（aborted）' if error == 'aborted' else error))
        for r in records:
            bottom[r['arm']] += r['failed_terminal_units']
    failure_ax.set(xticks=range(6), xticklabels=ARMS, ylabel='已记录失败单元数')
    if single:
        failure_ax.tick_params(axis='x', labelrotation=45)
    handles, labels = failure_ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .065),
               ncol=1 if single else 2, fontsize=5, frameon=True)
    if note_ax is not None:
        note_ax.axis('off')
        note_ax.text(0, .65, '仅按已记录错误类型计数。\n未知类型仍为未知。\n不重打质量分。\n不作不劣判断。', fontsize=7)
    fig._presentation_rect = (0, .14 if single else .20, 1, .95)
    return fig


def timing(rows, plt, width, height):
    single = width < 4
    fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 9.0 if single else 8.5)))
    exclusive = [r for r in rows if r.get('kind') == 'recorded_exclusive_partition_mass']
    categories = list(dict.fromkeys(r['category'] for r in exclusive))
    hatches = ('/', '\\', '|', '-', '+', 'x', 'o', 'O', '.', '*', '//', 'xx', '..')
    if categories and all(r.get('value_sec') is not None for r in exclusive):
        bottom = [0.] * 6
        for category_index, category in enumerate(categories):
            if category not in CATEGORIES:
                raise ValueError('unmapped saved span category: '+category)
            selected = {r['arm']: r for r in exclusive if r['category'] == category}
            if set(selected) != set(ARMS):
                raise ValueError('incomplete exclusive partition')
            values = [selected[a]['value_sec'] for a in ARMS]
            if category_index >= len(hatches):
                raise ValueError('new category requires an explicit grayscale hatch')
            axes[0].bar(range(6), values, bottom=bottom, label=CATEGORIES[category],
                        hatch=hatches[category_index], edgecolor='0.25', linewidth=.4)
            bottom = [a+b for a, b in zip(bottom, values)]
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, .93),
                   fontsize=6, ncol=2 if single else 3, frameon=True)
    else:
        axes[0].text(.5, .5, '互斥分区未观测', transform=axes[0].transAxes, ha='center')
    axes[0].set(xticks=range(6), xticklabels=ARMS, ylabel='互斥分区归属时间（s）')
    share = {r['arm']: r for r in rows if r.get('metric') == 'provider_wait_fraction'}
    wall = {r['arm']: r for r in rows if r.get('metric') == 'wall_sec'}
    for i, arm in enumerate(ARMS):
        r = share.get(arm, {})
        point(axes[1], i, r.get('value'), r.get('ci95_low'), r.get('ci95_high'), color=COLORS[arm])
    axes[1].set(xticks=range(6), xticklabels=ARMS, ylabel='SDK等待占比')
    for q, marker in (('p50', 'o'), ('p95', 's'), ('p99', '^')):
        for i, arm in enumerate(ARMS):
            point(axes[2], i, wall.get(arm, {}).get(q), color=COLORS[arm], marker=marker,
                  label=q if i == 0 else None)
    axes[2].set(xticks=range(6), xticklabels=ARMS, ylabel='任务耗时分位数（s）')
    axes[2].legend()
    for ax in axes:
        ax.grid(axis='y', alpha=.2)
    fig._presentation_rect = (0, .12 if single else .09, 1, .79 if single else .80)
    return fig


def state_paths(rows, plt, width, height):
    from matplotlib.lines import Line2D
    if len(rows) != 42 or any(r['n'] != 600 for r in rows):
        raise ValueError('approved figure05 requires 42 cells with n=600')
    fig, axes = plt.subplots(2, 1, figsize=(width, max(height, 5.2)), sharex=True)
    for ax, condition, title in zip(axes, ('cold', 'hot'), ('首次物化', '复用物化')):
        for i, (path, label) in enumerate((('copy', '复制'), ('shm', 'SHM'), ('memfd', 'memfd'))):
            values = sorted((r for r in rows if r['condition'] == condition and r['path'] == path), key=lambda r: r['logical_bytes'])
            if len(values) != 7:
                raise ValueError('seven saved payload sizes required')
            x = [r['logical_bytes']/1024 for r in values]
            ax.plot(x, [r['p50_ms'] for r in values], marker=('o', 's', '^')[i], color=GREYS[i], label=label)
            ax.plot(x, [r['p95_ms'] for r in values], linestyle=':',
                    marker=('o', 's', '^')[i], markerfacecolor='none',
                    markeredgecolor=GREYS[i], color=GREYS[i])
        ax.set(xscale='log', ylabel='任务往返耗时（ms）', title=title); ax.grid(alpha=.2)
        handles, labels = ax.get_legend_handles_labels()
        handles += [Line2D([0], [0], color='#6b7280', marker='o'),
                    Line2D([0], [0], color='#6b7280', linestyle=':',
                           marker='o', markerfacecolor='none')]
        ax.legend(handles, labels+['p50', 'p95'], ncol=3)
    axes[-1].set_xlabel('逻辑状态大小（KiB）')
    return fig


def quantization(rows, plt, width, height):
    if len(rows) != 64 or any(r['unique_states'] != 200 for r in rows):
        raise ValueError('approved figure06 requires 64 cells with n=200')
    fig, axes = plt.subplots(2, 2, figsize=(width, max(height, 6.2)), sharey=True)
    for ax, (distribution, title) in zip(axes.flat, DISTRIBUTIONS.items()):
        for i, precision in enumerate(('float32', 'float16', 'int8', 'adaptive')):
            selected = [r for r in rows if r['distribution'] == distribution and r['precision'] == precision]
            color = '#bf6b25' if precision == 'int8' else GREYS[i]
            label = '自适应' if precision == 'adaptive' else precision
            for n, r in enumerate(selected):
                x = r['encoded_embedding_scale_bytes_mean']
                ax.vlines(x, r['wilson95_low'], r['wilson95_high'], color=color, linewidth=.9)
                ax.plot(x, r['decision_rate'], marker=('o', 's', '^', 'D')[i], color=color,
                        label=label if n == 0 else None, ms=4)
        ax.set(xscale='log', ylim=(-.02, 1.02), title=title,
               xlabel='编码载荷（B）', ylabel='完整排序保持率'); ax.grid(alpha=.2); ax.legend(loc='lower left')
    return fig


def retrieval(rows, plt, width, height):
    single = width < 4
    fig, axes = plt.subplots(4, 1, figsize=(width, max(height, 9.2))) if single else plt.subplots(2, 2, figsize=(width, max(height, 6.8)))
    panels = (('recall_at_10', '有答案查询：Recall@10'), ('mrr', '有答案查询：MRR@10'),
              ('false_retrieval', '无答案查询：误检率'), ('latency_p95_ms', '查询时延p95（ms）'))
    markers = ('o', 's', '^', 'D', 'v')
    line_styles = ('-', '--', ':', '-.', (0, (5, 1, 1, 1, 1, 1)))
    for ax, (metric, title) in zip(axes.flat, panels):
        for i, mode in enumerate(sorted({r['mode'] for r in rows})):
            if mode not in MODES:
                raise ValueError('unmapped retrieval mode: '+mode)
            group = sorted((r for r in rows if r['mode'] == mode), key=lambda r: r['pool_size'])
            x = [r['pool_size'] for r in group]
            y = [r.get(metric) if r.get(metric) is not None else float('nan') for r in group]
            ax.plot(x, y, marker=markers[i], linestyle=line_styles[i],
                    ms=3, color=GREYS[i], label=MODES[mode])
            prefix, suffix = ('latency_p95', '_ms') if metric == 'latency_p95_ms' else (metric, '')
            for xx, r in zip(x, group):
                lo, hi = r.get(prefix+'_ci95_low'+suffix), r.get(prefix+'_ci95_high'+suffix)
                if (lo is None) != (hi is None):
                    raise ValueError('incomplete saved retrieval interval')
                if r.get(metric) is None:
                    continue  # Missing-mode identity is explicit in the panel title below.
                elif lo is not None:
                    ax.vlines(xx, lo, hi, color=GREYS[i], linewidth=.8)
        missing = [MODES[r['mode']]+'：'+str(int(r['pool_size']))+'未完成'
                   for r in rows if r.get(metric) is None]
        display_title = title + ('\n'+'；'.join(missing) if missing else '')
        ax.set(xscale='log', title=display_title, xlabel='候选池记录数')
        if metric == 'false_retrieval':
            ax.set_ylim(-.02, 1.02)
        ax.grid(alpha=.2)
    first = axes.flat[0]
    handles, labels = first.get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .065),
               fontsize=6, ncol=2 if single else 5, frameon=True)
    fig._presentation_rect = (0, .14 if single else .18, 1, .95)
    return fig


def stress(rows, plt, width, height):
    if len(rows) != 30 or any(r['recorded_batches'] != 30 for r in rows):
        raise ValueError('approved figure08 requires 30 complete cells')
    fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 7.2)))
    for i, concurrency in enumerate((1, 2, 4, 8, 16)):
        selected = sorted((r for r in rows if r['concurrency'] == concurrency), key=lambda r: r['length'])
        x = [r['length'] for r in selected]
        for xx, r in zip(x, selected):
            axes[0].vlines(xx, r['throughput_ci95_low'], r['throughput_ci95_high'], color=GREYS[i], lw=.8)
        axes[0].plot(x, [r['throughput_mean_tasks_per_sec'] for r in selected], marker=('o', 's', '^', 'D', 'v')[i], color=GREYS[i], label=f'并发数 {concurrency}')
        axes[1].plot(x, [r['task_latency_p95_sec'] for r in selected], marker=('o', 's', '^', 'D', 'v')[i], color=GREYS[i])
        axes[2].plot(x, [r['native_observable_frame_ack_bytes_per_chain']/1024 for r in selected], marker=('o', 's', '^', 'D', 'v')[i], color=GREYS[i])
    for ax in axes:
        ax.set_xscale('log', base=2); ax.grid(alpha=.2); ax.set_xlabel('因果链长度')
    axes[0].set_ylabel('吞吐（任务/s）')
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, .93),
               ncol=3 if width < 4 else 5, fontsize=7, frameon=True)
    fig._presentation_rect = (0, .12 if width < 4 else .08, 1, .84)
    axes[1].set_ylabel('任务时延p95（s）')
    axes[2].set_ylabel('原生帧与ACK（KiB/链）')
    return fig


def external(rows, plt, width, height):
    fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 6.2)))
    systems = ('native_T0', 'autogen', 'langgraph')
    for ax, metric, label in zip(axes, ('quality_success', 'provider_input_tokens', 'wall_sec'),
            ('自动任务成功率', '输入词元/执行单元', '任务记录耗时（s）')):
        for i, system in enumerate(systems):
            selected = [r for r in rows if r['system'] == system and r['metric'] == metric]
            if len(selected) != 1:
                raise ValueError('exactly nine saved external rows required')
            r = selected[0]; point(ax, i, r.get('value'), r.get('ci95_low'), r.get('ci95_high'))
        ax.set(xticks=range(3), xticklabels=('原生T0', 'AutoGen', 'LangGraph'), ylabel=label); ax.grid(axis='y', alpha=.2)
    return fig


DRAW = {1: method, 2: paired, 3: quality, 4: timing, 5: state_paths,
        6: quantization, 7: retrieval, 8: stress, 9: external}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--figures', default='1,2,3,4,5,6,7,8,9')
    parser.add_argument('--report-root', type=Path)
    parser.add_argument('--report-stage-result', type=Path)
    parser.add_argument('--offline-manifest', type=Path, required=True)
    parser.add_argument('--historical-figures-root', type=Path,
        help='explicit read-only mount of the three approved historical figure CSVs; hashes still come from the unchanged manifest')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--font', type=Path)
    parser.add_argument('--expected-font-sha256')
    parser.add_argument('--width', choices=('single', 'double', 'both'), default='double')
    parser.add_argument('--formats', default='png', help='comma-separated png,svg,pdf; PDF authoring requires the separate artifact marker workflow')
    parser.add_argument('--dpi', type=int, default=600)
    args = parser.parse_args()
    numbers = [int(n) for n in args.figures.split(',')]
    formats = args.formats.split(',')
    if not formats or len(set(formats)) != len(formats) or set(formats) - {'png', 'svg', 'pdf'}:
        raise ValueError('choose unique output formats from png,svg,pdf')
    if not numbers or len(set(numbers)) != len(numbers) or set(numbers) - set(DRAW):
        raise ValueError('choose unique figure numbers from 1..9')
    if args.out.exists():
        raise FileExistsError('fresh presentation output required')
    manifest = json.loads(args.offline_manifest.read_text(encoding='utf-8'))
    authority = {}
    if set(numbers) - {5, 6, 8}:
        if not args.report_root or not args.report_stage_result:
            raise ValueError('completed report root and stage result required')
        availability = args.report_root/'analysis/report_availability.json'
        result = json.loads(args.report_stage_result.read_text(encoding='utf-8'))
        if result.get('stage') != 'report' or result.get('exit_code') != 0 or result.get('receipt_sha256') != sha(availability):
            raise ValueError('report is not completed and receipt-bound; do not read partial CSVs')
        authority = json.loads(availability.read_text(encoding='utf-8'))
        if authority.get('status') != 'PREPARED_REQUIRES_POSTRUN_SCHEMA_NUMERIC_AND_ACTUAL_VISUAL_QA':
            raise ValueError('unrecognized completed report authority')
    sources = []
    data = {}
    for number in numbers:
        if number in (5, 6, 8):
            candidates = [(batch, asset) for batch in manifest['batches'] for asset in batch['assets']
                if asset.get('target_figure') == number and asset['relative_path'].endswith(f'/figure_{number:02}.csv')]
            if len(candidates) != 1:
                raise ValueError('exact approved historical figure asset required')
            batch, asset = candidates[0]
            path = (args.historical_figures_root/f'figure_{number:02}.csv'
                    if args.historical_figures_root else Path(batch['source_root'])/asset['relative_path'])
            expected = asset['sha256']
            source_kind = 'APPROVED_HISTORICAL_OFFLINE_MECHANISM'
        else:
            relative = f'figures/figure_{number:02}.csv'
            expected = authority['outputs_sha256'][relative]
            path = args.report_root/relative
            source_kind = 'COMPLETED_REPORT_SAVED_FIGURE_TABLE'
            if number == 9:
                record = next(r for r in authority['figures'] if r['figure'] == 9)
                if record.get('equivalence_status') != 'COMPARABLE_SYSTEM_LEVEL_REFERENCE':
                    raise ValueError('external three-endpoint system-reference gate required')
        actual = sha(path)
        if actual != expected:
            raise ValueError('figure CSV SHA differs from authority')
        rows, original = read_csv(path); data[number] = rows
        sources.append({'figure': number, 'path': str(path.resolve()), 'sha256': actual,
            'rows': len(rows), 'source_kind': source_kind,
            'original_cells_sha256': hashlib.sha256(json.dumps(original, ensure_ascii=False,
                sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()})
    font = args.font or next((p for p in (Path('C:/Windows/Fonts/msyh.ttc'),
        Path('C:/Windows/Fonts/simhei.ttf'), Path('C:/Windows/Fonts/Deng.ttf')) if p.is_file()), None)
    if font is None or not font.is_file():
        raise ValueError('an installed Chinese font file is required')
    if args.expected_font_sha256 and sha(font) != args.expected_font_sha256:
        raise ValueError('Chinese font SHA differs from the explicitly chosen font')
    args.out.mkdir(parents=True)
    receipt = {'status': 'RENDER_STARTED_NOT_SCIENTIFIC_PASS', 'source_sha256': sha(Path(__file__)),
        'font_path': str(font.resolve()), 'font_sha256': sha(font), 'input_sources': sources,
        'offline_manifest_sha256': sha(args.offline_manifest), 'requested_figures': numbers,
        'requested_formats': formats,
        'data_or_statistics_recomputed': False, 'unknown_zero_fill': False,
        'saved_ci_endpoints_changed': False, 'outputs': [], 'active_b8_sources_modified': False}
    try:
        plt, family, version, font_version = configure(font, args.out)
        receipt.update(font_family=family, font_version=font_version, matplotlib_version=version,
                       historical_figures_explicit_mount=str(args.historical_figures_root)
                       if args.historical_figures_root else None)
        widths = ('single', 'double') if args.width == 'both' else (args.width,)
        for number in numbers:
            for label in widths:
                width = 7.4 if label == 'double' else 3.6
                fig = DRAW[number](data[number], plt, width, 5.5)
                fig.suptitle(TITLES[number], fontsize=11, y=.99)
                note = NOTES[number]
                if label == 'single':
                    note = '\n'.join(note[i:i+26] for i in range(0, len(note), 26))
                fig.text(.02, .012, note, fontsize=6.7, va='bottom')
                fig.tight_layout(rect=getattr(fig, '_presentation_rect',
                    (0, .08 if label == 'double' else .12, 1, .95)))
                for ext in formats:
                    path = args.out/f'figure_{number:02}_zh_{label}.{ext}'
                    if path.exists():
                        raise FileExistsError('presentation artifact already exists')
                    fig.savefig(path, dpi=args.dpi)
                    receipt['outputs'].append({'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path)})
                plt.close(fig)
        for source in sources:
            if sha(source['path']) != source['sha256']:
                raise ValueError('source changed during render')
        receipt['status'] = 'CHINESE_PRESENTATION_RENDERED_REQUIRES_ROOT_NUMERIC_VISUAL_REVIEW'
    except BaseException as error:
        receipt['status'] = 'FAILED_RETAINED_PARTIAL_OUTPUT'
        receipt['error_type'] = type(error).__name__
        raise
    finally:
        (args.out/'render_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False,
            indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': receipt['status'], 'figures': numbers, 'out': str(args.out)}))


if __name__ == '__main__':
    main()
