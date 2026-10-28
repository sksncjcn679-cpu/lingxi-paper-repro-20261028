"""Prospective per-URI and index measurements on the unmodified business source.

No payload/response repair. Native ndarray read+matmul is a fused operation and is
named accordingly. Owner mmap creation and existing-map ndarray views differ.
Child traces append whitelisted timing metadata directly to a trace file, adding
no observer RPC to the Agent workload. Raw RPC contents are never retained here.
"""
from __future__ import annotations
import ast
import copy
from dataclasses import asdict,is_dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import textwrap
import threading
import time
import types

VERSION='uri-index-observer-candidate-v1'
PREFIX='_papertrace_'


def array_identity(value):
    import numpy as np
    a=np.asarray(value)
    result={'shape':list(a.shape),'dtype':str(a.dtype),'nbytes':int(a.nbytes)}
    if a.flags.c_contiguous and not a.dtype.hasobject:
        result['payload_sha256']=hashlib.sha256(memoryview(a).cast('B')).hexdigest()
        result['payload_hash_scope']='instrumentation-only read after native operation; overhead included in task wall'
    else:result['payload_sha256']=None
    return result


class _StripTrace(ast.NodeTransformer):
    def visit_Assign(self,node):
        if all(isinstance(t,ast.Name) and t.id.startswith(PREFIX) for t in node.targets):return None
        return self.generic_visit(node)
    def visit_Expr(self,node):
        if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id.startswith(PREFIX):return None
        return self.generic_visit(node)


def consumption_clone(original,emit):
    """Instrument the two actual native expressions; strip-to-original AST gate."""
    source=textwrap.dedent(inspect.getsource(original));baseline=ast.parse(source)
    if any(isinstance(n,ast.Name) and n.id.startswith(PREFIX) for n in ast.walk(baseline)):
        raise RuntimeError('observer local-name collision')
    tree=copy.deepcopy(baseline);counts={'matmul':0,'sort':0,'reorder':0}
    class Add(ast.NodeTransformer):
        def visit_Assign(self,node):
            self.generic_visit(node)
            if len(node.targets)!=1:return node
            target=node.targets[0]
            if isinstance(target,ast.Name) and target.id=='sims' and isinstance(node.value,ast.BinOp) and isinstance(node.value.op,ast.MatMult):
                counts['matmul']+=1
                return ast.parse('_papertrace_begin = _papertrace_clock()').body+[node]+ast.parse('_papertrace_dot_end = _papertrace_clock()').body
            if isinstance(target,ast.Name) and target.id=='order':
                counts['sort']+=1
                return [node]+ast.parse('_papertrace_sort_end = _papertrace_clock()').body
            if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id=='dag' and isinstance(target.slice,ast.Constant) and target.slice.value=='operands':
                counts['reorder']+=1
                addition="""
_papertrace_end = _papertrace_clock()
_papertrace_emit(ref, _papertrace_begin, _papertrace_dot_end, _papertrace_sort_end, _papertrace_end, [int(i) for i in order], list(dag['operands']))
"""
                return [node]+ast.parse(addition).body
            return node
    tree=Add().visit(tree);ast.fix_missing_locations(tree)
    if counts!={'matmul':1,'sort':1,'reorder':1}:raise RuntimeError('native consumer expression shape changed')
    stripped=_StripTrace().visit(copy.deepcopy(tree));ast.fix_missing_locations(stripped)
    if ast.dump(stripped,include_attributes=False)!=ast.dump(baseline,include_attributes=False):
        raise RuntimeError('consumer instrumentation changes native business AST')
    namespace=dict(original.__globals__)
    namespace.update(_papertrace_clock=time.perf_counter_ns,_papertrace_emit=emit)
    code=ast.unparse(tree)+'\n'
    exec(compile(tree,'<paper-uri-consumption-instrumentation>','exec'),namespace)
    function=namespace[original.__name__]
    function.__qualname__=original.__qualname__;function.__module__=original.__module__
    function.__annotations__=original.__annotations__
    return function,{'native_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                     'instrumented_source_sha256':hashlib.sha256(code.encode()).hexdigest(),
                     'stripped_ast_identical':True,'expression_counts':counts,'instrumented_source':code}


class Observer:
    def __init__(self,ctx,recorder,memory=None,sockets=None,enabled=True):
        self.ctx,self.recorder,self.memory,self.sockets=ctx,recorder,memory,sockets
        self.enabled=enabled;self.owner=os.getpid();self.restores=[];self.local=threading.local()
        self.sequence=0;self.maps=0;self.pending_rpc=None;self.child_fd=None;self.identities={};self.spec={}
        self.index_counts={'search':0,'add':0,'remove':0}
    def emit(self,event,**fields):
        if not self.enabled:return
        row={'event':event,'pid':os.getpid(),'monotonic_ns':time.perf_counter_ns(),
             'observer_version':VERSION,**fields}
        if os.getpid()==self.owner:
            self.recorder.add('states.jsonl',row)
        elif self.child_fd is not None:
            data=(json.dumps({**self.recorder.common,**row},ensure_ascii=False,allow_nan=False)+'\n').encode()
            # One file append, no RPC; only explicit metadata from this module is passed.
            if os.write(self.child_fd,data)!=len(data):raise RuntimeError('partial child state trace write')
    def consumption(self,uri,start,dot_end,sort_end,end,order,operands):
        self.emit('state_consumer_compute',uri=uri,consumer_pid=os.getpid(),consumer_role='retriever',
                  start_ns=start,end_ns=end,read_similarity_compute_ns=dot_end-start,
                  stable_sort_ns=sort_end-dot_end,reorder_ns=end-sort_end,decision_order=order,
                  ordered_operands=operands,read_and_compute_fused=True,
                  read_boundary='native op_vecs @ np.asarray(embedding, dtype=float32); includes cast/copy if native requires it',
                  instrumentation_file_append_after_measured_end=True)
    def replace(self,obj,name,new):
        self.restores.append((obj,name,getattr(obj,name)));setattr(obj,name,new)
    def wrap(self,obj,name,operation,callback=None,uri_arg=False,category='state'):
        original=getattr(obj,name)
        def invoke(*args,**kwargs):
            uri=args[0] if uri_arg and args and isinstance(args[0],str) else None
            started=ended=None;error=None;value=None
            try:
                with self.recorder.span(category,operation,uri=uri):
                    started=time.perf_counter_ns()
                    try:value=original(*args,**kwargs)
                    finally:ended=time.perf_counter_ns()
                return value
            except BaseException as exc:error=type(exc).__name__;raise
            finally:
                if callback:callback(args,kwargs,value,started,ended,error)
        self.replace(obj,name,invoke)
    def __enter__(self):
        if not self.enabled:return self
        trace=self.recorder.out/'raw/states.jsonl'
        self.child_fd=os.open(trace,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        from agent_runtime.agents.retriever import RetrieverAgent
        try:
            clone,self.spec['consumer_clone']=consumption_clone(RetrieverAgent._resolve_plan,self.consumption)
            self.replace(RetrieverAgent,'_resolve_plan',clone)
            self.install_mmap_probe();self.install_state();self.install_rpc();self.install_index()
        except BaseException:
            self.__exit__(None,None,None)
            raise
        self.recorder.add('observer_identity.jsonl',{'observer_version':VERSION,'specification':self.spec,
            'observer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'child_trace_path':'raw/states.jsonl','raw_rpc_body_recorded':False})
        return self
    def install_mmap_probe(self):
        import multiprocessing.shared_memory as shared
        original=shared.SharedMemory.__init__;module=original.__globals__['mmap'];observer=self
        class MmapProxy:
            def __getattr__(self,name):return getattr(module,name)
            def mmap(self,*args,**kwargs):
                start=time.perf_counter_ns();result=module.mmap(*args,**kwargs);end=time.perf_counter_ns()
                current=getattr(observer.local,'buffer_key',None)
                if current is not None:
                    observer.maps+=1
                    observer.emit('state_os_mapping',uri=current.split('#',1)[0],buffer_key=current,
                                  start_ns=start,end_ns=end,mapping_ns=end-start,
                                  mapped_virtual_bytes=len(result),mapping_owner_pid=os.getpid(),
                                  boundary='native mmap.mmap inside SharedMemory constructor; excludes shm_open/ftruncate and data copy')
                return result
        globals_copy=dict(original.__globals__);globals_copy['mmap']=MmapProxy()
        clone=types.FunctionType(original.__code__,globals_copy,original.__name__,original.__defaults__,original.__closure__)
        clone.__kwdefaults__=original.__kwdefaults__;clone.__annotations__=original.__annotations__
        self.replace(shared.SharedMemory,'__init__',clone)
        self.spec['mapping_probe']={'original_constructor_source_sha256':hashlib.sha256(inspect.getsource(original).encode()).hexdigest(),
                                    'constructor_code_object_identical':clone.__code__ is original.__code__,
                                    'only_constructor_global_substitution':'mmap module proxy, all attributes delegated; mmap call timed'}
    def install_state(self):
        exchange=self.ctx.state_exchange;buffer=exchange._buffer
        original_put=buffer.put
        def put(key,array):
            old=getattr(self.local,'buffer_key',None);self.local.buffer_key=key
            maps_before=self.maps;start=end=None;error=None;written=None
            try:
                with self.recorder.span('state','state_buffer_materialize',uri=key.split('#',1)[0]):
                    start=time.perf_counter_ns()
                    try:written=original_put(key,array)
                    finally:end=time.perf_counter_ns()
                return written
            except BaseException as exc:error=type(exc).__name__;raise
            finally:
                self.local.buffer_key=old
                descriptor=buffer.descriptor(key) if buffer.exists(key) else None
                details=array_identity(array)
                self.identities[key]=details
                self.emit('state_buffer_materialize',uri=key.split('#',1)[0],buffer_key=key,
                          start_ns=start,end_ns=end,materialize_ns=end-start,local_copied_bytes=written,
                          error_type=error,array=details,descriptor=asdict(descriptor) if descriptor else None,
                          mapping_event_count=self.maps-maps_before,
                          mapping_status='NEW_NATIVE_MAPPING_OBSERVED' if self.maps>maps_before else 'NO_NEW_MAP_EXISTING_ARENA_OR_INPROC',
                          boundary='native SharedVectorBuffer.put includes allocation/view construction and copy; payload checksum calculated after measured end')
        self.replace(buffer,'put',put)
        def viewed(args,kwargs,value,start,end,error):
            key=args[0]
            self.emit('state_view_construct',uri=key.split('#',1)[0],buffer_key=key,start_ns=start,end_ns=end,
                      view_construct_ns=end-start,new_os_mapping=False,error_type=error,
                      shape=list(value.shape) if value is not None else None,
                      dtype=str(value.dtype) if value is not None else None,
                      boundary='native read-only ndarray view on already mapped owner buffer; not payload traversal')
        self.wrap(buffer,'get_view','state_view_construct',viewed,uri_arg=True)
        def produced(args,kwargs,value,start,end,error):
            ref=asdict(value) if is_dataclass(value) else None
            self.emit('state_produce',uri=ref.get('uri') if ref else None,start_ns=start,end_ns=end,
                      prepare_ns=end-start,reference=ref,error_type=error,
                      boundary='native whole producer; inner materialization/mapping spans overlap and must not be added')
        for name in ('put_plan_state','embed_text','put_versioned_state','update_versioned_state'):
            self.wrap(exchange,name,name,produced)
        def received(args,kwargs,value,start,end,error):
            uri=args[0];ref=exchange.get_ref(uri) or exchange.get_versioned_ref(uri)
            self.emit('state_resolve',uri=uri,start_ns=start,end_ns=end,resolve_ns=end-start,
                      reference=asdict(ref) if ref is not None else None,error_type=error,
                      consumer_service_pid=os.getpid(),boundary='runtime service resolve/decode; direct views may not touch all payload bytes',
                      native_integrity_status='native verified versioned ledger' if uri.startswith('vstate://') and error is None else 'content-addressed metadata; no extra integrity enforcement added')
        for name in ('get_plan_state','get_vector','get_versioned_state'):
            self.wrap(exchange,name,name,received,uri_arg=True)
        original_delete=buffer.delete
        def delete(key):
            start=time.perf_counter_ns();result=original_delete(key);end=time.perf_counter_ns()
            self.emit('state_buffer_release',uri=key.split('#',1)[0],buffer_key=key,start_ns=start,end_ns=end,
                      release_ns=end-start,buffer_key_absent=not buffer.exists(key),
                      boundary='native delete; arena physical segment may remain for other keys')
            return result
        self.replace(buffer,'delete',delete)
        original_close=buffer.close
        def close():
            keys=sorted(set(buffer._arena_objs)|set(buffer._shm)|set(buffer._inproc))
            start=time.perf_counter_ns();result=original_close();end=time.perf_counter_ns()
            # One real close span shared by all released URI components, no duplicated sum.
            group=f"buffer-close-{self.owner}-{start}"
            for key in keys:
                self.emit('state_buffer_release',uri=key.split('#',1)[0],buffer_key=key,start_ns=start,end_ns=end,
                          shared_release_group=group,shared_group_release_ns=end-start,
                          individual_uri_release_ns=None,buffer_key_absent=not buffer.exists(key),
                          boundary='one native buffer.close releases all keys; shared timing must be counted once')
            return result
        self.replace(buffer,'close',close)
    def install_rpc(self):
        import agent_runtime.runtime.agent_host as host_module
        observer=self;original_serve=host_module.AgentProcessHost._serve
        original_run=host_module.AgentProcessHost.run;original_send=host_module.send_frame
        def run(host,name,message,ctx):
            old=getattr(observer.local,'rpc_consumer',None)
            observer.local.rpc_consumer={'consumer_role':name,'consumer_pid':host._workers[name].pid}
            try:return original_run(host,name,message,ctx)
            finally:observer.local.rpc_consumer=old;observer.pending_rpc=None
        def serve(host,ctx,op,path,args):
            observer.pending_rpc=None
            safe_uri=None;method=None
            if op=='call' and path and all(isinstance(p,tuple) and len(p)==2 for p in path):
                names=[p[1] for p in path if p[0]=='a']
                if len(names)>=2 and names[-2]=='state_exchange' and names[-1] in ('get_plan_state','get_vector','get_versioned_state'):
                    positional=args[0]
                    if positional and isinstance(positional[0],str) and positional[0].startswith(('state://','vstate://','vector://')):
                        safe_uri=positional[0];method=names[-1]
            start=time.perf_counter_ns()
            result=original_serve(host,ctx,op,path,args)
            if safe_uri:
                observer.pending_rpc={'uri':safe_uri,'method':method,'service_start_ns':start,
                                      'service_end_ns':time.perf_counter_ns(),**(getattr(observer.local,'rpc_consumer',None) or {})}
            return result
        def send(sock,payload,**kwargs):
            start=time.perf_counter_ns();result=original_send(sock,payload,**kwargs);end=time.perf_counter_ns()
            pending=observer.pending_rpc
            if os.getpid()==observer.owner and pending is not None:
                observer.emit('state_rpc_handoff',**pending,send_start_ns=start,end_ns=end,
                    handoff_parent_service_and_reply_ns=end-pending['service_start_ns'],
                    reply_send_ns=end-start,reply_payload_bytes=len(payload),reply_framed_socket_bytes=len(payload)+4,
                    boundary='successful Runtime state-service call through reply-frame send; excludes child receive/decode and waiting before service',
                    bytes_policy='one tagged state reply at Runtime endpoint, already included in task socket total; not additive to that total')
                observer.pending_rpc=None
            return result
        self.replace(host_module.AgentProcessHost,'run',run)
        self.replace(host_module.AgentProcessHost,'_serve',serve)
        self.replace(host_module,'send_frame',send)
    def install_index(self):
        memory=self.memory
        if memory is None:return
        self.local.memory_operation=None
        for operation in ('retrieve','write','record_feedback','mark_used'):
            original=getattr(memory,operation)
            def outer(*args,_original=original,_operation=operation,**kwargs):
                previous=getattr(self.local,'memory_operation',None);self.local.memory_operation=_operation
                try:return _original(*args,**kwargs)
                finally:self.local.memory_operation=previous
            self.replace(memory,operation,outer)
        index=memory.index;self.spec['memory_index_class']=type(index).__module__+'.'+type(index).__qualname__
        for operation in ('search','add','remove'):
            if not hasattr(index,operation):continue
            original=getattr(index,operation)
            def indexed(*args,_original=original,_operation=operation,**kwargs):
                start=end=cpu=cpu_end=None;error=None
                try:
                    with self.recorder.span('memory_index',f'vector_index_{_operation}'):
                        start=time.perf_counter_ns();cpu=time.process_time_ns()
                        try:return _original(*args,**kwargs)
                        finally:cpu_end=time.process_time_ns();end=time.perf_counter_ns()
                except BaseException as exc:error=type(exc).__name__;raise
                finally:
                    self.index_counts[_operation]+=1
                    self.recorder.add('memory_events.jsonl',{'event':'index_operation','operation':_operation,
                        'parent_memory_operation':getattr(self.local,'memory_operation',None),
                        'start_ns':start,'end_ns':end,'index_latency_ns':end-start,'cpu_ns':cpu_end-cpu,
                        'pid':os.getpid(),'error_type':error,'index_class':self.spec['memory_index_class'],
                        'boundary':'native vector index method only; embedding, SQLite, candidate merging and reranking excluded'})
            self.replace(index,operation,indexed)
    def __exit__(self,*exc):
        if not self.enabled:return
        for obj,name,old in reversed(self.restores):setattr(obj,name,old)
        self.restores.clear()
        if self.child_fd is not None:os.close(self.child_fd);self.child_fd=None
        self.recorder.add('observer_summary.jsonl',{'observer_version':VERSION,'mapping_events':self.maps,
            'index_operation_counts':self.index_counts,'all_hooks_restored':True,
            'read_pure_latency':'NOT_SEPARABLE_FROM_NATIVE_SIMILARITY_COMPUTATION; fused span measured',
            'no_new_mapping_is_not_zero_mapping_latency':True})

