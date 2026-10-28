"""Read-only current-container I/O capability diagnostic, not historical repair."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path

source = Path('/evidence/protocol_repair_20261020_v1/formal_snapshot_v1/drivers/formal_recording.py')
assert hashlib.sha256(source.read_bytes()).hexdigest() == '0357d1f36373d4bfc403dc1b9515685aa7f168586624401e3f74e98e5ea0011c'
spec = importlib.util.spec_from_file_location('readonly_frozen_resource_recording_probe', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
results = []
for label, pid in [('self', os.getpid()), ('container_init', 1)]:
    item = {'subject':label,'pid':pid}
    try:
        value = Path(f'/proc/{pid}/io').read_text()
        parsed = {key:int(number.strip()) for key,number in
                  (line.split(':',1) for line in value.splitlines())}
        item.update(raw_read_status='READ_AND_PARSE_SUCCEEDED', observed_keys=sorted(parsed))
    except (ValueError, OSError) as error:
        item.update(raw_read_status='FAILED', error_type=type(error).__name__, errno=getattr(error,'errno',None))
    native = module.read_io(pid)
    item.update(frozen_helper_returns_null=native is None,
                frozen_helper_keys=sorted(native) if isinstance(native,dict) else None)
    results.append(item)
print(json.dumps({'status':'CURRENT_READ_ONLY_CAPABILITY_PROBE_ONLY',
    'observed_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'results':results,'historical_io_values_reconstructed':False,
    'historical_exception_cause_inferred':False,'model_or_benchmark_calls':0}))
