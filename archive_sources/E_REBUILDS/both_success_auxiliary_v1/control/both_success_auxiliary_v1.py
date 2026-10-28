"""Append-only protocol section 6 item 5 descriptive supplement.

Prepared source only until root reviews and executes it in the fixed image.
No main statistics are changed. Only the frozen _chain_arrays/np.mean path is
called for five missing token auxiliaries; twenty saved auxiliaries are copied.
"""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

STUDY = 'protocol_repair_20261020_v1'
FREEZE_SHA = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
SOURCE_SHA = 'a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562'
EXPECTED = {
    'observations': ('1c2031eec1ad922c753199fc4cc0a7760fdeca0f110becc6d2e83db0dc12281a', 45655316),
    'primary': ('5a05487490072f1989a02b2bd99f39c1bfa07acba0f31af96a8579e73ddfaa2a', 7269836),
    'kernel': ('7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373', 16535),
    'contract': ('5ec70ab8b2d87fd248f92a13ff604bb2cd449b4b99e40e38aff6afd3a43bbbe8', 7948),
    'formal_plan': ('3944e9ae3b5de942dd6e463d7b241fb3d1b3f363415249a52b893ad1b2d3a159', 5311350),
    'claims_pair_index': ('efb1fb78b4e09c3b05c932c0676da9f686c9e3c47fa7ee8346dc4700db1f7133', 1438330),
    'original_protocol': ('ca5602a6f8fcb852f5824bf6c4fd7e71074b280c35ba5705676806d17d593843', 33970),
}
PAIRS = [('T0', 'S0'), ('S0', 'SS0'), ('T0', 'T1'), ('S0', 'S1'), ('SS0', 'SS1')]
METRICS = ['provider_input_tokens', 'observable_ipc_bytes', 'wall_sec',
           'provider_wait_sec', 'local_state_materialized_bytes']
LABELS = {'provider_input_tokens': '提供方输入 token', 'observable_ipc_bytes': '运行时可观测 IPC 字节',
          'wall_sec': '任务时延', 'provider_wait_sec': 'SDK 等待',
          'local_state_materialized_bytes': '本地状态物化字节'}
UNITS = {'provider_input_tokens': 'token', 'observable_ipc_bytes': 'B',
         'wall_sec': 's', 'provider_wait_sec': 's', 'local_state_materialized_bytes': 'B'}
POINT_CI_FIELDS = ['status', 'planned_paired_units', 'measured_paired_units',
    'baseline_mean', 'target_mean', 'paired_difference', 'relative_change',
    'difference_ci95', 'relative_change_ci95', 'all_planned_unit_denominator_per_arm',
    'primary_population_unique_tasks', 'unique_tasks', 'chain_count']
SELECTION_BIAS = ('按执行后双方均成功筛选，失败和可能昂贵或有害的路径被排除；'
    '保留轮次及任务、链构成可能改变。此描述不能替代全部预定单元主分析，'
    '不能证明质量不劣、因果效率改进或完整账单节省。')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def decode(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite JSON constant')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def read(path):
    return decode(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')


def ids_sha(ids):
    return hashlib.sha256(json.dumps(sorted(set(ids)), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def valid_cost(value):
    # The frozen cost validity rule; no coercion or null/zero replacement.
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def raw_link(row):
    return {key: row.get(key) for key in ['unit_id', 'run_id', 'phase', 'task_id', 'chain_id',
        'arm', 'repeat', 'model', 'model_id', 'topology', 'processes', 'source_sha256',
        'task_sha256', 'raw_file', 'raw_line', 'raw_line_sha256', 'provider_attempts',
        'provider_usage_complete', 'provider_input_tokens', 'success']}


def display(value):
    return '未完成／未知' if value is None else str(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    require(args.inputs.is_absolute() and args.out.is_absolute(), 'absolute input/output paths required')
    require(not args.out.exists(), 'output must not exist; retain previous/partial outputs')
    require(args.out.parent.is_dir(), 'output parent must already exist')
    manifest = read(args.inputs)
    require(manifest.get('schema_version') == 'both-success-auxiliary-inputs-v1', 'wrong input manifest schema')
    require(manifest.get('study_id') == STUDY, 'wrong study')
    require(manifest.get('adapter_source_sha256') == digest(Path(__file__)), 'adapter source hash mismatch')
    require(set(manifest.get('files', {})) == set(EXPECTED), 'exact seven allowlisted inputs required')
    bound = {}; paths = {}
    for name, (expected_sha, expected_bytes) in EXPECTED.items():
        item = manifest['files'][name]
        path = Path(item['path'])
        pure = PurePosixPath(item['path'])
        require(path.is_absolute() and pure.parts[:2] == ('/', 'input') and '..' not in pure.parts,
                'inputs must be fixed local files below /input')
        require(item['sha256'] == expected_sha and item['bytes'] == expected_bytes, 'input pin changed')
        require(path.is_file() and path.stat().st_size == expected_bytes, 'input missing or size changed')
        require(digest(path) == expected_sha, 'input actual SHA mismatch')
        paths[name] = path
        bound[name] = {'path': str(path), 'sha256': expected_sha, 'bytes': expected_bytes}
    require(not args.out.is_relative_to(Path('/input')), 'outputs may not be inside inputs')
    primary = read(paths['primary']); contract = read(paths['contract']); plan = read(paths['formal_plan'])
    require(primary['study_id'] == STUDY and primary['formal_freeze_sha256'] == FREEZE_SHA, 'wrong primary authority')
    require(primary['statistical_contract_sha256'] == EXPECTED['contract'][0], 'wrong prospective contract')
    require(primary['primary_family_test_count'] == 10 and primary['active_confirmatory_families'] == 1,
            'primary family boundary changed')
    require(primary['quality_noninferiority_wording'] == 'WITHHELD_PROSPECTIVELY', 'quality wording changed')
    require(contract['formal_outcomes_used'] is False and contract['pairs'] == [list(p) for p in PAIRS],
            'pre-outcome pair authority changed')
    require(primary['input_sha256']['manifests/formal_plan.json'] == EXPECTED['formal_plan'][0], 'plan not primary-bound')
    planned = {r['unit_id']: r for r in plan}
    require(len(plan) == len(planned) == 6504, 'planned ID set changed')
    public_ids = {r['unit_id'] for r in plan if r['phase'] == 'public_calibration'}
    main_plan = [r for r in plan if r['phase'] == 'main']
    require(len(public_ids) == 144 and len(main_plan) == 2160, 'cohort sizes changed')
    formal_tasks = sorted({r['task_id'] for r in main_plan})
    state_tasks = contract['applicable_task_ids']
    require(len(formal_tasks) == 120 and len(state_tasks) == 108 and len(set(state_tasks)) == 108,
            'frozen task populations changed')
    require(set(state_tasks) < set(formal_tasks), 'state tasks outside formal main population')
    require(ids_sha(formal_tasks) == contract['formal_task_ids_sha256'], 'formal task ID digest changed')
    require(ids_sha(state_tasks) == contract['applicable_task_ids_sha256'] == primary['applicable_task_ids_sha256'],
            'state task ID digest changed')
    require(len({r['chain_id'] for r in main_plan}) == 20 and {r['repeat'] for r in main_plan} == {0, 1, 2},
            'main chain/repeat design changed')
    require({(r['model'], r['model_id'], r['topology'], r['processes']) for r in main_plan} ==
            {('main', 'qwen3.7-plus', 'runtime_uds', False)}, 'main model/topology changed')
    expected_keys = {(task, arm, repeat) for task in formal_tasks
        for arm in ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1') for repeat in (0, 1, 2)}
    require({(r['task_id'], r['arm'], r['repeat']) for r in main_plan} == expected_keys,
            'main planned key coverage changed')

    observations = {}; all_ids = set(); projection_hash = hashlib.sha256()
    with paths['observations'].open('rb') as stream:
        for line in stream:
            projection_hash.update(line)
            require(bool(line.strip()), 'blank audited record')
            row = decode(line)
            uid = row['unit_id']
            require(uid not in all_ids and uid in planned, 'duplicate/unplanned audited ID')
            all_ids.add(uid)
            require(row['schema_version'] == 'audited-observations-projection-v1', 'wrong observation schema')
            if row['phase'] != 'main':
                continue
            p = planned[uid]
            require(uid not in public_ids and p['phase'] == 'main', 'public entered main selector')
            require(all(row.get(k) == p.get(k) for k in ['task_id', 'chain_id', 'domain', 'arm', 'repeat',
                'model', 'model_id', 'topology', 'processes', 'run_id', 'unit_id', 'task_sha256']),
                'main observed/planned identity mismatch')
            require(row.get('source_sha256') == SOURCE_SHA and type(row.get('success')) is bool,
                    'main source/success metadata invalid')
            require(row.get('raw_file') == 'task_runs.jsonl' and type(row.get('raw_line')) is int and
                    isinstance(row.get('raw_line_sha256'), str) and len(row['raw_line_sha256']) == 64,
                    'raw locator missing')
            # Keep only required scalar identity/cost/link metadata, never message bodies.
            safe = raw_link(row)
            safe['domain'] = row['domain']
            observations[(row['task_id'], row['arm'], row['repeat'])] = safe
    require(projection_hash.hexdigest() == EXPECTED['observations'][0], 'observations changed while read')
    require(all_ids == set(planned) and len(observations) == 2160 and set(observations) == expected_keys,
            'audited full/main ID coverage changed')

    spec = importlib.util.spec_from_file_location('_frozen_both_success_auxiliary_kernel', paths['kernel'])
    require(spec is not None and spec.loader is not None, 'kernel loader unavailable')
    kernel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kernel)
    # No analyze/bootstrap/signflip/holm entry is called, and no kernel globals change.
    summaries = []; selected_pairs = []; all_planned = []; comparisons = primary['strata'][0]['comparisons']
    require(len(comparisons) == 5, 'five comparisons required')
    for ci, comparison in enumerate(comparisons):
        baseline, target = PAIRS[ci]
        require((comparison['baseline'], comparison['target']) == (baseline, target), 'pair order changed')
        task_distribution = comparison['metrics']['observable_ipc_bytes']['task_pair_distribution']
        task_ids = [r['task_id'] for r in task_distribution]
        expected_tasks = state_tasks if ci == 1 else formal_tasks
        require(set(task_ids) == set(expected_tasks) and len(task_ids) == len(expected_tasks), 'pair task population changed')
        require(all(r['repeat_count'] == 3 for r in task_distribution), 'pair repeats changed')
        require(comparison['planned_paired_units'] == len(expected_tasks) * 3, 'pair planned count changed')
        for value in [comparison['quality'], *comparison['metrics'].values()]:
            if 'task_pair_distribution' in value:
                require({r['task_id'] for r in value['task_pair_distribution']} == set(expected_tasks),
                        'saved endpoint population differs')
        paired = []
        for task in task_ids:
            for repeat in (0, 1, 2):
                a = observations[(task, baseline, repeat)]; b = observations[(task, target, repeat)]
                require(all(a[k] == b[k] for k in ['task_id', 'chain_id', 'domain', 'repeat', 'model',
                    'model_id', 'topology', 'processes', 'task_sha256', 'source_sha256']), 'pair key mismatch')
                paired.append({'domain': a['domain'], 'chain_id': a['chain_id'], 'task_id': task,
                    'repeat': repeat, 'a': a, 'b': b})
        both = [p for p in paired if p['a']['success'] is True and p['b']['success'] is True]
        selected_tasks = sorted({p['task_id'] for p in both})
        composition_checks = []
        for metric in METRICS[1:]:
            saved = comparison['metrics'][metric]
            old = saved['both_success_auxiliary']
            require(saved['both_success_paired_units'] == old['paired_units'] == len(both),
                    'old auxiliary both-success count mismatch')
            require(old.get('unique_tasks', 0) == len(selected_tasks), 'old auxiliary selected-task count mismatch')
            composition_checks.append({'metric': metric, 'paired_units_and_unique_tasks_match': True,
                'old_selected_run_id_list_not_saved': True,
                'composition_authority': 'same frozen task population, exact paired identities and frozen both-success predicate; old JSON stores counts rather than selected ID lists'})
        pair_id = baseline + '_' + target
        paired_locator = [{'task_id': p['task_id'], 'domain': p['domain'], 'chain_id': p['chain_id'],
            'repeat': p['repeat'], 'baseline': raw_link(p['a']), 'target': raw_link(p['b'])} for p in both]
        pair_record = {'pair_id': pair_id, 'primary_pointer': '/strata/0/comparisons/' + str(ci),
            'population': comparison['population'], 'planned_paired_units': len(paired),
            'both_success_paired_units': len(both), 'selected_task_ids': selected_tasks,
            'selected_task_repeat_keys': [{'task_id': p['task_id'], 'repeat': p['repeat']} for p in both],
            'all_selected_run_id_raw_links': paired_locator, 'old_twenty_auxiliary_composition_checks': composition_checks}
        selected_pairs.append(pair_record)
        for metric in METRICS:
            saved = comparison['metrics'][metric]
            pointer = '/strata/0/comparisons/' + str(ci) + '/metrics/' + metric
            points = {k: copy.deepcopy(saved[k]) for k in POINT_CI_FIELDS if k in saved}
            all_planned.append({'pair_id': pair_id, 'metric': metric, 'source_pointer': pointer,
                'present_fields': sorted(points), 'saved_original_points_and_ci': points})
            if metric == 'provider_input_tokens':
                require(saved['status'] == 'INCOMPLETE' and 'both_success_auxiliary' not in saved,
                        'token authority changed; no replacement of existing auxiliary allowed')
                unknown = [p for p in both if not all(
                    valid_cost(p[arm].get(metric)) and p[arm].get('provider_usage_complete') is True
                    and type(p[arm].get('provider_attempts')) is int and p[arm]['provider_attempts'] > 0
                    for arm in ('a', 'b'))]
                common = {'paired_units': len(both), 'unique_tasks': len(selected_tasks),
                    'baseline_mean': None, 'target_mean': None, 'difference': None, 'relative_change': None,
                    'selection_bias': SELECTION_BIAS}
                task_values = []
                if not both:
                    auxiliary = {'status': 'NO_BOTH_SUCCESS_PAIRS', **common}
                elif unknown:
                    auxiliary = {'status': 'INCOMPLETE_BOTH_SUCCESS_TOKEN_USAGE', **common}
                else:
                    _, task_values = kernel._chain_arrays(both, metric)
                    require({v['task_id'] for v in task_values} == set(selected_tasks), 'kernel changed selected tasks')
                    require(sum(v['repeat_count'] for v in task_values) == len(both), 'kernel changed selected repeats')
                    ba = float(kernel.np.mean([v['a'] for v in task_values]))
                    bb = float(kernel.np.mean([v['b'] for v in task_values]))
                    auxiliary = {'status': 'EXPLORATORY_SELECTION_ON_POST_EXECUTION_SUCCESS', **common,
                        'baseline_mean': ba, 'target_mean': bb, 'difference': bb - ba,
                        'relative_change': (bb - ba) / ba if ba else None}
                pair_record['new_token_task_values'] = task_values
                pair_record['unknown_token_both_success_pairs'] = len(unknown)
                pair_record['token_validity_rule'] = 'frozen finite nonnegative recorded cost check plus explicit complete provider usage and positive recorded provider attempts over the entire both-success subset; uncalibrated no-attempt zero is unknown; any unknown makes every auxiliary point null, no extra available-case selection'
                origin = 'NEW_DESCRIPTIVE_TOKEN_SUPPLEMENT_FROZEN_CHAIN_ARRAYS_ONLY'
                source_pointer = pointer + ' (original INCOMPLETE; no saved auxiliary)'
            else:
                auxiliary = copy.deepcopy(saved['both_success_auxiliary'])
                origin = 'VERBATIM_SAVED_AUXILIARY_DICT_PROJECTION'
                source_pointer = pointer + '/both_success_auxiliary'
            main_difference = saved.get('paired_difference')
            aux_difference = auxiliary.get('difference')
            delta = (aux_difference - main_difference
                if metric != 'provider_input_tokens' and isinstance(aux_difference, (int, float))
                and math.isfinite(aux_difference) and isinstance(main_difference, (int, float))
                and math.isfinite(main_difference) else None)
            # Delta is descriptive arithmetic on already saved points, never a new test/CI.
            summaries.append({'pair_id': pair_id, 'baseline': baseline, 'target': target,
                'metric': metric, 'metric_zh': LABELS[metric], 'unit': UNITS[metric],
                'population': comparison['population'], 'planned_paired_units': len(paired),
                'both_success_paired_units': len(both), 'both_success_unique_tasks': len(selected_tasks),
                'all_planned_status': saved['status'], 'all_planned_baseline_mean': saved.get('baseline_mean'),
                'all_planned_target_mean': saved.get('target_mean'), 'all_planned_difference': main_difference,
                'all_planned_relative_change': saved.get('relative_change'),
                'all_planned_difference_ci95': copy.deepcopy(saved.get('difference_ci95')),
                'all_planned_relative_change_ci95': copy.deepcopy(saved.get('relative_change_ci95')),
                'auxiliary_origin': origin, 'auxiliary_source_pointer': source_pointer,
                'auxiliary': auxiliary, 'auxiliary_minus_all_planned_difference': delta,
                'difference_comparison_note': ('主 token 终点仍未完成，无全预定点估计，主辅差为 null；不得用已测子集替代。'
                    if metric == 'provider_input_tokens' else '仅为已保存辅助差减去已保存主差的描述性算术；没有新区间或检验。'),
                'selection_bias': SELECTION_BIAS})
    require(len(summaries) == 25 and len(selected_pairs) == 5, 'output row coverage changed')
    require(sum(s['auxiliary_origin'] == 'VERBATIM_SAVED_AUXILIARY_DICT_PROJECTION' for s in summaries) == 20,
            'saved projection count changed')
    # Confirm small sources and the streamed audited input remained the pinned bytes.
    for name, path in paths.items():
        if name != 'observations':
            require(digest(path) == EXPECTED[name][0], 'input changed during supplement')
    result = {'schema_version': 'both-success-descriptive-auxiliary-v1',
        'status': 'DESCRIPTIVE_PROTOCOL_6_5_APPENDIX_NOT_PRIMARY_OR_B8_IDENTITY_EXTENSION',
        'study_id': STUDY, 'adapter_source_sha256': digest(Path(__file__)),
        'input_manifest_sha256': digest(args.inputs), 'bound_inputs': bound,
        'protocol_reference': {'file_key': 'original_protocol', 'location': 'section6 item5 / line151',
            'requirement': '全部预定主分析之外，给双方均成功成本辅助并说明差异和选择偏差。'},
        'method': {'token_auxiliary': 'frozen kernel _chain_arrays then frozen kernel np.mean; first average selected repeats per task, then equally average selected task values',
            'saved_other_auxiliary': 'twenty dicts projected verbatim with original JSON pointers; their costs not recomputed',
            'success_selection': 'both recorded success fields are exactly true; frozen pair populations and all selected repeats retained',
            'token_unknown_policy': 'any invalid/unknown token in the entire both-success subset yields INCOMPLETE and all point estimates null',
            'claims_pair_index_binding': 'exact existing1764-row metadata locator SHA bound, not parsed or rewritten; pair populations independently matched to frozen contract/primary distribution',
            'units': UNITS, 'kernel_sha256': EXPECTED['kernel'][0], 'numpy_version': kernel.np.__version__},
        'coverage': {'all_observed_ids': 6504, 'public_excluded_from_auxiliary': 144, 'main_planned_units': 2160,
            'main_tasks': 120, 'main_chains': 20, 'main_repeats': [0, 1, 2], 'state_frozen_tasks': 108,
            'model_id': 'qwen3.7-plus', 'topology': 'runtime_uds', 'processes': False,
            'summary_rows': 25, 'new_token_descriptive_rows': 5, 'saved_auxiliary_projection_rows': 20},
        'all_planned_points_and_ci_verbatim': all_planned, 'rows': summaries, 'selected_pair_support': selected_pairs,
        'primary_input_tokens_still_INCOMPLETE': True, 'quality_noninferiority_wording': 'WITHHELD_PROSPECTIVELY',
        'new_CI_p_Holm_or_hypothesis_family': False, 'primary_source_modified': False,
        'runtime_provider_raw_request_body_or_secret_read': False,
        'within_existing_B8_241_record_identity': False,
        'limits': [SELECTION_BIAS, '这是原协议辅助交付补齐，不恢复主 token 终点、不设新检验族。',
            '原主统计、冻结源码、B8既有241项数值身份与原图均不改；新补表须单独审查及两次实际字节比较。',
            '旧辅助未保存逐run选择名单；这里只核相同冻结任务人群、相同成功谓词及旧paired_units/unique_tasks，明确不伪称逐run旧名单已保存。',
            '原始行链接来自既有审计投影，没有打开或重新哈希task_runs/provider/model_io请求正文。',
            '双方成功是执行后选择；该选择不提供质量不劣、因果效率或完整提供方账单证明。']}
    args.out.mkdir(exist_ok=False)
    write_json(args.out / 'results.json', result)
    csv_fields = ['pair_id', 'metric', 'metric_zh', 'unit', 'population', 'planned_paired_units',
        'both_success_paired_units', 'both_success_unique_tasks', 'all_planned_status',
        'all_planned_baseline_mean', 'all_planned_target_mean', 'all_planned_difference',
        'all_planned_relative_change', 'all_planned_difference_ci95', 'all_planned_relative_change_ci95',
        'auxiliary_status', 'auxiliary_baseline_mean', 'auxiliary_target_mean', 'auxiliary_difference',
        'auxiliary_relative_change', 'auxiliary_minus_all_planned_difference', 'auxiliary_origin',
        'auxiliary_source_pointer', 'difference_comparison_note', 'selection_bias']
    with (args.out / 'summary.csv').open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields, lineterminator='\n')
        writer.writeheader()
        for row in summaries:
            flat = dict(row)
            for key in ['status', 'baseline_mean', 'target_mean', 'difference', 'relative_change']:
                flat['auxiliary_' + key] = row['auxiliary'].get(key)
            writer.writerow({key: canonical(flat.get(key)) for key in csv_fields})
    headers = ['配对', '指标（单位）', '全预定配对数', '双方成功配对数／任务数', '主差', '双方成功辅助差', '辅助差减主差']
    table = [[row['pair_id'].replace('_', '→'), LABELS[row['metric']] + '（' + UNITS[row['metric']] + '）',
        str(row['planned_paired_units']), str(row['both_success_paired_units']) + '／' + str(row['both_success_unique_tasks']),
        display(row['all_planned_difference']), display(row['auxiliary'].get('difference')),
        display(row['auxiliary_minus_all_planned_difference'])] for row in summaries]
    paragraphs = ['本补充按原实验方案第六节第五点，呈现双方均成功的成本辅助分析。主分析人群和全部失败记录保持不变。',
        '五个输入 token 主终点仍未完成，质量不劣措辞仍被事先禁止。辅助分析只有描述性点值，没有新区间、p值或多重校正。',
        '五个 token 辅助按冻结函数先在每个任务内平均保留轮次，再对保留任务等权平均。另二十行直接复制已保存辅助结果。',
        'S0→SS0沿用事前冻结的一百零八个状态适用任务，其余四配对沿用一百二十任务；主模型为qwen3.7-plus，拓扑为runtime_uds，三个预定轮次不扩独立样本。',
        SELECTION_BIAS, '主差和原区间在结果文件中单独按原值保存。主 token 无全预定点估计，不能计算主辅差；其余主辅差仅为已保存点值的描述性相减。',
        '本补表不纳入既有B8二百四十一项重建身份，不改变原始主统计、图表、原审计UNKNOWN或检索缺失。']
    markdown = ['# 双方均成功成本辅助分析：原方案第六节第五点补充', '']
    for paragraph in paragraphs:
        markdown.extend([paragraph, ''])
    markdown.extend(['|' + '|'.join(headers) + '|', '|' + '|'.join(['---'] * len(headers)) + '|'])
    markdown.extend('|' + '|'.join(cells) + '|' for cells in table)
    (args.out / 'append_summary.md').write_text('\n'.join(markdown) + '\n', encoding='utf-8')
    xml = ['<h1>双方均成功成本辅助分析：原方案第六节第五点补充</h1>']
    xml.extend('<p>' + escape(p) + '</p>' for p in paragraphs)
    xml.append('<table>')
    for cells in [headers, *table]:
        xml.append('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in cells) + '</tr>')
    xml.append('</table>')
    (args.out / 'feishu.xml').write_text('\n'.join(xml) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'summary_rows': 25,
        'new_token_descriptive_rows': 5, 'saved_auxiliary_projection_rows': 20}, ensure_ascii=False))


if __name__ == '__main__':
    main()
