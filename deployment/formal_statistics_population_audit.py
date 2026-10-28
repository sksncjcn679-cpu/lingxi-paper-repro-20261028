"""Outcome-blind design audit of state applicability; metadata only, no raw reads."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

OUT = Path('/evidence/formal_20261019')
STATS = OUT / 'analysis/stats'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    freeze_path = OUT / 'manifests/formal-freeze.json'
    freeze = json.loads(freeze_path.read_text())
    inputs = [OUT / 'manifests/original_protocol.md', OUT / 'protocol.yaml',
              STATS / 'statistical_plan_v2.json', STATS / 'statistical_plan_addendum_unequal_chain_v2.json',
              Path('/drivers/formal_statistics.py'), Path('/drivers/formal_report.py'), freeze_path]
    hashes = {str(path): digest(path) for path in inputs}
    verified = {str(path): hashes[str(path)] == freeze['files'][str(path)]
                for path in inputs if str(path) in freeze['files']}
    if not all(verified.values()):
        raise RuntimeError('frozen source/metadata identity mismatch')
    original = (OUT / 'manifests/original_protocol.md').read_text()
    required = ['不纳入状态增量估计', '主分析按可用状态任务子集和全体任务分别报告', '10 个确认性检验']
    if not all(fragment in original for fragment in required):
        raise RuntimeError('original protocol source requirement not found')
    pairs = [('T0','S0',120), ('S0','SS0',108), ('T0','T1',120), ('S0','S1',120), ('SS0','SS1',120)]
    result = {'status': 'OUTCOME_BLIND_DESIGN_AUDIT_RECOMMENDATION',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'formal_raw_files_read': [], 'formal_effect_estimates_read': False,
        'frozen_files_changed': False, 'statistical_recomputation_performed': False,
        'original_protocol_authority': 'original_protocol.md sections 4.1 and 6.2',
        'frozen_main_implementation': 'analyze(all 120 main tasks) constructs all five contrasts and its ten-test Holm family from that same supplied population',
        'frozen_metadata_pair_population_map': 'NOT_EXPLICIT; statistical_plan_v2 lists 120 independent tasks plus separately predeclared 108 state-applicable subset, no per-pair confirmatory population map',
        'frozen_report_source': '/drivers/formal_report.py' in freeze['files'],
        'report_current_policy': 'state-applicable subset described as exploratory, full main family described as confirmatory; conflicts with original instruction if interpreted as state-mechanism confirmation',
        'recommended_state_primary_estimand': 'S0 vs SS0 among frozen pre-treatment state_spec.applicable=true tasks; all planned repeats and failures retained',
        'recommended_full_120_state_interpretation': 'descriptive overall deployment-strategy contrast with 12 NOT_APPLICABLE cases; not pure state-exchange increment',
        'recommended_single_active_holm_family': [{'baseline':a,'target':b,'unique_tasks':n,
             'chains':20,'repeats':3,'planned_paired_units_per_arm':n*3,
             'endpoints':['provider_input_tokens','observable_ipc_bytes']} for a,b,n in pairs],
        'active_confirmatory_test_count': 10,
        'implementation_disposition': 'retain immutable all120 frozen implementation replay, including its computed old p_holm, but mark that replay archival/nonconfirmatory after a documented correction; do not choose between two families',
        'correction_timing_disclosure': 'during data collection after original freeze, before inspecting formal aggregate effects; a transparent original-protocol correction, not a claim that implementation was originally identical',
        'forbidden': ['choose cohort by success/state consumption/observed effect',
            'exclude state-path failures from the 108 planned applicability population',
            'create a second active ten-test family for the 108 subset',
            'select the more significant p_holm between corrected and original replay',
            'change frozen source files or delete all120 reproduction outputs'],
        'recommended_next_step': 'effect-blind addendum plus independent v2 analysis wrapper; leave frozen formal_statistics.py and old outputs unchanged; compute eight primary p-values on 120 and two state p-values on 108, apply Holm once jointly to those ten',
        'input_hashes': hashes, 'frozen_hashes_verified': verified}
    with (STATS / 'state_increment_population_audit_v1.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    document = '''Material Passport: ORIGINAL-PROTOCOL DESIGN AUDIT; metadata only; no formal raw outcomes or effect estimates read; no frozen file changed.

按原方案，S0↔SS0 的“状态交换增量”应以冻结的 108 个状态适用任务为主要估计总体。120 个任务的同组对比仍需完整报告，但只能解释为含 12 个无状态任务的整体部署策略差异，不能把其中 NOT_APPLICABLE 案例计入纯状态增量。

依据：原方案第 4.1 节（原文第 83 行）明确写明“标记 `NOT_APPLICABLE`，不纳入状态增量估计”，随后要求“主分析按可用状态任务子集和全体任务分别报告”。第 6.2 节（第 148 行）规定五对×两个主要成本终点共同组成一个 10 检验 Holm 族；这没有要求各对的估计总体必须相同。第 6.5 节的“全部预定单元”是在相应预定估计总体中保留所有失败，而非把结构上不适用的任务重新纳入状态效应。

建议唯一确认性家族如下，五对名称和两个终点均不改变：

| 配对 | 预定任务总体 | 独立任务／链 | 每臂计划配对执行单元 | 主要成本终点 |
|---|---|---:|---:|---|
| T0↔S0 | 全体正式任务 | 120／20 | 360 | provider 输入 token、可观测 IPC 字节 |
| S0↔SS0 | state_spec.applicable=true | 108／20 | 324 | 同上 |
| T0↔T1 | 全体正式任务 | 120／20 | 360 | 同上 |
| S0↔S1 | 全体正式任务 | 120／20 | 360 | 同上 |
| SS0↔SS1 | 全体正式任务 | 120／20 | 360 | 同上 |

把其余四对的 8 个原始 p 值与 S0↔SS0 在 108 个任务上的 2 个原始 p 值合并，再做一次 Holm。不得分别给全体和状态子集各开一个有效的确认性家族，也不得在两个家族之间选择更有利的结果。记忆增量 SS0↔SS1 仍用全体 120，因为该对改变的是记忆；无状态任务不妨碍其记忆对比。

现有冻结实现确有需要披露的差异。冻结 statistical_plan_v2.json 第 39–41 行以 120 个任务、全部计划单元和主 phase 描述总体；第 53 行起另列 108 个预定状态任务，但没有逐配对的确认性总体映射。冻结 formal_statistics.py 的 analyze() 对传入的同一 plan 建立全部五对，并在第 273–280 行把这些对的主要成本 p 值统一校正。因此 analyze(main120) 的重现结果确定使用全体 120，这一事实不能改写。当前 formal_report.py 第 733 行还把状态子集称为探索性，并把全体家族称为主要家族；该报告脚本未列入原冻结文件清单，不能作为比原实验方案更高的协议授权。

冻结 protocol.yaml 第 6597 行要求全体与预定 108 子集分别报告，但没有明确覆盖原文的“不纳入状态增量”要求。此前“不等链长”补充只修质量保守界；其“主界数值不变”的声明不等于已经决定了 S0↔SS0 的确认性总体。此次应承认：原方案要求与已冻结分析调用方式存在偏差，需要显式纠错，不能宣称原来就完全一致。

建议处理：在查看任何正式汇总效应前，保存“数据收集期间、效果盲的原协议分析纠错”补充，列明上述唯一 10 检验家族；使用新增独立版本实现，保留冻结源、全 120 结果及其原有校正 p 值作为可追溯实现重放。旧家族应明确为归档重放，不再并列作为第二个可选择的确认性家族。108 子集只能按事先冻结的 applicability 标签筛选，不能按是否实际消费状态、是否成功或开销大小筛选；状态路径失败仍保留在 108 分母，未知成本保持 null/INCOMPLETE。域内聚类和 3 次重复处理保持原方法，不新增独立任务；不等链长保守界已经支持 108 子集。质量不劣措辞继续预先放弃。

本审计仅检查原协议、冻结清单及分析源代码；没有读取 raw/task_runs、provider_calls 或正式统计输出，没有运行真实效应分析，没有修改任何冻结文件。此文给出实现纠错建议，尚未替换任何分析实现。
'''
    with (STATS / 'state_increment_population_audit_v1.md').open('x') as stream:
        stream.write(document)
    print(json.dumps({'status': result['status'], 'formal_effects_read': False,
        'frozen_files_changed': False, 'recommended_family_size':10,'state_population':108}))


if __name__ == '__main__':
    main()
