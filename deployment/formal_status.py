"""Rebuild transparent completion metadata from the frozen plan and append-only units."""
from pathlib import Path
from collections import Counter
import json
from formal_recording import now

OUT=Path('/evidence/formal_20261019')


def main():
    plan=json.loads((OUT/'manifests/formal_plan.json').read_text())
    path=OUT/'raw/task_runs.jsonl'
    rows=[json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
    formal=[r for r in rows if r['phase']!='public_calibration']
    counts=Counter(r['phase'] for r in rows)
    expected=Counter(u['phase'] for u in plan)
    unique={r['run_id'] for r in rows}
    duplicate_count=len(rows)-len(unique)
    states={phase:{'planned':n,'terminal':counts[phase],
        'success':sum(bool(r['success']) for r in rows if r['phase']==phase),
        'complete':counts[phase]==n and {r['run_id'] for r in rows if r['phase']==phase}==
                   {u['run_id'] for u in plan if u['phase']==phase}} for phase,n in expected.items()}
    all_real_complete=not duplicate_count and all(s['complete'] for s in states.values())
    pressure_path=OUT/'analysis/stress_matrix_audit.json'
    pressure=json.loads(pressure_path.read_text()) if pressure_path.exists() else {}
    continuous=pressure.get('continuous_status') or {}
    postrun_paths=sorted((OUT/'analysis').glob('postrun_verification_*/manifest.json'),key=lambda p:p.stat().st_mtime_ns)
    postrun=json.loads(postrun_paths[-1].read_text()) if postrun_paths else {}
    manifest_path=OUT/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    manifest.update(updated_utc=now(),status='REAL_MATRIX_TERMINAL_REQUIRES_FINAL_AUDIT' if all_real_complete else 'FORMAL_EXECUTION_IN_PROGRESS' if formal else 'PRE_FORMAL_VALIDATION',
        formal_outcomes_collected=bool(formal),formal_terminal_units=len(formal),
        matrix_completion=states,duplicate_terminal_rows=duplicate_count,
        budget={'limits':'manifests/budget_limits.json','estimate':'manifests/preformal_budget_estimate.json',
                'authorization':'full protocol matrix; finite request/token limits; no monetary cap inferred',
                'actual_currency_cost':'UNKNOWN'},
        human_review='USER_AUTHORIZED_AUTOMATIC_VALIDATION_NO_HUMAN_RATINGS',
        measurement_audit='PUBLIC_REAL_AND_MULTIPROCESS_DRY_PASS; QUIESCENT_OVERHEAD_RECORDED',
        corpus_freeze='FULL_PROTOCOL_FROZEN' if (OUT/'manifests/formal-freeze.json').exists() else 'AUTOMATIC_VALIDATED_PENDING_FULL_PROTOCOL_FREEZE',
        quality_noninferiority='WITHHELD_PROSPECTIVELY; PAIRED_DIFFERENCE_AND_CONSERVATIVE_CI_ONLY',
        first_protocol_microbenchmark='v3: 60000 measured rows, 3 independent producer and consumer batches; prior drafts retained',
        pressure_completion_metadata={'recorded_tasks':pressure.get('recorded_pressure_tasks',0),
            'complete_cells':pressure.get('pressure_cells_complete',0),'planned_tasks':58590,'planned_cells':30,
            'continuous_status':continuous.get('status','PENDING'),'continuous_rounds':continuous.get('rounds',0),
            'scope':'derived summary only; separate streaming completion gate is authoritative'},
        postrun_verification={'status':postrun.get('status','NOT_STARTED'),'manifest':str(postrun_paths[-1].relative_to(OUT)) if postrun_paths else None},
        full_paper_delivery='NOT_CERTIFIED_REQUIRES_PROTOCOL_AND_NUMERIC_VISUAL_REPRODUCTION_AUDIT')
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    lines=['# 正式实验状态','',f'更新时间：{manifest["updated_utc"]}',
        '',f'正式执行终态：{len(formal)} / 6360（主实验 2160；其他跨模型、进程、框架、消融和记忆条件另列）。',
        '公开校准144个执行单元单独保存，不进入正式估计。',
        '', '| 阶段 | 计划 | 已记录终态 | 通过 |','|---|---:|---:|---:|']
    for phase,state in states.items():
        lines.append(f'| {phase} | {state["planned"]} | {state["terminal"]} | {state["success"]} |')
    lines += ['', '用户唯一授权的协议变更：采用自动验证，取消人工评分和双人复核；不生成虚构人工分数或一致性指标。',
        '', '六组保持不同的原生精简文本/结构化/引用表示，业务事实完整且等价；历史相同提示词排查数据全部排除。',
        '', '执行环境：openEuler 24.03 SP3 用户空间，Docker/WSL2内核，2 CPU配额、2 GiB；不能称原生openEuler内核。',
        '', '独立计量审计：公开真实校准144次调用通过；3个质量失败保留。进程干跑24个单元通过并记录真实消费者PID与计算。',
        '计量开销已经单独测量，不从正式时延扣除。前瞻放弃“质量不劣”措辞，报告效应和不确定性。',
        '', '离线检索完整14,400查询、协议60,000、状态25,200、量化12,800、状态复用22,500、六故障180次已记录。',
        f'完整压力派生摘要：{pressure.get("recorded_pressure_tasks",0)} / 58,590；完整档位 {pressure.get("pressure_cells_complete",0)} / 30；连续轮次 {continuous.get("rounds",0)} / 100（{continuous.get("status","PENDING")}）。是否完整由独立原始记录门禁核实。',
        f'后续串行验证：{postrun.get("status","NOT_STARTED")}；统计测试、计量审计、导出与成图结果须逐步核对。',
        '故障边界：NaN状态损坏可静默改变排序；错误程序性记忆被采用。确定性fixture成功不能证明真实模型鲁棒性或因果收益。',
        '', '真实usage、可观测socket字节、本地物化字节和逻辑字节分别核算。所有失败保留，未知成本不补零。',
        '', '当前尚未满足全部论文交付门禁，不能声称完整实验已完成。']
    (OUT/'STATUS.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'formal_terminal':len(formal),'main_terminal':counts['main'],'duplicate_terminals':duplicate_count}))


if __name__=='__main__':main()
