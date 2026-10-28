"""Audit measured v3 rows and integrity without changing any raw evidence."""
from collections import Counter, defaultdict
from pathlib import Path
import csv
import hashlib
import json
import math
import numpy as np

OUT = Path('/evidence/formal_20261019')
cells = defaultdict(list)
producers, consumers = set(), set()
failures, errors, files, records = 0, [], {}, 0
for batch in (1, 2, 3):
    path = OUT / f'raw/protocol_v3_batch_{batch}.jsonl'
    previous = '0' * 64
    counts = Counter()
    with path.open() as raw:
        for line in raw:
            row = json.loads(line)
            stated = row.pop('record_sha256')
            digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            if stated != digest or row['prev_sha256'] != previous:
                errors.append({'record': records, 'kind': 'HASH_CHAIN'})
            previous = stated
            producers.add(row['producer_pid'])
            consumers.add(row['consumer_pid'])
            failures += not row['success']
            if row['observable_socket_bytes'] != row['payload_wire_bytes'] + row['frame_bytes'] + row['ack_bytes']:
                errors.append({'record': records, 'kind': 'FRAME_ACK_CONSERVATION'})
            if row['warmup_per_cell'] != 100 or row['batch'] != batch:
                errors.append({'record': records, 'kind': 'DESIGN'})
            for field in ('encode_ns', 'consumer_decode_ns', 'producer_cpu_ns', 'consumer_decode_verify_cpu_ns', 'end_to_end_ns'):
                if not isinstance(row[field], (int, float)) or not math.isfinite(row[field]) or row[field] < 0:
                    errors.append({'record': records, 'kind': 'INVALID_MEASUREMENT', 'field': field})
            counts[(row['codec'], row['payload_bytes'])] += 1
            cells[(batch, row['codec'], row['payload_bytes'])].append(row)
            records += 1
    if set(counts.values()) != {1000} or len(counts) != 20:
        errors.append({'batch': batch, 'kind': 'CELL_REPETITIONS', 'counts': str(counts)})
    files[str(path.relative_to(OUT))] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                       'rows': sum(counts.values()), 'final_record_sha256': previous}
if len(producers) != 3 or len(consumers) != 3:
    errors.append({'kind': 'PROCESS_ISOLATION'})
summary = []
for (batch, codec, size), rows in sorted(cells.items()):
    item = {'batch': batch, 'codec': codec, 'payload_bytes': size, 'measurements': len(rows)}
    for field in ('encode_ns', 'consumer_decode_ns', 'producer_cpu_ns', 'consumer_decode_verify_cpu_ns',
                  'end_to_end_ns', 'observable_socket_bytes'):
        data = [r[field] for r in rows]
        item[field + '_mean'] = float(np.mean(data))
        for percentile in (50, 95, 99):
            item[field + f'_p{percentile}'] = float(np.percentile(data, percentile))
    summary.append(item)
stats = OUT / 'analysis/stats'
stats.mkdir(exist_ok=True)
with (stats / 'protocol_v3_cells.csv').open('x', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(summary[0]))
    writer.writeheader()
    writer.writerows(summary)
result = {'status': 'PASS' if not errors and not failures and records == 60000 else 'FAILED',
          'measured_rows': records, 'planned_rows': 60000, 'failures': failures, 'errors': errors,
          'independent_producer_pids': sorted(producers), 'independent_consumer_pids': sorted(consumers),
          'file_integrity': files,
          'scope': 'actual AF_UNIX framed protocol microbenchmark; synthetic business payload; no real model calls',
          'limitations': ['native lean decoder extracts full input JSON; header roundtrip not certified',
                          'decode duration excludes verification hashing; RTT and consumer CPU include verification hashing',
                          'per-row socket bytes exclude process termination sentinel; it is not a benchmark message']}
with (OUT / 'analysis/protocol_v3_audit.json').open('x') as f:
    json.dump(result, f, indent=2)
print(json.dumps(result, indent=2))
raise SystemExit(result['status'] != 'PASS')
