"""Register the full study and isolate its working environment before collecting outcomes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

OUT = Path('/evidence/formal_20261019')
SOURCE = Path('/opt/lingxi/checkout')
WORK = Path('/opt/lingxi/formal-checkout')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    OUT.mkdir(exist_ok=False)
    for name in ['corpus','manifests','raw','logs/preflight','logs/main','logs/model2','logs/external',
                 'logs/mechanisms','logs/failures','analysis','figures','tables','review','drivers']:
        (OUT/name).mkdir(parents=True)
    shutil.copytree(SOURCE, WORK, ignore=shutil.ignore_patterns('results','__pycache__','.pytest_cache','*.egg-info','.agent_data'))
    manifest = []
    for p in sorted((WORK/'src').rglob('*.py')):
        manifest.append({'path':p.relative_to(WORK).as_posix(),'sha256':sha(p)})
    write(OUT/'manifests/source-files.json', manifest)
    write(OUT/'manifests/environment.json', {'prepared_at':datetime.now(timezone.utc).isoformat(),
        'python':sys.version, 'platform':platform.platform(), 'uname':list(platform.uname()),
        'distribution':Path('/etc/os-release').read_text(), 'workdir':str(WORK),
        'kernel_scope':'openEuler user space, shared WSL2 kernel; not a native openEuler VM',
        'resource_caps':{'cpu_quota':Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
                         'memory_max':Path('/sys/fs/cgroup/memory.max').read_text().strip()},
        'host_background_load':'uncontrolled; record load during runs',
        'source_file_manifest_sha256':sha(OUT/'manifests/source-files.json')})
    write(OUT/'manifests/models.json', {'main':{'model':'qwen3.7-plus','extra_body':{'enable_thinking':False}},
        'cross_family':{'model':'doubao-seed-2-0-mini-260215','extra_body':{'thinking':{'type':'disabled'}}},
        'endpoint':'https://www.dmxapi.cn/v1/chat/completions','temperature':0,'max_tokens':1500,
        'timeout_sec':60,'max_attempts':3,'prices':'UNKNOWN','seed':'not supplied or claimed',
        'model_change_reason':'explicit user request after public pilot; frozen before formal outputs',
        'baseline_pilot_data':'excluded from formal estimates'})
    plan = {'status':'PREPARATION_IN_PROGRESS','formal_outcomes_collected':False,
        'matrices':{'main':2160,'cross_model':720,'multiprocess':360,'external_autogen':120,'external_langgraph':120,
                    'mechanism_ablations_minimum':1440,'serialization_references_minimum':360},
        'planned_unique_cases':144,'pilot_unique_cases':24,'formal_unique_cases':120,'formal_chains':20,
        'retrieval_queries':240,'query_no_answer_minimum':60,
        'human_review':'PENDING_USER_DECISION','budget':'PENDING_USER_DECISION',
        'measurement_audit':'NOT_YET_CALIBRATED','corpus_freeze':'NOT_YET_FROZEN',
        'quality_noninferiority':'NO_CLAIM_WITHOUT_PROSPECTIVE_POWER_AND_CONSERVATIVE_BOUND',
        'do_not_claim':['native openEuler kernel','child directly maps SHM in existing AgentHost path',
                        'model-token savings with identical model prompts','complete IPC bytes across uninstrumented paths']}
    write(OUT/'manifest.json', plan)
    write(OUT/'analysis/abstract_metric_contract.json', {
        'unique_task_count':{'source':'corpus/tasks.jsonl formal split; unique task and chain IDs','unit':'case; chain'},
        'success_rate_difference':{'source':'raw/task_runs.jsonl','denominator':'all frozen planned units including failures',
            'comparison':['S0-T0','SS0-S0','T1-T0','S1-S0','SS1-SS0'],
            'interval':'domain-stratified chain cluster bootstrap 10000; conservative one-sided quality lower bound',
            'repeated_runs':'not independent cases'},
        'provider_input_token_change':{'source':'raw/provider_calls.jsonl usage.prompt_tokens',
            'note':'report measured zero if identical prompts; never substitute tokenizer proxy'},
        'observable_IPC_byte_change':{'source':'raw/messages.jsonl plus measured setup/ACK/close and AgentHost RPC accounting',
            'unit':'bytes; keep state materialization and logical bytes separate'},
        'state_increment':{'comparison':'SS0-S0; state-applicable subset and all cases',
            'requirements':'producer, reference, receiver, actual computation, cleanup; size/precision/path strata'},
        'memory_increment':{'comparison':['T1-T0','S1-S0','SS1-SS0'],
            'requirements':'retrieval, adoption, feedback separate; negative transfer and relevance denominators'},
        'latency':{'source':'task wall and raw/spans.jsonl','requirements':'provider wait plus mutually exclusive span partition; p50/p95/p99'},
        'validity_boundaries':{'source':'size/path/correlation/concurrency/fault matrices',
            'missing':'UNSUPPORTED or INCOMPLETE; never zero-fill'},
        'claims_policy':'estimates may be negative, null or uncertain; do not force abstract placeholders to imply improvement'})
    text = '''## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-19
- Verification Status: PREPARATION
- Version Label: formal_20261019

## 正式实验登记

目标已启动。本目录与公开试运行分开。主模型按用户要求登记为 qwen3.7-plus，跨家族复核使用豆包 mini；不以观察正式结果为依据更换模型、删任务或改指标。

最低真实矩阵为 3,480 个任务单元，另有消融与序列化参考矩阵。任务、检索标注、计量审计和预算尚未冻结，当前不得声称主实验已经采集完成。

摘要指标合同见 `analysis/abstract_metric_contract.json`。真实 usage、通信 token、可观测 IPC 字节、本地物化字节和逻辑字节分别记录。相同语义提示词不会被包装为模型 token 压缩收益。

目前需要用户决定双人任务/检索审核、盲评的执行方式及预算规则。准备阶段继续建立案例、校验器、完整记录器与离线机制矩阵；不将自动标签假称人工审核。原生内核、namespace/cgroup 权限及子 Agent 直接 SHM 映射的既有限制将明确保留。
'''
    (OUT/'preflight_report.md').write_text(text, encoding='utf-8')
    write(OUT/'review/review_requirements.json',{'task_reviewers_required':2,'retrieval_labelers_required':2,
        'open_output_blind_ratings_minimum':120,'reviewer_assignments':None,'user_decision':None})
    (OUT/'README_reproduce.md').write_text('本实验尚在冻结准备阶段。所有运行入口与依赖将在 manifests 和 drivers 中归档；当前不能用试运行结果替代正式数据。\n',encoding='utf-8')
    shutil.copy2(__file__,OUT/'drivers/formal_prepare.py')
    print(json.dumps({'prepared':str(OUT),'workdir':str(WORK),'manifest':plan},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
