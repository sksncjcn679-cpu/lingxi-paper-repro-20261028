from pathlib import Path
import json, hashlib

p = Path(__file__).resolve().parent
data = json.loads((p / 'release-ab.json').read_text())
metrics = ('message_count', 'text_chars', 'text_tokens', 'wire_bytes', 'rss_peak_mb', 'latency_sec')
lines = ['# 冻结1.7与1.8.6本轮同机对照', '', '> 与全量测试及真实API运行并发；时延置信区间仅描述此次并发环境，不证明独占目标机性能。消息、字符、token、wire、RSS均为新−旧的百分比变化，负数表示下降。', '', '| 组 | 消息 | 字符 | token | wire | RSS | 耗时 | 耗时95%CI（秒） |', '|---|---:|---:|---:|---:|---:|---:|---|']
for name, row in data['summary'].items():
    values=[]
    for key in metrics:
        v=row.get(key,{}).get('change_percent')
        values.append('N/A' if v is None else f'{v:+.2f}%')
    ci=row.get('latency_sec',{}).get('ci95')
    lines.append('| '+name+' | '+' | '.join(values)+' | '+(str(ci) if ci else 'N/A')+' |')
lines += ['', '## 身份和一致性', '', *[f'- {v} source SHA：`{h}`' for v,h in data['sources'].items()], f'- 交错配对轮数：{data["methodology"]["rounds"]}；原始配对cell：{len(data["raw"])}。', '- 每cell核对task_sha；memory组核对两版本答案列表一致，任一不一致即终止。', '- 结果不可代替文稿中未经执行的BGE/A100/隐藏层/FAISS/eBPF实验。', '']
(p/'七组对照结果.md').write_text('\n'.join(lines),encoding='utf-8')
files=[f for f in p.iterdir() if f.is_file() and f.name!='MANIFEST.sha256']
(p/'MANIFEST.sha256').write_text('\n'.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name for f in sorted(files))+'\n')
print('\n'.join(lines))
