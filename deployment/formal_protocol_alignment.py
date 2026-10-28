"""Record strict adherence and correction of the pilot-only prompt condition before formal outcomes."""
from pathlib import Path
import csv
import hashlib
import json
import shutil

OUT=Path('/evidence/formal_20261019')

def main():
    history=OUT/'manifests/preparation_versions'
    history.mkdir(exist_ok=True)
    for rel in ['manifest.json','analysis/abstract_metric_contract.json','preflight_report.md']:
        p=OUT/rel
        shutil.copy2(p,history/p.name)
    manifest=json.loads((OUT/'manifest.json').read_text())
    manifest.update(human_review='REQUIRED_TWO_REVIEWERS_PENDING',
        prompt_design='protocol section 4.1: lean text versus structured representations, same complete business facts; no identical-prompt main design',
        protocol_authority='manifests/original_protocol.md; user explicitly requires no design additions or substitutions',
        first_protocol_microbenchmark='60000 raw measurements saved; full measurement coverage audit pending')
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    contract=json.loads((OUT/'analysis/abstract_metric_contract.json').read_text())
    contract['provider_input_token_change']['note']='真实模型读取各组对应表示，事实完整等价；实际 provider usage 计量。相同提示词历史排查批次不进入正式对照。'
    contract['prompt_equivalence']={'required':'same facts, tasks, role graph and evaluator; representation differs',
        'main':'existing lean full-payload text versus structured/reference handoff with verified readable fact resolution',
        'forbidden':'identical model prompts as the main experiment, duplicate baseline facts, missing facts in reference arms',
        'calibration_gate':'per-group accessible fact inventory and actual prompt coverage before outcome collection'}
    (OUT/'analysis/abstract_metric_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2))
    amendment={'type':'preparation correction, not research protocol amendment',
        'user_instruction':'完全按照论文实验方案，不加自己的东西',
        'correction':'identical semantic prompts were introduced for historical diagnosis only; excluded from formal main design',
        'formal_provider_calls_before_correction':0,
        'human_requirements':'two task/query reviewers and independent blinded output ratings retained; no automatic substitute',
        'protocol_sha256':hashlib.sha256((OUT/'manifests/original_protocol.md').read_bytes()).hexdigest()}
    (OUT/'manifests/protocol_alignment.json').write_text(json.dumps(amendment,ensure_ascii=False,indent=2))
    requirements=[
        ('2.1','源码身份、差异和环境冻结','IN_PROGRESS'),('2.2','完整预检 stdout/stderr/退出码','IN_PROGRESS'),
        ('3.1','144 案例，24 链；120 正式、24 公开试运行','CANDIDATES_CREATED'),
        ('3.1','至少90个状态任务，载荷分层并真实消费','METADATA_ONLY_NOT_VERIFIED'),
        ('3.1','双人任务审核与至少120个输出的独立盲评','REQUIRED_PENDING'),
        ('3.2','240 query，域均衡、无答案≥60、跨语言/过期/冲突/恶意','CANDIDATES_IN_PROGRESS'),
        ('3.2','双人检索标签与裁决','REQUIRED_PENDING'),
        ('4.1','六组表示差异，完整等价事实、同图同验证器','CALIBRATION_PENDING'),
        ('4.1','主实验120×6×3=2160','NOT_STARTED'),('4.1','跨模型60×6×2=720','NOT_STARTED'),
        ('4.1','独立进程60×3×2=360及真实消费者','NOT_STARTED'),
        ('4.1','AutoGen/LangGraph各60×2，等价与计量审计','DEPENDENCIES_PINNED_ADAPTERS_PENDING'),
        ('4.1','价格快照、预算上限、预计时间与幂等恢复','BUDGET_PENDING'),
        ('4.2','协议3批×5档×4方式×1000，预热100','RAW_60000_METRIC_AUDIT_PENDING'),
        ('4.2','状态3路径×7档×200×3批，预热20','NOT_STARTED'),
        ('4.2','量化4精度×4维度×4分布，≥200唯一状态','NOT_STARTED'),
        ('4.2','状态复用/增量矩阵','NOT_STARTED'),('4.2','检索4容量×5方式×240 query×3次建库','LABELS_PENDING'),
        ('4.2','链长6档×并发5档，每格≥30批','NOT_STARTED'),('4.2','连续≥100轮、6类故障各≥30次','NOT_STARTED'),
        ('4.3','逐项消融8机制及JSON/MessagePack参考','NOT_STARTED'),
        ('5','逐任务/调用/消息/状态/记忆/资源完整原始表','RECORDER_CALIBRATION_PENDING'),
        ('6','链聚类bootstrap、置换、Holm、保守质量界限和功效门禁','NOT_YET_FROZEN'),
        ('7','8主图、6主表、附录与数据源','NOT_STARTED'),
        ('8','5–10任务计量校准、字节守恒/重复计量/仪器开销','NOT_STARTED'),
        ('8','至少30原始单元人工抽查及结论索引','REQUIRED_PENDING')]
    with (OUT/'analysis/protocol_compliance.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['protocol_section','requirement','status']);writer.writerows(requirements)
    with (OUT/'preflight_report.md').open('a') as f:
        f.write('\n## 按用户要求纠正准备条件\n\n主实验严格遵循原方案：表示不同、业务事实完整等价，不采用完全相同提示词。历史相同提示词排查结果单列，不进入正式统计。双人审核、盲评、预算与计量门禁保留。逐条进度见 analysis/protocol_compliance.csv。\n')
    print(json.dumps(amendment,ensure_ascii=False))

if __name__=='__main__':main()
