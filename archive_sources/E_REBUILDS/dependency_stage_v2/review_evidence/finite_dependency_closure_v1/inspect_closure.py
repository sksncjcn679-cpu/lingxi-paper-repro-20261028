"""Read distribution metadata in the paid container; never import SDK/business."""
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

DEST = Path(__file__).resolve().parent
STAGE = DEST.parent.parent
CONTAINER = 'lingxi-openeuler-2403'


def utc(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def dump(name, obj):
    with (DEST/name).open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


REMOTE = r'''
import importlib.metadata as md
import json
from pathlib import Path
import re
import subprocess
import sys

roots=sorted(str(path) for path in Path('/usr').glob('lib*/python3.11/site-packages') if path.is_dir())
venv=Path('/opt/lingxi/formal-venv/lib/python3.11/site-packages')
def identity(dist,scope):
    metadata=Path(dist._path)
    location=Path(dist.locate_file(''))
    tops=[name.strip() for name in (dist.read_text('top_level.txt') or '').splitlines() if re.fullmatch(r'[A-Za-z_]\w*',name.strip())]
    modules=[]
    for name in tops:
        for path in [location/name,location/(name+'.py')]:
            if path.exists():modules.append(str(path))
    requires=[]
    for value in dist.requires or []:
        # Package metadata only. Direct-URL values are never emitted.
        if '@' in value:
            match=re.match(r'\s*([A-Za-z0-9_.-]+)',value)
            requires.append({'name':match.group(1) if match else 'UNPARSED','direct_url_redacted':True})
        else:
            requires.append({'requirement':value})
    result={'name':dist.metadata.get('Name'),'version':dist.version,'location':str(location),
            'metadata_path':str(metadata),'module_roots':modules,'scope':scope,'requires':requires}
    if scope=='global':
        candidates=[str(metadata)]+modules
        for name in ('METADATA','PKG-INFO'):
            if (metadata/name).exists():candidates.append(str(metadata/name))
        owners=[]
        for path in dict.fromkeys(candidates):
            p=subprocess.run(['rpm','-qf','--qf','%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n',path],capture_output=True,text=True)
            owners.append({'path':path,'exit_code':p.returncode,'exact_nevra':sorted(set(p.stdout.strip().splitlines())) if p.returncode==0 else []})
        result['rpm_owners']=owners
    return result

result={'python_version':'.'.join(map(str,sys.version_info[:3])),
        'global_paths':roots,'global_distributions':[identity(d,'global') for d in md.distributions(path=roots)],
        'venv_path':str(venv),'venv_distributions':[identity(d,'venv') for d in md.distributions(path=[str(venv)])],
        'source_project_metadata':[identity(d,'source_metadata') for d in md.distributions(path=['/opt/lingxi/formal-checkout/src'])],
        'stdlib_isolated_no_site_startup':True,'SDK_or_business_imports':False,
        'pyvenv_include_system_site_packages':[line.strip() for line in Path('/opt/lingxi/formal-venv/pyvenv.cfg').read_text().splitlines() if line.startswith('include-system-site-packages')]}
print(json.dumps(result,ensure_ascii=False))
'''


def main():
    started, tick = utc(), time.perf_counter()
    command=['docker','exec',CONTAINER,'/usr/bin/python3','-I','-S','-c',REMOTE]
    process=subprocess.run(command,capture_output=True,timeout=90)
    (DEST/'metadata_inventory.stdout.json').write_bytes(process.stdout)
    (DEST/'metadata_inventory.stderr.txt').write_bytes(process.stderr)
    execution={'command':command,'started_utc':started,'finished_utc':utc(),'wall_sec':time.perf_counter()-tick,
               'exit_code':process.returncode,'source_sha256':sha(Path(__file__)),
               'stdout_sha256':sha(DEST/'metadata_inventory.stdout.json'),'stderr_sha256':sha(DEST/'metadata_inventory.stderr.txt'),
               'network_install_download_build_smoke_or_API_calls':0}
    dump('actual_metadata_command.json',execution)
    if process.returncode:raise RuntimeError('metadata-only command failed; original exit/output retained')
    inventory=json.loads(process.stdout)
    providers={}
    duplicates=[]
    for scope in ('global_distributions','venv_distributions','source_project_metadata'):
        for dist in inventory[scope]:
            name=canonicalize_name(dist['name'])
            if name in providers and providers[name]['scope']==dist['scope']:duplicates.append(name)
            providers[name]=dist
    environment={'implementation_name':'cpython','implementation_version':inventory['python_version'],
                 'os_name':'posix','platform_machine':'x86_64','platform_python_implementation':'CPython',
                 'platform_release':'','platform_system':'Linux','platform_version':'',
                 'python_full_version':inventory['python_version'],'python_version':'.'.join(inventory['python_version'].split('.')[:2]),
                 'sys_platform':'linux','extra':''}
    roots={canonicalize_name(dist['name']) for scope in ('venv_distributions','source_project_metadata') for dist in inventory[scope]}
    queue=deque(sorted(roots));visited=set();edges=[];missing=[];inactive=[];unknown=[];global_used={}
    while queue:
        name=queue.popleft()
        if name in visited:continue
        visited.add(name);dist=providers[name]
        if dist['scope']=='global':global_used[name]=dist
        for item in dist['requires']:
            if item.get('direct_url_redacted'):
                unknown.append({'dependent':name,'required_name':item['name'],'reason':'direct URL redacted; not selected by a guessed requirement'})
                continue
            try:requirement=Requirement(item['requirement'])
            except Exception:
                unknown.append({'dependent':name,'reason':'unparsed metadata requirement; value not emitted'});continue
            target=canonicalize_name(requirement.name)
            if requirement.marker and not requirement.marker.evaluate(environment):
                inactive.append({'dependent':name,'required_name':target,'marker':str(requirement.marker)});continue
            provider=providers.get(target)
            edge={'dependent':name,'required_name':target,'specifier':str(requirement.specifier),
                  'marker':str(requirement.marker) if requirement.marker else None,
                  'provider_name':provider['name'] if provider else None,'provider_version':provider['version'] if provider else None,
                  'provider_scope':provider['scope'] if provider else None,
                  'provider_metadata_path':provider['metadata_path'] if provider else None,
                  'version_satisfies_metadata':requirement.specifier.contains(provider['version'],prereleases=True) if provider else False}
            edges.append(edge)
            if not provider or not edge['version_satisfies_metadata']:missing.append(edge)
            if provider and target not in visited:queue.append(target)
    specs_path=STAGE/'context/rpm_specs.txt'
    if not specs_path.exists():specs_path=STAGE.parent/'dependency_stage_v1/context/rpm_specs.txt'
    initial=set(specs_path.read_text(encoding='utf-8').splitlines())
    selected=[];unowned=[]
    for name,dist in sorted(global_used.items()):
        owners=sorted({value for row in dist['rpm_owners'] for value in row['exact_nevra']})
        metadata_owners=sorted({value for row in dist['rpm_owners'] if row['path']==dist['metadata_path'] or row['path'].startswith(dist['metadata_path']+'/') for value in row['exact_nevra']})
        if not metadata_owners:unowned.append(name)
        selected.append({'name':dist['name'],'version':dist['version'],'metadata_path':dist['metadata_path'],'module_roots':dist['module_roots'],
                         'actual_rpm_metadata_owners':metadata_owners,'all_actual_rpm_owners':owners,
                         'missing_from_initial_exact_specs':sorted(set(metadata_owners)-initial),
                         'required_by':sorted({edge['dependent'] for edge in edges if edge['required_name']==name})})
    additional=sorted({rpm for row in selected for rpm in row['missing_from_initial_exact_specs']})
    focus=[]
    for name in ('matplotlib','numpy','scipy','pyarrow','pandas'):
        dist=providers.get(name)
        focus.append({'name':name,'actual_version':dist['version'] if dist else None,'actual_location':dist['metadata_path'] if dist else None,
                      'active_direct_requirements':[edge for edge in edges if edge['dependent']==name]})
    result={'status':'ACTUAL_METADATA_CLOSURE_REVIEW_NOT_INSTALL_OR_SMOKE_CERTIFICATE','created_utc':utc(),
            'actual_global_paths':inventory['global_paths'],'global_distribution_count':len(inventory['global_distributions']),
            'venv_distribution_count':len(inventory['venv_distributions']),'source_metadata_distribution_count':len(inventory['source_project_metadata']),
            'active_roots_count':len(roots),'active_dependency_edges':len(edges),'duplicate_same_scope_names':duplicates,
            'actual_global_suppliers_reached':selected,'minimal_additional_OS_RPMs_for_observed_active_metadata_closure':additional,
            'known_pyparsing_actual_verified':any(row['name'].lower()=='pyparsing' and row['version']=='3.1.1' and 'python3-pyparsing-0:3.1.1-1.oe2403sp3.noarch' in row['actual_rpm_metadata_owners'] for row in selected),
            'global_suppliers_with_no_RPM_metadata_owner':unowned,'active_missing_or_version_mismatched_requirements':missing,
            'unresolved_requirement_metadata':unknown,'focus_actual_distribution_requirements':focus,
            'marker_environment_identity':environment,'initial_exact_rpm_specs_sha256':sha(specs_path),
            'metadata_inventory_sha256':sha(DEST/'metadata_inventory.stdout.json'),
            'bounded_method':'All actual venv/source distributions are roots; evaluate base Requires-Dist for current Linux CPython3.11, no extras, recurse actual providers preferring venv over global. Global metadata/module paths are mapped by actual rpm-qf. No SDK/business imports.',
            'limitations':['Metadata closure cannot discover undeclared/dynamic/optional imports. Nonselected extras are not guessed into the OS set.',
                           'This is the minimum additional RPM owner set for observed active distribution metadata. Runtime shared-library RPM dependencies still need the normal offline exact install resolver; no installation was attempted.',
                           'A future smoke PASS is separate evidence. Existing failed image/smoke records are not relabelled.'],
            'not_performed':['install','download','image build','smoke rerun','SDK import','business import','environment/key read','stage context modification']}
    dump('finite_dependency_closure_v1.json',result)
    dump('active_dependency_edges.json',edges)
    dump('inactive_extra_platform_requirements.json',inactive)
    print(json.dumps({'status':result['status'],'global_distribution_count':result['global_distribution_count'],
                      'additional_exact_OS_RPMs':additional,'known_pyparsing_actual_verified':result['known_pyparsing_actual_verified'],
                      'missing_or_mismatched':len(missing),'unresolved_metadata':len(unknown),'unowned_global_suppliers':unowned,
                      'receipt_sha256':sha(DEST/'finite_dependency_closure_v1.json')},ensure_ascii=False))


if __name__=='__main__':main()
