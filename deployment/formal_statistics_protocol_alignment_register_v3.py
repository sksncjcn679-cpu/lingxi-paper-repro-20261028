"""AST/hash registration only; never imports statistics or reads formal outcomes."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json

OUT = Path('/evidence/formal_20261019')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    source = Path('/drivers/formal_statistics_protocol_aligned_v3.py')
    tests = Path('/drivers/formal_statistics_protocol_aligned_v3_test.py')
    for path in (source, tests):
        ast.parse(path.read_text(),filename=str(path))
    metadata = OUT/'analysis/stats/statistical_plan_protocol_alignment_v3.json'
    contract = json.loads(metadata.read_text())
    if digest('/drivers/formal_statistics.py') != contract['frozen_statistics_sha256']:
        raise RuntimeError('old frozen statistics changed')
    report = {'created_utc':datetime.now(timezone.utc).isoformat(),'status':'AST_VALIDATED_IMPLEMENTATION_REGISTERED',
        'statistical_computation_executed':False,'synthetic_tests_executed':False,
        'formal_raw_files_read':[],'formal_effects_inspected':False,'frozen_files_modified':False,
        'implementation':str(source),'implementation_sha256':digest(source),
        'prepared_synthetic_tests':str(tests),'prepared_tests_sha256':digest(tests),
        'population_registration_sha256':digest(metadata),
        'frozen_statistics_sha256':contract['frozen_statistics_sha256'],
        'api':'analyze(runs, plan, applicable_task_ids, seed=20261019)',
        'return_primary':'same top-level strata/comparisons/metrics; S0_SS0 108, others120; single combined10Holm',
        'return_archive':'all120_frozen_replay_archive retains old numbers, archive_only',
        'return_deployment':'full120_state_deployment_descriptive is list[{model,topology,comparison}] without p_holm',
        'return_subset':'state_applicable_descriptive has strata/comparisons, descriptive raw p only, no second family'}
    path = OUT/'analysis/stats/statistical_implementation_protocol_alignment_v3.json'
    with path.open('x') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    document = '''Material Passport: ORIGINAL-PROTOCOL IMPLEMENTATION CORRECTION; effect-blind during collection; no original frozen file changed.

纠正冻结分析调用遗漏原方案第4.1节“NOT_APPLICABLE不纳入状态增量估计”的限制。研究方案、任务与模型均不改变。唯一确认性10检验家族为：S0↔SS0两个主要终点采用事先冻结的108任务，其余四对八个主要终点采用120任务，十个原始p值一起做一次Holm。

新独立实现 formal_statistics_protocol_aligned_v3.py 调用原冻结统计模块，其源码SHA固定验证，bootstrap、配对符号翻转、保守质量界及所有随机seed偏移保持原方法。108总体仅由冻结标签决定，包含路径失败及未知成本，禁止按实际消费或成功筛选。

全120的冻结实现重放完整保留所有数值，包括原p_holm；这些仅为archive_only，不能与新家族择优或并列作为第二确认家族。全120的S0↔SS0另作为部署策略描述，108子集其他比较也只报描述性p_raw，不另给Holm家族。质量不劣措辞继续放弃。

补充登记发生在原冻结后的数据收集期间、查看正式汇总效应之前；不能宣称这是原冻结代码已经实现的处理。本次仅AST与源码SHA检查，尚未运行统计计算或synthetic测试；测试代码已准备，待真实计时结束后执行。
'''
    with (OUT/'analysis/stats/statistical_plan_protocol_alignment_v3.md').open('x') as stream:
        stream.write(document)
    print(json.dumps({'status':report['status'],'implementation_sha256':report['implementation_sha256'],
                      'tests_executed':False,'formal_effects_read':False}))


if __name__ == '__main__':
    main()
