"""Create a new recorded-data bundle; all sources are read-only and preserved."""
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
import hashlib
import json
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT.parent
DEST = ROOT / 'delivery/final_bundle_v1'
DOC_RECEIPT = ROOT / 'delivery/manuscript_append_v1/final_delivery_receipt.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')


def main():
    DEST.mkdir(exist_ok=False)
    started = now()
    save(DEST / 'started.json', {'status': 'ACTUAL_BUNDLE_BUILDER_STARTED', 'started_utc': started,
                                'model_calls': 0, 'numerical_statistics_runs': 0})
    deadline = time.monotonic() + 2700
    while not DOC_RECEIPT.is_file():
        if time.monotonic() > deadline:
            raise TimeoutError('Document actual QA receipt not observed; no successful bundle claim')
        time.sleep(10)
    document = json.loads(DOC_RECEIPT.read_text(encoding='utf-8'))
    assert document['status'] == 'COMPLETE_APPEND_ONLY_MANUSCRIPT_AND_ALL_PAGE_VISUAL_QA'
    assert document['original_137_blocks_unchanged'] is True
    docx = Path(document['docx_path'])
    assert sha(docx) == document['sha256']
    assert Path(document['visual_qa_receipt_path']).is_file()
    secret = None
    for line in (PROJECT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('VLM_API_KEY='):
            secret = line.split('=', 1)[1].strip().strip('\"').strip("'").encode()
    assert secret and len(secret) > 12
    roots = [
        (ROOT, 'new_study'),
        (PROJECT / 'paper_experiments_20261019_pilot/protocol_repair_20261020_v1', 'original_corrected_study'),
        (Path('E:/codex_rebuilds/completion_20261021_v1/seed_write/run_v3'), 'offline_seed_write_v3'),
        (Path('E:/codex_rebuilds/completion_20261021_v1/stress_ci'), 'offline_stress_ci'),
        (Path('E:/codex_rebuilds/corrected_postrun_v2/archive/virtual/opt/lingxi/formal-checkout'), 'frozen_runtime_source')]
    singles = [
        (PROJECT / '论文实验设计方案.md', 'original_protocol/论文实验设计方案.md'),
        (PROJECT / 'paper_experiments_20261019_pilot/source_package_formal_20261019_v2/source_bundle.tar.gz', 'historical_archives/source_bundle.tar.gz'),
        (PROJECT / 'paper_experiments_20261019_pilot/raw_archive_formal_20261019_v1/original_raw.tar.gz', 'historical_archives/original_raw.tar.gz'),
        (Path('E:/xwechat_files/wxid_isc4w98f5mlm22_5004/msg/file/2026-10/面向多智能体协作的结构化通信、状态交换与共享记忆机制_排版复核稿.docx'), 'original_manuscript/面向多智能体协作_原排版复核稿.docx')]
    inputs, excluded = [], []
    for base, prefix in roots:
        assert base.is_dir(), prefix
        for p in sorted(base.rglob('*')):
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if base == ROOT and (p == DEST or DEST in p.parents):
                continue
            if p.name == '.env' or (p.name.startswith('.env.') and p.name != '.env.example') or '__pycache__' in rel.parts or p.suffix == '.pyc':
                excluded.append({'root': prefix, 'relative_path': rel.as_posix(), 'reason': 'credential-file or interpreter cache excluded'})
                continue
            assert not p.is_symlink(), 'Explicit source required for symlink'
            inputs.append((p, prefix + '/' + rel.as_posix()))
    inputs.extend(singles)
    assert len({name for _, name in inputs}) == len(inputs)
    assert all(not n.startswith('/') and '..' not in PurePosixPath(n).parts for _, n in inputs)
    assert any(n.endswith('/多智能体协作框架_可编辑.drawio') for _, n in inputs)
    target = DEST / '完整实验与新增补充_含原始记录_中文图表_drawio_复现环境_20261021.zip'
    members = {}
    processed = 0
    with (DEST / 'progress.jsonl').open('x', encoding='utf-8') as progress, zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED, compresslevel=4, allowZip64=True) as archive:
        for index, (path, name) in enumerate(inputs, 1):
            before = path.stat()
            h, tail = hashlib.sha256(), b''
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if path.suffix.lower() in ('.zip', '.gz', '.png', '.jpg', '.jpeg', '.pdf', '.woff', '.woff2') else zipfile.ZIP_DEFLATED
            with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as dest:
                for data in iter(lambda: source.read(1024 * 1024), b''):
                    if secret in tail + data:
                        raise ValueError('Known credential bytes detected; bundle retained as FAILED, never delivered')
                    h.update(data); dest.write(data); processed += len(data)
                    tail = data[-len(secret):]
            after = path.stat()
            assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'Source changed during capture'
            members[name] = {'bytes': before.st_size, 'sha256': h.hexdigest(), 'source_path': str(path)}
            if index % 25 == 0:
                progress.write(json.dumps({'utc': now(), 'stage': 'COPY_HASH', 'files': index, 'planned_files': len(inputs), 'source_bytes': processed}) + '\n'); progress.flush()
        readme = '''完整实验与新增补充复现包（2026-10-21）

原有内容均保留，新增论文只在原参考文献后追加；补充表/图使用S编号。
new_study含新2160主执行、6公开校准、单次usage预检、所有新原始请求/回答/usage/执行记录、冻结源码、统计、图像、追加稿及实际回执。
original_corrected_study完整保留纠错后的6360正式执行、原审计、容量停止和失败记录；historical_archives保留更早独立离线证据及源码档案。各批不得拼池。
offline_seed_write_v3是720次真实本地初始化，不是720次新LLM任务；offline_stress_ci是30格采后逐格区间，原点不变。
new_study/delivery/figure_package_v2含原图表与draw.io；drawio_framework_original_v1含两个可编辑页面、彩色/黑白PNG/SVG及来源。
new_study/delivery/manuscript_append_v1/final_delivery_receipt.json指出实际QA通过的新DOCX；旧稿与早期布局版本作为过程记录保留。

离线主统计复现（Docker可用，宿主Python3.10+；不会发起模型调用）：
1. docker load --input new_study/delivery/runtime_v1/openeuler_dependency_image_80471.tar
2. python new_study/code/reproduction/run_saved_analysis.py --study-root new_study --out reproduced_stats
输出目录必须不存在，父目录须存在；加载的固定OCI index为sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58。
3. 对比 reproduced_stats/outputs/analysis 下 primary、observed_by_arm、figure_04 等文件与已保存输出；本次已真实验证6份核心输出逐字节一致。
首次图表字段接入失败、实际视觉问题与修复均保留；以figures_v3与实际QA、最终图表包为准。旧替换稿准备工具未用于改写原文。

原始记录和方法已冻结。重新调用真实模型需要自行提供私有密钥、服务余额、全新输出目录和独立批次身份；本包不自动执行付费重跑，也不包含密钥或账户原始账单。
原usage缺失107任务、原初始化计时缺失和RAG容量停止54条保持原状，新数据不能回填。平台I/O、采样RSS/CPU/FD、SDK等待与guest wall的测量边界详见指标覆盖和补充正文。
不得把完成执行/打包解读为质量不劣、普遍加速或全部科学假设通过。
包内文件清单给出逐成员SHA256；包外package_receipt.json记录实际全成员解码CRC/SHA结果及整个ZIP的SHA256。
'''
        archive.writestr('阅读与离线复现说明.txt', readme.encode('utf-8'))
        members['阅读与离线复现说明.txt'] = {'bytes': len(readme.encode()), 'sha256': hashlib.sha256(readme.encode()).hexdigest(), 'source_path': 'generated from fixed actual artifact layout'}
        manifest = {'schema_version': 'complete-experiment-bundle-members-v1', 'created_utc': now(),
                    'members_excluding_manifest_itself': members, 'excluded': excluded,
                    'originals_modified': False, 'known_credential_exclusion_checked_during_copy': True}
        manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
        archive.writestr('完整文件清单_SHA256.json', manifest_raw)
        members['完整文件清单_SHA256.json'] = {'bytes': len(manifest_raw), 'sha256': hashlib.sha256(manifest_raw).hexdigest()}
    secret = None
    with zipfile.ZipFile(target) as archive, (DEST / 'verification_progress.jsonl').open('x', encoding='utf-8') as progress:
        assert len(archive.infolist()) == len(set(archive.namelist())) == len(members)
        for index, (name, expected) in enumerate(members.items(), 1):
            h = hashlib.sha256(); size = 0
            with archive.open(name) as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(data); size += len(data)
            assert h.hexdigest() == expected['sha256'] and size == expected['bytes'], 'Decoded member differs'
            if index % 25 == 0:
                progress.write(json.dumps({'utc': now(), 'stage': 'ACTUAL_DECODE_CRC_SHA', 'files': index, 'total': len(members)}) + '\n'); progress.flush()
    receipt = {'status': 'ACTUAL_FULL_BUNDLE_ALL_MEMBERS_DECODE_CRC_SHA_VERIFIED',
               'started_utc': started, 'finished_utc': now(), 'path': str(target),
               'bytes': target.stat().st_size, 'sha256': sha(target),
               'members': len(members), 'source_bytes': processed, 'original_sources_modified': False,
               'source_sizes_mtimes_unchanged': True, 'actual_final_document_receipt': str(DOC_RECEIPT),
               'known_credential_matches': 0, 'model_calls': 0, 'numerical_statistics_runs': 0}
    save(DEST / 'package_receipt.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
