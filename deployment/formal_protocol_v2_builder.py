"""Version the protocol recorder to add CPU accounting; preserve first recorder and its data."""
from pathlib import Path
import shutil

source=Path(__file__).with_name('formal_protocol_matrix.py')
out=Path('/evidence/formal_20261019')
shutil.copy2(source,out/'drivers/formal_protocol_matrix_initial.py')
text=source.read_text()
changes={
    "import traceback\n":"import traceback\nimport resource\n",
    "ACK=struct.Struct('!?Q32sI')":"ACK=struct.Struct('!?QQ32sI')",
    "start=time.perf_counter_ns()\n            if mode==0:":"start=time.perf_counter_ns();cpu_start=time.process_time_ns()\n            if mode==0:",
    "ACK.pack(True,elapsed,digest,os.getpid())":"ACK.pack(True,elapsed,time.process_time_ns()-cpu_start,digest,os.getpid())",
    "f'raw/protocol_batch_{index}.jsonl'":"f'raw/protocol_v2_batch_{index}.jsonl'",
    "start=time.perf_counter_ns()\n                        if mode==0:":"start=time.perf_counter_ns();cpu_start=time.process_time_ns()\n                        if mode==0:",
    "ok,decode_ns,received,consumer_pid=ACK.unpack":"ok,decode_ns,consumer_cpu_ns,received,consumer_pid=ACK.unpack",
    "end=time.perf_counter_ns()\n                        if received":"end=time.perf_counter_ns();producer_cpu_ns=time.process_time_ns()-cpu_start\n                        if received",
    "'encode_ns':encoded-start,'consumer_decode_ns':decode_ns,":"'encode_ns':encoded-start,'consumer_decode_ns':decode_ns,\n                            'producer_cpu_ns':producer_cpu_ns,'consumer_decode_verify_cpu_ns':consumer_cpu_ns,\n                            'producer_lifetime_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,",
    "protocol_microbenchmark_plan.json":"protocol_microbenchmark_v2_plan.json",
    "protocol_matrix_status.json":"protocol_matrix_v2_status.json"}
for old,new in changes.items():
    if old not in text:raise RuntimeError(f'expected recorder source fragment missing: {old}')
    text=text.replace(old,new)
target=out/'drivers/formal_protocol_matrix_v2.py'
target.write_text(text)
(out/'analysis/protocol_initial_recording_note.md').write_text('初始 60,000 条记录保留为计量准备数据；由于未记录进程 CPU 时间，不作为完整正式协议矩阵。v2 记录器在相同因子、预热、重复与批次数下补齐 CPU 字段，另存原始记录，不覆盖旧行。\n')
print(target)
