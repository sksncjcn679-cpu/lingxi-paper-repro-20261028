"""Host-only prospective registration and static AST checks. No raw/native execution."""
from __future__ import annotations
import ast
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    workspace=Path(__file__).resolve().parent.parent
    out=workspace/'paper_experiments_20261019_pilot/formal_20261019'
    target=out/'analysis/state_measurement_correction_v2_registration.json'
    if target.exists() or (out/'state_measurement_correction_v2').exists():raise RuntimeError('registration and run root must both be new')
    driver=workspace/'deployment/formal_state_measurement_correction_v2.py'
    analyzer=driver.with_name('formal_state_measurement_correction_v2_analyze.py')
    tree=ast.parse(driver.read_text(encoding='utf-8'));ast.parse(analyzer.read_text(encoding='utf-8'))
    # Only evaluate the stdlib AST-rewrite definitions, never the driver module,
    # its imports, experiment entrypoints, or rewritten native function bodies.
    names={'ast_key','phase_block','timed_context','StripInstrumentation','NativeMapTransform','VerifyTransform','ConsumerTransform'}
    rewrite=ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names],type_ignores=[])
    namespace={'ast':ast};exec(compile(rewrite,'<static-ast-only>','exec'),namespace)
    proofs=[]
    for name,path,transform in (
        ('consumer',workspace/'deployment/formal_state_matrix.py','ConsumerTransform'),
        ('verify_sealed_fd',workspace/'final-submit/src/src/agent_runtime/state_exchange/fd_passing.py','VerifyTransform')):
        original=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name==name)
        original.decorator_list=[]
        instrumenter=namespace[transform]();rewritten=instrumenter.visit(copy.deepcopy(original));ast.fix_missing_locations(rewritten)
        stripped=namespace['StripInstrumentation']().visit(copy.deepcopy(rewritten));ast.fix_missing_locations(stripped)
        original_ast=ast.dump(original,include_attributes=False);stripped_ast=ast.dump(stripped,include_attributes=False)
        if original_ast!=stripped_ast:raise RuntimeError('static stripped AST differs')
        compile(ast.Module(body=[rewritten],type_ignores=[]),'<not-executed:'+name+'>','exec')
        proofs.append({'function':name,'source_sha256':sha(path),'original_ast_sha256':hashlib.sha256(original_ast.encode()).hexdigest(),
            'instrumented_ast_sha256':hashlib.sha256(ast.dump(rewritten,include_attributes=False).encode()).hexdigest(),
            'stripped_ast_sha256':hashlib.sha256(stripped_ast.encode()).hexdigest(),'stripped_ast_equivalent':True,
            'native_function_executed':False,'transform_boundaries':vars(instrumenter)})
    values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and
        isinstance(n.targets[0],ast.Name) and n.targets[0].id in {'NATIVE','BASELINE_SHA','EXPECTED_SOURCE','ACK_FIELDS'}}
    source_meta=json.loads((out/'analysis/source_test_postcheck_v4/source_before.json').read_text(encoding='utf-8'))
    entries={row['path']:row for row in source_meta['formal']['files']['files']}
    for relative,expected in values['NATIVE'].items():
        actual=sha(workspace/'final-submit/src'/relative)
        if actual!=expected or entries[relative]['sha256']!=expected:raise RuntimeError('formal native source bytes differ')
    if sha(out/'drivers/frozen/formal_state_matrix.py')!=values['BASELINE_SHA'] or sha(workspace/'deployment/formal_state_matrix.py')!=values['BASELINE_SHA']:
        raise RuntimeError('original/frozen state driver bytes differ')
    draft='https://json-schema.org/draft/2020-12/schema'
    stage_names=['receiver_precheck','receiver_attach_map_view','receiver_object_open','receiver_native_map',
        'receiver_payload_read_integrity','receiver_payload_buffer_assembly','receiver_handle_map_release','receiver_descriptor_close']
    identity={'batch':{'type':'integer','minimum':1,'maximum':3},'transfer_ordinal':{'type':'integer','minimum':0,'maximum':9239},
        'path':{'enum':['copy','shm','memfd']},'logical_bytes':{'enum':[1024,4096,16384,65536,262144,1048576,4194304]},
        'payload_sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},'success':{'type':'boolean'},
        'consumer_pid':{'type':'integer','exclusiveMinimum':0},'source_identity_sha256':{'const':values['EXPECTED_SOURCE']}}
    stage={'type':'object','required':['name','offset_start_ns','offset_end_ns','duration_ns','nested_parent','error_type'],
        'properties':{'name':{'enum':stage_names},'offset_start_ns':{'type':'integer','minimum':0},
            'offset_end_ns':{'type':'integer','minimum':0},'duration_ns':{'type':'integer','minimum':0},
            'nested_parent':{'type':['string','null']},'error_type':{'type':['string','null']}},'additionalProperties':False}
    child={'$schema':draft,'type':'object','required':[*identity,'stages','stage_totals_ns','consumer_total_ns',
        'common_recvmsg_wait_prefetch_ns','common_header_assembly_parse_ns','consumer_observation_end_ns','phase_partition_residual_ns',
        'frame_received_fd_count','phase_error','read_mode','timing_partition'],
        'properties':{**identity,'stages':{'type':'array','maxItems':64,'items':stage},
            'stage_totals_ns':{'type':'object','additionalProperties':{'type':'integer','minimum':0}},
            **{name:{'type':'integer','minimum':0} for name in ('consumer_total_ns','common_recvmsg_wait_prefetch_ns',
                'common_header_assembly_parse_ns','consumer_observation_end_ns','phase_partition_residual_ns','frame_received_fd_count')},
            'phase_error':{'type':['object','null']},'read_mode':{'type':'string'},'timing_partition':{'type':'string'}},
        'additionalProperties':True,'$comment':'Complete-row schema. Failure/partial rows remain unmodified and make the full-matrix gate FAIL; never discard them to satisfy this schema.'}
    producer={'$schema':draft,'type':'object','required':[*identity,'producer_pid','condition','iteration','warmup',
        'producer_prepare_ns','handoff_roundtrip_ns','producer_release_ns','task_wall_ns','receiver_read_verify_ns','observed_ack_fields'],
        'properties':{**identity,'producer_pid':{'type':'integer','exclusiveMinimum':0},'condition':{'enum':['cold','hot']},
            'iteration':{'type':'integer','minimum':-20,'maximum':199},'warmup':{'type':'boolean'},
            'observed_ack_fields':{'const':sorted(values['ACK_FIELDS'])},
            **{name:{'type':'integer','minimum':0} for name in ('producer_prepare_ns','handoff_roundtrip_ns','producer_release_ns',
                'task_wall_ns','producer_cpu_ns','handoff_producer_cpu_ns','hot_one_time_setup_ns','receiver_read_verify_ns',
                'receiver_cpu_ns','local_materialization_bytes','socket_observed_bytes','fd_count_sent','producer_fd_count',
                'consumer_fd_count','producer_peak_rss_bytes','consumer_peak_rss_bytes')}},'additionalProperties':True,
        '$comment':'Original ACK fields copied unchanged plus independently journaled identity/measurement metadata; no new fields are sent over the wire.'}
    resource={'$schema':draft,'type':'object','required':['batch','monotonic_ns','processes','loadavg','sampling_interval_sec'],
        'properties':{'batch':identity['batch'],'monotonic_ns':{'type':'integer','minimum':0},
            'sampling_interval_sec':{'const':.2},'processes':{'type':'array','minItems':1,'items':{'type':'object',
                'required':['pid','rss','cpu_sec','fds'],'properties':{'pid':{'type':'integer','exclusiveMinimum':0},
                    'rss':{'type':'integer','minimum':0},'cpu_sec':{'type':'number','minimum':0},'fds':{'type':'integer','minimum':0}}}},
            'loadavg':{'type':'array','minItems':3,'maxItems':3,'items':{'type':'number','minimum':0}}},'additionalProperties':False}
    raw_schemas={'producer':producer,'child_stage':child,'resources':resource}
    original_inputs=['manifests/formal-freeze.json','manifests/original_protocol.md','manifests/state_matrix_plan.json',
        'manifests/source_identity.json','manifests/source-files.json','manifests/dependencies.txt','manifests/environment.json']
    result={'schema_version':1,'status':'PREPARED_NOT_EXECUTED','registered_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Prospective measurement correction restoring existing original protocol4.2/5.2 mapping/reading/release breakdown; no new research arm/mechanism/size and no paid replay.',
        'preformal_registration_claim':False,'relative_output_root':'state_measurement_correction_v2',
        'driver':'deployment/formal_state_measurement_correction_v2.py','driver_sha256':sha(driver),
        'analyzer':'deployment/formal_state_measurement_correction_v2_analyze.py','analyzer_sha256':sha(analyzer),
        'preparation_helper_sha256':sha(Path(__file__)),'execution_helper_sha256':sha(workspace/'deployment/formal_postrun_verify.py'),
        'source_identity_sha256':values['EXPECTED_SOURCE'],'native_file_sha256':values['NATIVE'],'original_state_driver_sha256':values['BASELINE_SHA'],
        'registered_input_sha256':{relative:sha(out/relative) for relative in original_inputs},
        'matrix':{'paths':['copy','shm','memfd'],'logical_bytes':[1024,4096,16384,65536,262144,1048576,4194304],
            'conditions':['cold','hot'],'measured_per_cell':200,'warmup_per_cell':20,'batches':3,'cells':126,
            'measured_rows':25200,'warmup_rows':2520,'total_actual_transfers':27720,'rows_per_batch_including_warmup':9240},
        'batch_timeout_sec':1800,'consumer_socket_timeout_sec':60,'post_timing_consumer_flush_wait_sec':60,'automatic_retry':False,
        'original_data_and_flow':'Same np.arange(size//4,dtype=float32), original frozen materialize/transfer/release functions and original consumer AST. No extra data copy/touch/hash pass. Native sealed memfd readonly MAP_PRIVATE→MAP_SHARED→pread order remains unchanged.',
        'cold_hot_boundary':'cold creates/releases producer object every transfer; hot reuses one producer object with separate setup timing. Every SHM/memfd hot consumer still attaches/maps anew. Neither condition claims process/OS cache cold-start.',
        'wire_boundary':{'header_schema':'Original mode,size,sha256; SHM additionally descriptor; original AF_UNIX framing and recv_fds65536 unchanged.',
            'ack_fields':sorted(values['ACK_FIELDS']),'ack_policy':'Original seven-field ACK schema/message count/payload format retained. Measured time numeric lengths may differ; do not claim identical ACK byte counts.',
            'stage_transport':'Stage fields stay child-local until transfers stop; never added to header or ACK.'},
        'phase_boundary':{'stage_names':stage_names,'origin':'Original consumer body start following header decode; duration_ns=end-start.',
            'receiver_attach_map_view':'SHM context enter includes descriptor validation, object open, actual map and NumPy readonly view creation.',
            'receiver_object_open':'Actual POSIX shm_open call; nested within SHM attach.',
            'receiver_native_map':'Actual SharedMemory mmap constructor (SHM nested) or original memfd mmap context enter (top level); failed attempts retained. Lazy page faults may occur later during consumption.',
            'receiver_payload_read_integrity':'Actual original hashlib buffer traversal and integrity check. Fused read+hash, never pure read or pure memory-I/O latency.',
            'receiver_payload_buffer_assembly':'Original exact() allocation/prefix copy/remainder receive for socket copy; or each original pread fallback read. Does not erase earlier recvmsg prefetch.',
            'receiver_handle_map_release':'Original SHM attach context exit or memfd mmap context exit.',
            'receiver_descriptor_close':'Original finally os.close received descriptors, after original consumer_total; bounded by consumer_observation_end.',
            'common_windows':'recvmsg_wait_prefetch/header_assembly_parse separate. Blocking receive can start before parent handoff, including producer setup/logging wait, so no <=RTT assertion and no addition to RTT.',
            'partition':'consumer_total=union(body-stage intervals)+nonnegative residual, excluding descriptor_close/common windows. Nested map/open is not added again. Parent handoff RTT/child phases are never summed.',
            'not_applicable':'Absent phase remains N/A with sample/NA counts, never implicit zero.'},
        'linkage':'All27720 producer and child rows keyed(batch,transfer_ordinal), each batch0..9239 exactly once. iteration-20..-1 warmups retained separately; only25200 measured observations enter stage distributions.',
        'instrumentation':{'phase_buffer_max_rows_per_batch':9240,'stage_events_max_per_transfer':64,
            'buffer_policy':'Bounded child-local rows and phases; deep size/serialized bytes measured after timed transfers stop; process RSS includes recorder memory.',
            'overhead_policy':'Additional clock calls, Python phase bookkeeping, phase buffering and retained warmup journaling are recording overhead. Wall/CPU/residual remain actual; no subtraction and no claim negligible.',
            'separate_uninstrumented_paired_overhead':'UNKNOWN_NOT_MEASURED; not inferred from the earlier different recorder calibration.'},
        'source_isolation':'Original business files and frozen driver remain byte-identical. Timer clones of original consumer/memfd verifier and runtime Linux SharedMemory.__init__ must strip to original AST; native mapping returns actual native objects.',
        'static_ast_checks':proofs,'stdlib_sharedmemory_ast_check':'PENDING_ACTUAL_RUNTIME_SOURCE_AND_EQUIVALENCE_CHECK; not run on container during preparation.',
        'runtime_gate':'Acquire both original postrun/provider locks nonblocking; original real+pressure exit0; fixed input/native hashes; fresh registered destination; per-batch process-group timeout cleanup. Stop on first failure, retain partial data/logs, never retry.',
        'cleanup':'Owner closes/unlinks state objects; consumer closes local handles and received FDs. Check SHM residues, actual FD target multiset and only the existing resource_tracker pipe within bound5; no claim absolute zero FDs.',
        'analysis':'Independent stdlib analyzer reads only this new root; exact full matrix/ordinal/AST/source/cleanup/phase-bounds audit. Export126 stage_cells.csv with empirical type7 p50/p95/p99 and means, ns/ms, actual n/NA; all failures retained.',
        'old_state_result_policy':'Original25200 full-path timings/resources remain valid in existing main table4/figure5. New phase supplement has independent source/n and is never pooled with old observations; no retrospective phase recovery.',
        'delivery':'New supplement table4 stages only; no changes to frozen primary statistics, quality denominator, original115 authority or retrieval selection bytes.',
        'raw_schemas':raw_schemas,'raw_schema_validation_executed':False,'matrix_executed':False,'provider_calls':0,
        'preparation_only':'Host stdlib AST parse/rewrite/strip/compile without native-body execution, code/metadata hashes. No raw/outcome scan, container process, experiment or provider call.'}
    with target.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    note=target.with_suffix('.md')
    note.write_text('# 状态分项前瞻补测注册 v2\n\n状态：PREPARED_NOT_EXECUTED。恢复原方案4.2/5.2已有分项要求，不新增研究条件。\n\n'
        '保留原25200条完整路径结果与主状态图表。新补测25200 measured +2520 warmup，双流全部27720次传递按batch/ordinal一一关联；新分项独立补充，不拼池。\n\n'
        '原source、frozen driver、frame和ACK schema保留；零拷贝真实哈希读取不加copy/touch。映射、读取并校验、释放分别记录；读取与哈希融合，不能称纯读取。common接收等待可早于父RTT；子span不与RTT相加。\n\n'
        '新增计时、缓存与warmup留档开销保留不扣除；单独无插桩配对开销未知。阶段缓存停测后落盘，记录内存范围。两锁、固定源SHA、AST剥离等价、独立目录、不重试，失败保留。\n\n'
        '源码仅通过主机AST静态检查；实际Linux SharedMemory构造函数的等价验证将在正式补测启动时完成并留证。本注册不冒充原正式实验前冻结。\n',encoding='utf-8')
    for path in (driver,analyzer,Path(__file__)):
        destination=out/'drivers'/path.name
        if destination.exists():raise RuntimeError('archival helper already exists')
        shutil.copyfile(path,destination)
        if sha(path)!=sha(destination):raise RuntimeError('archival byte copy failed')
    print(json.dumps({'status':'PREPARED_NOT_EXECUTED','driver_sha256':sha(driver),'analyzer_sha256':sha(analyzer),
        'registration_sha256':sha(target),'registration':str(target),'static_ast_checks':len(proofs),'matrix_executed':False},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
