"""Inspect only kernel/proc capability metadata without inventing history."""
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import platform

release=platform.release()
wanted={'CONFIG_TASKSTATS','CONFIG_TASK_XACCT','CONFIG_TASK_IO_ACCOUNTING','CONFIG_PROC_FS','CONFIG_IKCONFIG_PROC'}
configs=[]
for p in (Path('/proc/config.gz'),Path('/boot/config-'+release),Path('/lib/modules')/release/'build/.config'):
    item={'path':str(p),'exists':p.is_file()}
    if item['exists']:
        try:
            b=p.read_bytes(); text=(gzip.decompress(b) if p.suffix=='.gz' else b).decode('utf-8')
            item['selected_flags']=[line for line in text.splitlines() if
                line.split('=',1)[0] in wanted or any(line=='# '+flag+' is not set' for flag in wanted)]
        except (OSError,ValueError) as e:
            item.update(read_error=type(e).__name__,errno=getattr(e,'errno',None))
    configs.append(item)
proc={}
for base in ('self','1'):
    proc[base]={name:(Path('/proc')/base/name).exists() for name in ('io','stat','status','fd')}
mounts=[]
for line in Path('/proc/self/mountinfo').read_text().splitlines():
    columns=line.split(); at=columns.index('-')
    if columns[4]=='/proc' or columns[4].startswith('/proc/'):
        mounts.append({'mount_point':columns[4],'options':columns[5],'filesystem':columns[at+1]})
print(json.dumps({'status':'CURRENT_KERNEL_PROC_METADATA_ONLY','observed_utc':datetime.now(timezone.utc).isoformat(),
    'kernel_release':release,'uname':list(platform.uname()),'proc_paths':proc,'kernel_config_candidates':configs,
    'proc_mounts':mounts,'historical_per_sample_error_not_reconstructed':True}))
