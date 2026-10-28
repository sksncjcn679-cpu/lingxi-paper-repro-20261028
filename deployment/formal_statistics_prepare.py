"""Freeze prospective analysis before formal outcomes; use public pilot only."""
from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import json
import math

import numpy as np
from scipy.stats import beta
from formal_statistics import PAIRS, PRIMARY, BOOTSTRAPS, PERMUTATIONS

OUT = Path('/evidence/formal_20261019')
PILOT = OUT.parent / 'model_checks/qwen37_plus_20261019/raw/task_runs.jsonl'


def prospective():
    rows = [json.loads(line) for line in PILOT.read_text().splitlines()]
    tasks = sorted({r['task_id'] for r in rows})
    chains = sorted({r['chain_id'] for r in rows})
    arm_counts = {arm: {'units': sum(r['arm'] == arm for r in rows),
                       'successes': sum(r['arm'] == arm and r['success'] for r in rows)}
                  for arm in sorted({r['arm'] for r in rows})}
    task_loss = []
    for task in tasks:
        rr = [r for r in rows if r['task_id'] == task]
        task_loss.append(not all(r['success'] for r in rr))
    failure_upper = 1.0 if all(task_loss) else float(beta.ppf(.95, sum(task_loss) + 1, len(tasks) - sum(task_loss)))
    rng = np.random.default_rng(20261019)
    simulations = 10000
    sensitivity = []
    # Public pilot has all-pass and only three chains, so neither failure risk
    # nor ICC is identifiable. Explicit sensitivity replaces a fabricated ICC.
    for n_chains in (20, 40):
        per_domain = n_chains // 4
        best_lower = -(1 - (.05 / 4) ** (1 / per_domain))
        for failure in sorted(set([0.0, .01, .05, .10, failure_upper])):
            for icc in (0.0, .2, .5, .9):
                for true_difference in (0.0, -.02, -.05):
                    # Joint paired outcome categories: loss / gain / same.
                    # Equal baseline/target failure risk permits discordance
                    # f(1-f); a specified loss increment changes only target.
                    gain = failure * (1 - failure)
                    loss = min(1 - gain, gain - true_difference)
                    probabilities = np.asarray([loss, gain, 1 - loss - gain])
                    active = probabilities > 0
                    if icc == 0:
                        pp = np.broadcast_to(probabilities, (simulations, n_chains, 3))
                    else:
                        pp = np.zeros((simulations, n_chains, 3))
                        pp[..., active] = rng.dirichlet(probabilities[active] * (1 / icc - 1), size=(simulations, n_chains))
                    lost = rng.binomial(6, pp[..., 0])
                    remain = 6 - lost
                    cond_gain = np.divide(pp[..., 1], 1 - pp[..., 0], out=np.zeros_like(pp[..., 1]), where=pp[..., 0] < 1)
                    gained = rng.binomial(remain, np.clip(cond_gain, 0, 1))
                    chain_difference = (gained - lost) / 6
                    mean_difference = chain_difference.mean(axis=1)
                    any_loss_counts = (lost > 0).reshape(simulations, 4, per_domain).sum(axis=2)
                    upper = beta.ppf(1 - .05 / 4, any_loss_counts + 1, per_domain - any_loss_counts)
                    upper[any_loss_counts == per_domain] = 1.0
                    lower = -upper.mean(axis=1)
                    power = float(np.mean(lower > -.05))
                    sensitivity.append({'formal_chains': n_chains, 'tasks_per_chain': 6,
                        'repeats_not_independent': True, 'assumed_failure_rate': failure,
                        'assumed_within_chain_icc': icc, 'true_quality_difference': true_difference,
                        'simulations': simulations, 'mean_observed_difference': float(mean_difference.mean()),
                        'sd_observed_difference': float(mean_difference.std(ddof=1)),
                        'noninferiority_power': power,
                        'mc_power_upper95_if_zero': 1 - .05 ** (1 / simulations) if power == 0 else None,
                        'safety_lower_coverage': float(np.mean(lower <= true_difference)),
                        'best_possible_safety_lower': best_lower})
    return {'created_utc': datetime.now(timezone.utc).isoformat(), 'seed': 20261019,
        'source': str(PILOT), 'source_sha256': hashlib.sha256(PILOT.read_bytes()).hexdigest(),
        'public_pilot_units': len(rows), 'public_unique_tasks': len(tasks), 'public_chains': len(chains),
        'public_arm_counts': arm_counts, 'pilot_task_failure_point': float(np.mean(task_loss)),
        'pilot_task_binomial_failure_upper95_descriptive': failure_upper,
        'pilot_scope': 'historical public qwen3.7-plus trial with identical semantic prompts; not formal protocol representation contrast, not formal outcomes',
        'pilot_limitation': 'six public cases and all-pass cannot estimate ICC, discordance, or formal native-prompt success; upper limit treats tasks as independent only for sensitivity range, never confirmatory inference',
        'simulation_model': 'paired loss/gain/same probabilities with Dirichlet-multinomial within-chain dependence; six checkpoints per chain; domain-stratified conservative paired exact safety test',
        'minimum_effect_of_interest': .05, 'one_sided_alpha': .05,
        'decision': 'KEEP_120_FORMAL_TASKS_20_CHAINS_AND_WITHHOLD_NONINFERIORITY_WORDING',
        'authority': 'original protocol section 6.3 permits withholding quality noninferiority wording when power is insufficient',
        'no_formal_outcomes_used': True, 'sensitivity': sensitivity}


def main():
    stats = OUT / 'analysis/stats'
    stats.mkdir(exist_ok=True)
    power = prospective()
    with (stats / 'prospective_power.json').open('x') as f:
        json.dump(power, f, indent=2)
    with (stats / 'prospective_power.csv').open('x', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(power['sensitivity'][0]))
        writer.writeheader()
        writer.writerows(power['sensitivity'])
    contract = {'schema_version': 1, 'authority': 'original_protocol.md section 6',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'no_formal_outcomes_used': True,
        'pairs': PAIRS, 'primary_endpoints': PRIMARY, 'holm_family_size': 10,
        'bootstrap_draws': BOOTSTRAPS, 'permutation_draws_minimum': PERMUTATIONS,
        'sign_flip_exact_up_to_chains': 20, 'quality_risk_margin': -.05,
        'quality_method': 'domain-stratified paired chain bootstrap plus conservative paired chain-any-loss/gain binomial exact bound with Bonferroni simultaneous domain coverage',
        'noninferiority_claim': 'WITHHELD_PROSPECTIVELY',
        'independent_units': '120 unique tasks in 20 chains; repeats averaged within task; chains resampled within domain',
        'planned_denominator': 'all frozen planned units; missing quality is failure; unknown cost remains missing and confirmatory status INCOMPLETE',
        'multiple_models_topologies': 'separate strata, never pooled across topology or model; confirmatory family for frozen primary phase only',
        'cost_direction': 'target minus baseline; relative change=(target-baseline)/baseline; negative is reduction',
        'auxiliary_success_subset': 'exploratory only; both-success pairs, count and selection bias reported',
        'missing_zero_forbidden': True, 'source_files': {}}
    for name in ('formal_statistics.py', 'formal_statistics_prepare.py', 'formal_statistics_test.py'):
        path = Path('/drivers') / name
        contract['source_files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with (stats / 'statistical_plan.json').open('x') as f:
        json.dump(contract, f, indent=2)
    (stats / 'statistical_plan.md').write_text('''Material Passport: ORIGINAL PROTOCOL ANALYSIS PLAN; prospective; no formal outcomes used.

按原方案第 6 节冻结五个配对、两个主要成本终点及共同的 10 检验 Holm 族。每个任务先平均独立重复，再在域内重抽完整链 10,000 次。按链聚合后的双侧符号翻转在 20 链以内精确枚举，更多链至少 100,000 次。相对变化以基线均值为分母。所有缺失、超时和失败保留在计划质量分母；未知成本不补零，确认性结果保持 INCOMPLETE。

质量另给保守配对精确安全界：每条链任意检查点出现的配对新增失败，作为链内平均损失的上界；逐域 Clopper–Pearson 上界采用 Bonferroni 同时覆盖，按冻结任务数加权。该界允许链内任意相关和域异质性，忽略改善带来的抵消，因此保守；即使全通过也不会产生 [0,0] 的质量区间。域内各链检查点数必须一致。

公开 qwen3.7-plus 试运行只有 6 个任务且全部成功；其 ICC 和真实失配概率不可识别，且历史统一语义提示词不能证明正式表示差异的质量。故在失败率、链内相关性和 0/−2/−5 pp 效应的预注册敏感性网格上模拟。20 链乃至 40 链在当前保守安全界下不能排除 −5 pp；按原方案允许的分支保留 120 个正式任务，正式开始前即放弃“质量不劣”措辞，只报告观察差异、区间和适用范围。不得在正式结果后切换到较有利的质量检验。
''')
    print(json.dumps({'status': 'FROZEN_PROSPECTIVE_ANALYSIS', 'sensitivity_cells': len(power['sensitivity']),
                      'decision': power['decision'], 'formal_outcomes_used': False}))


if __name__ == '__main__':
    main()
