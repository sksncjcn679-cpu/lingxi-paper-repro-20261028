"""Prospective state-path phase supplement; no models and no change to native files.

The old full-path matrix remains valid. This independent matrix restores the
mapping/consumption/release breakdown required by protocol4.2/5.2. Child timers
are observations inside a new instrumented run, never retrospectively split
from old observations. The original wire header and ACK schemas are retained.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import contextlib
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
from importlib.metadata import version
import inspect
import json
import multiprocessing as mp
import os
from pathlib import Path
import resource
import socket
import struct
import sys
import textwrap
import threading
import time

SIZES=(1024,4096,16384,65536,262144,1048576,4194304)
PATHS=('copy','shm','memfd')
SOURCE=Path('/opt/lingxi/formal-checkout')
DEFAULT_OUT=Path('/evidence/formal_20261019')
EXPECTED_SOURCE='a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562'
NATIVE={
    'src/agent_runtime/state_exchange/shared_buffer.py':'9b04b20537f139eadc315ec0654e060d45a1326cefe8d9f28166faae819b4c52',
    'src/agent_runtime/state_exchange/fd_passing.py':'a916f2ae2cf3110f5843a357565e09fd6f09e9ecaad0b5636206f808d2c1cea9',
    'src/agent_runtime/state_exchange/exchange.py':'b6a25e2a93a3df42ba124a84d50616060dad2293e90c9eb9544a5244e084e827'}
BASELINE_SHA='2349422b7b569d222208993654c2309753c4b7ef90c98d7b590c43f55f69e7e5'
ROWS_PER_BATCH=9240
REGISTRATION='analysis/state_measurement_correction_v2_registration.json'
ACK_FIELDS=('success','consumer_pid','receiver_read_verify_ns','receiver_cpu_ns','read_mode','consumer_peak_rss_bytes','consumer_fd_count')


def utc():return datetime.now(timezone.utc).isoformat()


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data:=stream.read(1024*1024):value.update(data)
    return value.hexdigest()


def save(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def source_tree_sha256(root):
    candidates=[root/'pyproject.toml']
    for folder in (root/'src',root/'scenarios',root/'scripts'):
        if folder.is_dir():
            candidates.extend(sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix in {'.py','.sh','.json','.npz'}))
    value=hashlib.sha256()
    for path in sorted(candidates,key=lambda p:p.relative_to(root).as_posix()):
        value.update(path.relative_to(root).as_posix().encode());value.update(b'\0')
        value.update(path.read_bytes());value.update(b'\0')
    return value.hexdigest()


def source_identity(out,require=True):
    result={'aggregate_sha256':source_tree_sha256(SOURCE),
        'native_files':{rel:digest(SOURCE/rel) for rel in NATIVE},
        'baseline_driver_sha256':digest(out/'drivers/frozen/formal_state_matrix.py')}
    if require and (result['aggregate_sha256']!=EXPECTED_SOURCE or result['native_files']!=NATIVE or result['baseline_driver_sha256']!=BASELINE_SHA):
        raise RuntimeError('registered source identity changed')
    return result


def load_baseline(out):
    path=out/'drivers/frozen/formal_state_matrix.py'
    if digest(path)!=BASELINE_SHA:raise RuntimeError('baseline state driver identity changed')
    sys.path.insert(0,str(SOURCE/'src'))
    spec=importlib.util.spec_from_file_location('_state_phase_v2_baseline',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def ast_key(node):return ast.dump(node,include_attributes=False)


def phase_block(body,name):
    return ast.With(items=[ast.withitem(context_expr=ast.Call(func=ast.Name(id='_phase',ctx=ast.Load()),
        args=[ast.Constant(name)],keywords=[]),optional_vars=None)],body=body,type_comment=None)


def timed_context(expression,enter,leave):
    factory=ast.Lambda(args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=expression)
    return ast.Call(func=ast.Name(id='_timed_context',ctx=ast.Load()),
        args=[factory,ast.Constant(enter),ast.Constant(leave)],keywords=[])


class StripInstrumentation(ast.NodeTransformer):
    """Mechanical inverse; any unrecognized semantic edit fails the AST guard."""
    def visit_With(self,node):
        node=self.generic_visit(node)
        if len(node.items)==1 and isinstance(node.items[0].context_expr,ast.Call) and \
                isinstance(node.items[0].context_expr.func,ast.Name) and node.items[0].context_expr.func.id=='_phase':
            return node.body
        return node

    def visit_Call(self,node):
        node=self.generic_visit(node)
        if isinstance(node.func,ast.Name) and node.func.id=='_timed_context':return node.args[0].body
        if isinstance(node.func,ast.Name) and node.func.id=='_timed_call':return node.args[1].body
        return node

    def visit_Expr(self,node):
        if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and \
                node.value.func.id in {'_begin_transfer','_header_ready','_body_start','_body_end','_finish_transfer'}:
            return None
        return self.generic_visit(node)


class NativeMapTransform(ast.NodeTransformer):
    def __init__(self):self.maps=0;self.opens=0
    def visit_Call(self,node):
        node=self.generic_visit(node)
        name=ast.unparse(node.func)
        stage=None
        if name=='mmap.mmap':stage='receiver_native_map';self.maps+=1
        elif name=='_posixshmem.shm_open':stage='receiver_object_open';self.opens+=1
        if stage:
            factory=ast.Lambda(args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=node)
            return ast.copy_location(ast.Call(func=ast.Name(id='_timed_call',ctx=ast.Load()),
                args=[ast.Constant(stage),factory],keywords=[]),node)
        return node


class VerifyTransform(ast.NodeTransformer):
    def __init__(self):self.maps=0;self.reads=0
    def visit_FunctionDef(self,node):
        node=self.generic_visit(node)
        if node.name=='verify_sealed_fd':
            begin=next(i for i,n in enumerate(node.body) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='seals')
            end=next(i for i,n in enumerate(node.body) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='digest')
            node.body[begin:end]=[phase_block(node.body[begin:end],'receiver_precheck')]
        return node
    def visit_With(self,node):
        node=self.generic_visit(node)
        if len(node.items)==1 and isinstance(node.items[0].context_expr,ast.Call) and \
                ast.unparse(node.items[0].context_expr.func)=='mmap.mmap':
            self.maps+=1
            node.items[0].context_expr=timed_context(node.items[0].context_expr,'receiver_native_map','receiver_handle_map_release')
            node.body=[phase_block(node.body,'receiver_payload_read_integrity')]
        return node

    def visit_Assign(self,node):
        call=node.value
        if isinstance(call,ast.Call) and ast.unparse(call.func)=='os.pread':
            self.reads+=1;return ast.copy_location(phase_block([node],'receiver_payload_buffer_assembly'),node)
        if isinstance(call,ast.Call) and ast.unparse(call.func)=='hashlib.sha256':
            return ast.copy_location(phase_block([node],'receiver_payload_read_integrity'),node)
        if isinstance(call,ast.IfExp) and 'h.hexdigest()' in ast.unparse(call):
            return ast.copy_location(phase_block([node],'receiver_payload_read_integrity'),node)
        return node

    def visit_Expr(self,node):
        if isinstance(node.value,ast.Call) and ast.unparse(node.value.func)=='h.update':
            return ast.copy_location(phase_block([node],'receiver_payload_read_integrity'),node)
        return node


class ConsumerTransform(ast.NodeTransformer):
    def __init__(self):self.receive=0;self.attach=0
    def visit_With(self,node):
        node=self.generic_visit(node)
        for item in node.items:
            if isinstance(item.context_expr,ast.Call) and ast.unparse(item.context_expr.func)=='SharedVectorBuffer.attach_readonly':
                self.attach+=1
                item.context_expr=timed_context(item.context_expr,'receiver_attach_map_view','receiver_handle_map_release')
        return node

    def visit_Assign(self,node):
        value=node.value;text=ast.unparse(node)
        if isinstance(value,ast.Call) and ast.unparse(value.func)=='socket.recv_fds':
            self.receive+=1
            begin=ast.Expr(value=ast.Call(func=ast.Name(id='_begin_transfer',ctx=ast.Load()),args=[],keywords=[]))
            return [begin,phase_block([node],'common_recvmsg_wait_prefetch')]
        if isinstance(value,ast.Call) and ast.unparse(value.func)=='exact' and ast_key(node.targets[0])==ast_key(ast.Name(id='payload',ctx=ast.Store())):
            return phase_block([node],'receiver_payload_buffer_assembly')
        if text.startswith('ok = hashlib.sha256('):return phase_block([node],'receiver_payload_read_integrity')
        if text=='start = time.perf_counter_ns()':
            marker=ast.Expr(value=ast.Call(func=ast.Name(id='_body_start',ctx=ast.Load()),args=[ast.Name(id='start',ctx=ast.Load())],keywords=[]))
            return [node,marker]
        if text=='cpu_ns = time.process_time_ns() - cpu':
            marker=ast.Expr(value=ast.Call(func=ast.Name(id='_body_end',ctx=ast.Load()),args=[ast.Name(id='elapsed',ctx=ast.Load())],keywords=[]))
            return [node,marker]
        return node

    def visit_For(self,node):
        node=self.generic_visit(node)
        if ast.unparse(node.target)=='fd' and ast.unparse(node.iter)=='fds':
            node.body=[phase_block(node.body,'receiver_descriptor_close')]
        return node

    def visit_Expr(self,node):
        if isinstance(node.value,ast.Call) and ast.unparse(node.value.func)=='sock.sendall':
            marker=ast.Expr(value=ast.Call(func=ast.Name(id='_finish_transfer',ctx=ast.Load()),
                args=[ast.Name(id='response',ctx=ast.Load())],keywords=[]))
            return [node,marker]
        return node

    def visit_While(self,node):
        node=self.generic_visit(node)
        # The header assembly is unchanged, including recv_fds payload prefetch.
        if isinstance(node.test,ast.Constant) and node.test.value is True:
            begin=next(i for i,n in enumerate(node.body) if isinstance(n,ast.If) and ast.unparse(n.test)=='len(first) < 4')
            end=next(i for i,n in enumerate(node.body) if isinstance(n,ast.If) and ast.unparse(n.test)=="header['mode'] == 'stop'")
            header=node.body[begin:end]
            marker=ast.Expr(value=ast.Call(func=ast.Name(id='_header_ready',ctx=ast.Load()),
                args=[ast.Name(id='header',ctx=ast.Load()),ast.Name(id='fds',ctx=ast.Load())],keywords=[]))
            node.body[begin:end]=[phase_block(header,'common_header_assembly_parse'),marker]
        return node


def instrument(function,transform,bindings,proofs,label):
    if function.__closure__:raise RuntimeError('instrumentation rejects closure-dependent functions')
    text=textwrap.dedent(inspect.getsource(function));module=ast.parse(text)
    original=next(node for node in module.body if isinstance(node,ast.FunctionDef))
    original.decorator_list=[]
    rewritten=transform.visit(copy.deepcopy(original));ast.fix_missing_locations(rewritten)
    stripped=StripInstrumentation().visit(copy.deepcopy(rewritten));ast.fix_missing_locations(stripped)
    if ast_key(original)!=ast_key(stripped):raise RuntimeError('instrumentation changed original AST semantics: '+label)
    namespace={**function.__globals__,**bindings}
    compiled=ast.Module(body=[rewritten],type_ignores=[]);ast.fix_missing_locations(compiled)
    exec(compile(compiled,'<state-phase-v2:'+label+'>','exec'),namespace)
    proofs[label]={'source_file':inspect.getsourcefile(function),'source_file_sha256':digest(inspect.getsourcefile(function)),
        'original_ast_sha256':hashlib.sha256(ast_key(original).encode()).hexdigest(),
        'instrumented_ast_sha256':hashlib.sha256(ast_key(rewritten).encode()).hexdigest(),
        'stripped_ast_sha256':hashlib.sha256(ast_key(stripped).encode()).hexdigest(),'stripped_ast_equivalent':True}
    return namespace[original.name]


class PhaseRecorder:
    def __init__(self,batch):self.batch=batch;self.ordinal=-1;self.current=None;self.rows=[];self.stack=[]
    def begin(self):
        self.ordinal+=1;self.current={'batch':self.batch,'transfer_ordinal':self.ordinal,'stages':[],
            'common_recvmsg_wait_prefetch_ns':0,'common_header_assembly_parse_ns':0,'phase_error':None}
    def header(self,header,fds):
        self.current.update(path=header['mode'],logical_bytes=header.get('size'),payload_sha256=header.get('sha256'),
            frame_received_fd_count=len(fds))
    def body_start(self,start):self.current['_origin']=start
    def body_end(self,total):self.current['consumer_total_ns']=total
    def add(self,name,start,end,error,parent):
        if name.startswith('common_'):
            self.current[name+'_ns']+=end-start;return
        if len(self.current['stages'])>=64:raise RuntimeError('phase buffer per-transfer bound exceeded')
        origin=self.current['_origin']
        self.current['stages'].append({'name':name,'offset_start_ns':start-origin,'offset_end_ns':end-origin,
            'duration_ns':end-start,'nested_parent':parent,'error_type':error})
    def finish(self,response):
        row=self.current;row.update(consumer_pid=os.getpid(),success=response['success'],read_mode=response['read_mode'],
            consumer_observation_end_ns=time.perf_counter_ns()-row['_origin'])
        totals=Counter()
        for stage in row['stages']:totals[stage['name']]+=stage['duration_ns']
        row['stage_totals_ns']=dict(totals)
        intervals=sorted((s['offset_start_ns'],s['offset_end_ns']) for s in row['stages']
            if s['name']!='receiver_descriptor_close' and s['offset_end_ns']<=row['consumer_total_ns'])
        union=0;last=0
        for begin,end in intervals:
            union+=max(0,end-max(begin,last));last=max(last,end)
        row['phase_partition_residual_ns']=row['consumer_total_ns']-union
        row['source_identity_sha256']=EXPECTED_SOURCE
        row['timing_partition']='Body phases overlap only when nested_parent is explicit. Common recvmsg/header windows may begin before parent handoff; descriptor_close is after original receiver_total. Never add child spans to parent RTT.'
        row.pop('_origin',None);self.rows.append(row);self.current=None
        if len(self.rows)>ROWS_PER_BATCH:raise RuntimeError('phase record buffer bound exceeded')
    @contextlib.contextmanager
    def phase(self,name):
        parent=self.stack[-1] if self.stack else None;self.stack.append(name)
        begin=time.perf_counter_ns();error=None
        try:yield
        except BaseException as exc:error=type(exc).__name__;raise
        finally:
            end=time.perf_counter_ns();self.stack.pop();self.add(name,begin,end,error,parent)


def deep_size(value,seen=None):
    seen=set() if seen is None else seen
    if id(value) in seen:return 0
    seen.add(id(value));total=sys.getsizeof(value)
    if isinstance(value,dict):total+=sum(deep_size(k,seen)+deep_size(v,seen) for k,v in value.items())
    elif isinstance(value,(list,tuple)):total+=sum(deep_size(v,seen) for v in value)
    return total


def consumer(sock,batch,out_text,destination_text):
    out=Path(out_text);destination=Path(destination_text);recorder=PhaseRecorder(batch);proofs={};error=None
    baseline=load_baseline(out)
    from multiprocessing import shared_memory
    from agent_runtime.state_exchange import fd_passing
    phase=recorder.phase
    def timed_call(name,factory):
        with phase(name):return factory()
    class TimedContext:
        def __init__(self,factory,enter,leave):self.factory=factory;self.enter_name=enter;self.leave_name=leave
        def __enter__(self):
            with phase(self.enter_name):self.actual=self.factory();return self.actual.__enter__()
        def __exit__(self,*args):
            with phase(self.leave_name):return self.actual.__exit__(*args)
    bindings={'_phase':phase,'_timed_call':timed_call,'_timed_context':TimedContext}
    native=NativeMapTransform()
    original_init=shared_memory.SharedMemory.__init__
    shared_memory.SharedMemory.__init__=instrument(original_init,native,bindings,proofs,'stdlib_shared_memory_init')
    if native.maps<1 or native.opens<1:raise RuntimeError('Linux SharedMemory native map/open boundary not found')
    verify=VerifyTransform();verified=instrument(fd_passing.verify_sealed_fd,verify,bindings,proofs,'verify_sealed_fd')
    if verify.maps!=1 or verify.reads!=1:raise RuntimeError('native memfd boundaries changed')
    consumer_bindings={**bindings,'verify_sealed_fd':verified,'_begin_transfer':recorder.begin,
        '_header_ready':recorder.header,'_body_start':recorder.body_start,'_body_end':recorder.body_end,
        '_finish_transfer':recorder.finish}
    transform=ConsumerTransform();original=instrument(baseline.consumer,transform,consumer_bindings,proofs,'original_consumer')
    if transform.receive!=1 or transform.attach!=1:raise RuntimeError('baseline consumer boundaries changed')
    try:original(sock)
    except BaseException as exc:
        error={'error_type':type(exc).__name__,'error_text_sha256':hashlib.sha256(str(exc).encode()).hexdigest()}
        if recorder.current and recorder.current.get('path') not in (None,'stop'):
            partial=dict(recorder.current);partial.pop('_origin',None);partial.update(success=False,phase_error=error,partial=True)
            recorder.rows.append(partial)
        raise
    finally:
        shared_memory.SharedMemory.__init__=original_init
        # All stage journaling and deep-size traversal happen after timed transfers stop.
        path=destination/f'raw/child_stage_batch_{batch}.jsonl';buffer_bytes=deep_size(recorder.rows)
        with path.open('x',encoding='utf-8') as stream:
            for row in recorder.rows:stream.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
            stream.flush();os.fsync(stream.fileno())
        save(destination/f'analysis/child_{batch}.json',{'status':'COMPLETED' if error is None else 'FAILED',
            'rows':len(recorder.rows),'consumer_pid':os.getpid(),'consumer_sha256':digest(path),'ast_proofs':proofs,
            'buffer_rows_max':ROWS_PER_BATCH,'stage_events_per_transfer_max':64,'buffer_deep_size_bytes_at_flush':buffer_bytes,
            'serialized_bytes':path.stat().st_size,'flush_after_timed_transfers':True,'error':error})


class Sampler:
    def __init__(self,destination,batch):self.destination=destination;self.batch=batch;self.done=threading.Event();self.error=None
    def sample(self):
        import psutil
        try:
            with (self.destination/f'raw/resources_batch_{self.batch}.jsonl').open('x') as stream:
                while not self.done.is_set():
                    processes=[];root=psutil.Process()
                    for process in [root]+root.children(recursive=True):
                        try:processes.append({'pid':process.pid,'rss':process.memory_info().rss,'cpu_sec':sum(process.cpu_times()[:2]),'fds':process.num_fds()})
                        except psutil.Error:pass
                    stream.write(json.dumps({'batch':self.batch,'monotonic_ns':time.perf_counter_ns(),'loadavg':os.getloadavg(),
                        'processes':processes,'sampling_interval_sec':.2})+'\n');stream.flush();self.done.wait(.2)
        except BaseException as exc:self.error=type(exc).__name__
    def __enter__(self):self.thread=threading.Thread(target=self.sample,daemon=True);self.thread.start();return self
    def __exit__(self,*args):
        self.done.set();self.thread.join()
        if self.error:raise RuntimeError('resource sampler failed: '+self.error)


def fd_targets():
    result={}
    for entry in Path('/proc/self/fd').iterdir():
        try:result[str(entry.name)]=os.readlink(entry)
        except FileNotFoundError:pass
    return result


def batch_worker(index,out,destination):
    import numpy as np
    before=source_identity(out);baseline=load_baseline(out);baseline.OUT=destination
    initial_fd=fd_targets();shm_before={p.name for p in Path('/dev/shm').iterdir()}
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM);parent.settimeout(60)
    process=mp.get_context('spawn').Process(target=consumer,args=(child,index,str(out),str(destination)))
    process.start();child.close();count=warmups=ordinal=0;error=None
    status={'status':'RUNNING','batch':index,'source_before':before,'producer_pid':os.getpid(),
        'fd_before':len(initial_fd),'fd_targets_before':initial_fd,'allowed_new_helper_fds_bound':5}
    producer_path=destination/f'raw/producer_batch_{index}.jsonl'
    try:
        with Sampler(destination,index),producer_path.open('x',encoding='utf-8') as stream:
            for size in SIZES:
                array=np.arange(size//4,dtype=np.float32);payload_sha=hashlib.sha256(memoryview(array).cast('B')).hexdigest()
                for mode in PATHS:
                    for condition in ('cold','hot'):
                        warm_obj=warm_desc=None;initial=0
                        if condition=='hot':
                            start=time.perf_counter_ns();warm_obj,warm_desc=baseline.materialize(mode,array);initial=time.perf_counter_ns()-start
                        try:
                            for iteration in range(-20,200):
                                row={'batch':index,'transfer_ordinal':ordinal,'path':mode,'condition':condition,'logical_bytes':size,
                                    'iteration':iteration,'warmup':iteration<0,'source_identity_sha256':EXPECTED_SOURCE,'payload_sha256':payload_sha}
                                obj=desc=None
                                try:
                                    wall=time.perf_counter_ns();cpu=time.process_time_ns();prep_start=time.perf_counter_ns()
                                    obj,desc=baseline.materialize(mode,array) if condition=='cold' else (warm_obj,warm_desc)
                                    preparation_ns=time.perf_counter_ns()-prep_start if condition=='cold' else 0
                                    response,handoff_ns,handoff_cpu_ns,socket_bytes=baseline.transfer(parent,mode,obj,desc,size,payload_sha)
                                    release_start=time.perf_counter_ns()
                                    if condition=='cold':baseline.release(mode,obj);obj=None
                                    release_ns=time.perf_counter_ns()-release_start if condition=='cold' else 0
                                    end=time.perf_counter_ns();cpu_ns=time.process_time_ns()-cpu
                                    row.update(producer_pid=os.getpid(),producer_prepare_ns=preparation_ns,handoff_roundtrip_ns=handoff_ns,
                                        producer_release_ns=release_ns,task_wall_ns=end-wall,producer_cpu_ns=cpu_ns,
                                        handoff_producer_cpu_ns=handoff_cpu_ns,hot_one_time_setup_ns=initial,
                                        local_materialization_bytes=size if condition=='cold' else 0,socket_observed_bytes=socket_bytes,
                                        fd_count_sent=int(mode=='memfd'),producer_fd_count=len(os.listdir('/proc/self/fd')),
                                        producer_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                                        observed_ack_fields=sorted(response),
                                        timing_partition='Producer preparation/handoff RTT/release; child post-header phases nested and never added. Common receive wait may start before parent handoff.',
                                        **response)
                                    if sorted(response)!=sorted(ACK_FIELDS):raise RuntimeError('original ACK schema changed')
                                    if not response['success']:raise AssertionError('consumer failed actual state verification')
                                except BaseException as exc:
                                    row.update(success=False,partial=True,error_type=type(exc).__name__,
                                        error_text_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
                                    raise
                                finally:
                                    # Preserve warmup identities too, after the original timed boundary.
                                    stream.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n');ordinal+=1
                                    if condition=='cold' and obj is not None:baseline.release(mode,obj)
                                count+=int(iteration>=0);warmups+=int(iteration<0)
                            stream.flush()
                        finally:
                            if warm_obj is not None:baseline.release(mode,warm_obj)
            stream.flush();os.fsync(stream.fileno())
    except BaseException as exc:
        error={'error_type':type(exc).__name__,'error_text_sha256':hashlib.sha256(str(exc).encode()).hexdigest()}
        raise
    finally:
        with contextlib.suppress(OSError):
            stop=json.dumps({'mode':'stop'}).encode();parent.sendall(struct.pack('!I',len(stop))+stop)
        # Transfers have stopped; allow the bounded phase buffer to flush before cleanup.
        parent.close();process.join(60)
        if process.is_alive():process.terminate();process.join(5)
        if process.is_alive():process.kill();process.join()
        status['consumer_exit']=process.exitcode;process.close()
        after=source_identity(out,require=False);remaining=fd_targets()
        new_targets=list((Counter(remaining.values())-Counter(initial_fd.values())).elements())
        from multiprocessing import resource_tracker
        tracker_fd=getattr(resource_tracker._resource_tracker,'_fd',None)
        known_helpers={'resource_tracker':remaining[str(tracker_fd)]} if str(tracker_fd) in remaining else {}
        residue=sorted({p.name for p in Path('/dev/shm').iterdir()}-shm_before)
        child_path=destination/f'raw/child_stage_batch_{index}.jsonl';child_status_path=destination/f'analysis/child_{index}.json'
        child_status=json.loads(child_status_path.read_text()) if child_status_path.is_file() else {}
        cleanup_ok=not residue and len(new_targets)<=5 and all(target.startswith('pipe:[') and target in known_helpers.values() for target in new_targets)
        status.update(status='COMPLETED' if error is None and status['consumer_exit']==0 and cleanup_ok and before==after and child_status.get('status')=='COMPLETED' else 'FAILED',
            measured_rows=count,warmup_rows=warmups,producer_rows=ordinal,consumer_rows=child_status.get('rows'),source_after=after,
            source_stable=before==after,shm_residue=residue,fd_after_process_close=len(remaining),fd_targets_after=remaining,
            new_fd_targets=new_targets,known_helper_fd_targets=known_helpers,cleanup_ok=cleanup_ok,
            producer_sha256=digest(producer_path) if producer_path.is_file() else None,
            consumer_sha256=digest(child_path) if child_path.is_file() else None,
            resource_sha256=digest(destination/f'raw/resources_batch_{index}.jsonl') if (destination/f'raw/resources_batch_{index}.jsonl').is_file() else None,
            child_status_sha256=digest(child_status_path) if child_status_path.is_file() else None,error=error)
        save(destination/f'analysis/batch_{index}.json',status)
        if status['status']!='COMPLETED' and error is None:raise RuntimeError('batch cleanup/consumer failed')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(DEFAULT_OUT));parser.add_argument('--destination',required=True)
    parser.add_argument('--registration');parser.add_argument('--worker-batch',type=int,choices=(1,2,3));args=parser.parse_args()
    out=Path(args.out).resolve();destination=Path(args.destination).resolve()
    if args.worker_batch:
        control=json.loads((destination/'manifest.json').read_text())
        nonce=os.environ.get('STATE_PHASE_V2_WORKER_NONCE','')
        if control.get('status')!='RUNNING' or control.get('controller_pid')!=os.getppid() or not nonce or \
                hashlib.sha256(nonce.encode()).hexdigest()!=control.get('worker_nonce_sha256') or digest(__file__)!=control.get('driver_sha256'):
            raise RuntimeError('worker requires its live registered controller; no independent worker execution')
        batch_worker(args.worker_batch,out,destination);return
    registration=Path(args.registration) if args.registration else out/REGISTRATION
    rules=json.loads(registration.read_text(encoding='utf-8'))
    if rules.get('status')!='PREPARED_NOT_EXECUTED' or digest(__file__)!=rules['driver_sha256']:
        raise RuntimeError('registered driver identity/status differs')
    if rules['relative_output_root']!='state_measurement_correction_v2' or destination!=out/rules['relative_output_root'] or destination.exists():
        raise ValueError('registered fresh independent destination required')
    if not destination.is_relative_to(out) or destination==out:raise ValueError('destination must stay within registered OUT')
    locks=[]
    try:
        for relative in ('analysis/postrun_verification.lock','manifests/real_provider_execution.lock'):
            lock=(out/relative).open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
        ready_path=out/'logs/mechanisms/formal_complete_sequence_v1.result.json';ready=json.loads(ready_path.read_text())
        if ready.get('exit_code')!=0 or ready.get('timed_out'):raise RuntimeError('original real+pressure sequence has not closed successfully')
        for relative,expected in rules['registered_input_sha256'].items():
            if digest(out/relative)!=expected:raise RuntimeError('registered original input identity changed')
        analyzer=Path(__file__).with_name('formal_state_measurement_correction_v2_analyze.py')
        helper=Path(__file__).with_name('formal_postrun_verify.py')
        if digest(analyzer)!=rules['analyzer_sha256'] or digest(helper)!=rules['execution_helper_sha256']:
            raise RuntimeError('registered analyzer/controller helper identity changed')
        before=source_identity(out)
        destination.mkdir()
        for relative in ('raw','analysis','logs'): (destination/relative).mkdir()
        (destination/'prospective_registration.json').write_bytes(registration.read_bytes())
        for name,schema in rules['raw_schemas'].items():save(destination/'analysis'/(name+'.schema.json'),schema)
        nonce=os.urandom(32).hex()
        save(destination/'manifest.json',{'schema_version':1,'status':'RUNNING','created_utc':utc(),'registration_sha256':digest(registration),
            'driver_sha256':digest(__file__),'analyzer_sha256':digest(analyzer),'source_identity_sha256':EXPECTED_SOURCE,
            'controller_pid':os.getpid(),'worker_nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest(),
            'runtime':{'python':sys.version,'python_executable':sys.executable,'numpy':version('numpy'),'psutil':version('psutil'),
                'perf_counter':vars(time.get_clock_info('perf_counter')),'kernel_scope':'openEuler userspace on shared WSL2 kernel; same original environment'},
            'source_before':before,'paid_api_calls':0,'prior_state_matrix_policy':'Old full-path matrix remains valid and separate; no pooling or retrospective phase inference.',
            'planned_measured_rows':25200,'planned_warmup_rows':2520,'planned_total_transfers':27720,
            'instrumentation_overhead':'NOT_MEASURED_SEPARATELY; all observed overhead retained without subtraction. Timestamp/phase-buffer work and warmup journals add recording work; process RSS includes the bounded child buffer.'})
        from formal_postrun_verify import enable_child_subreaper,execute_step
        result={'status':'RUNNING','created_utc':utc(),'prospective_registration_sha256':digest(registration),'source_before':before,
            'source_identity_sha256':EXPECTED_SOURCE,'paid_api_calls':0,'steps':[],'batches':3,'automatic_retry':False,
            'real_pressure_ready_sha256':digest(ready_path),'child_subreaper':enable_child_subreaper()}
        persist=lambda:save(destination/'analysis/execution.json',result)
        persist()
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':'/drivers:'+str(SOURCE/'src'),
            'STATE_PHASE_V2_WORKER_NONCE':nonce}
        for key in list(env):
            if any(token in key.upper() for token in ('API_KEY','API_TOKEN','ACCESS_TOKEN','AUTH_TOKEN','PASSWORD','SECRET')):env.pop(key,None)
        try:
            for index in range(1,4):
                command=[sys.executable,str(Path(__file__).resolve()),'--out',str(out),'--destination',str(destination),'--worker-batch',str(index)]
                step={'name':f'batch_{index}','command':command,'started_utc':utc()};result['steps'].append(step)
                execute_step(command,env,destination/'logs',step,rules['batch_timeout_sec'],persist)
            batches=[json.loads((destination/f'analysis/batch_{i}.json').read_text()) for i in (1,2,3)]
            if any(b.get('status')!='COMPLETED' or b.get('measured_rows')!=8400 or b.get('warmup_rows')!=840 or b.get('producer_rows')!=9240 or b.get('consumer_rows')!=9240 for b in batches):
                raise RuntimeError('closed batch coverage differs from registered full matrix')
            result.update(status='COMPLETED',measured_rows=25200,warmup_rows=2520,total_transfers=27720)
        except BaseException as exc:
            result.update(status='FAILED_RETAINED_NO_RETRY',error_type=type(exc).__name__,error_text_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
            raise
        finally:
            after=source_identity(out,require=False)
            if before!=after:result['status']='FAILED_SOURCE_CHANGED_RETAINED_NO_RETRY'
            input_after={relative:digest(out/relative) for relative in rules['registered_input_sha256']}
            if input_after!=rules['registered_input_sha256']:result['status']='FAILED_REGISTERED_INPUT_CHANGED_RETAINED_NO_RETRY'
            result.update(source_after=after,source_stable=before==after,finished_utc=utc())
            result.update(registered_input_sha256_after=input_after,registered_inputs_stable=input_after==rules['registered_input_sha256'])
            persist();manifest=json.loads((destination/'manifest.json').read_text())
            manifest.update(status=result['status'],source_after=after,source_stable=before==after,execution_sha256=digest(destination/'analysis/execution.json'))
            save(destination/'manifest.json',manifest)
        if result['status']!='COMPLETED':raise RuntimeError('state supplement did not complete; retained failure evidence')
    finally:
        for lock in reversed(locks):lock.close()


if __name__=='__main__':main()
