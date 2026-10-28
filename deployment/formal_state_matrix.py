"""Protocol 4.2 state paths: common real AF_UNIX consumer, 7 sizes, 200 trials, 3 batches."""
from dataclasses import asdict
from pathlib import Path
import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import resource
import socket
import struct
import sys
import threading
import time
import traceback
import numpy as np
import psutil

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.state_exchange.shared_buffer import SharedVectorBuffer, SharedArrayDescriptor
from agent_runtime.state_exchange.fd_passing import SealedSegment,verify_sealed_fd

OUT=Path('/evidence/formal_20261019')
SIZES=[1024,4096,16384,65536,262144,1048576,4194304]
PATHS=['copy','shm','memfd']


def exact(sock,size,prefix=b''):
    result=bytearray(size);have=min(len(prefix),size);result[:have]=prefix[:have]
    while have<size:
        got=sock.recv_into(memoryview(result)[have:])
        if not got:raise EOFError('incomplete frame')
        have+=got
    return result


def consumer(sock):
    sock.settimeout(60)
    try:
        while True:
            first,fds,flags,address=socket.recv_fds(sock,65536,4)
            if not first:break
            if len(first)<4:first+=exact(sock,4-len(first))
            hlen=struct.unpack('!I',first[:4])[0]
            head=first[4:]
            if len(head)<hlen:head+=exact(sock,hlen-len(head))
            header=json.loads(head[:hlen]);rest=head[hlen:]
            if header['mode']=='stop':break
            start=time.perf_counter_ns();cpu=time.process_time_ns()
            try:
                if header['mode']=='copy':
                    payload=exact(sock,header['size'],rest)
                    ok=hashlib.sha256(payload).hexdigest()==header['sha256']
                    read_mode='socket_copy'
                elif header['mode']=='shm':
                    desc=header['descriptor'];desc['shape']=tuple(desc['shape'])
                    with SharedVectorBuffer.attach_readonly(SharedArrayDescriptor(**desc)) as view:
                        raw=memoryview(view).cast('B')
                        ok=hashlib.sha256(raw).hexdigest()==header['sha256']
                        raw.release()
                    read_mode='named_shm_readonly'
                else:
                    check=verify_sealed_fd(fds[0],header['size'],header['sha256'])
                    ok=check['ok'];read_mode=check.get('read_mode','unsupported')
                elapsed=time.perf_counter_ns()-start;cpu_ns=time.process_time_ns()-cpu
                response={'success':bool(ok),'consumer_pid':os.getpid(),'receiver_read_verify_ns':elapsed,
                    'receiver_cpu_ns':cpu_ns,'read_mode':read_mode,'consumer_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                    'consumer_fd_count':len(os.listdir('/proc/self/fd'))}
            finally:
                for fd in fds:os.close(fd)
            encoded=json.dumps(response,separators=(',',':')).encode()
            sock.sendall(struct.pack('!I',len(encoded))+encoded)
    finally:sock.close()


class Sampler:
    def __init__(self,batch):
        self.batch=batch;self.done=threading.Event();self.thread=threading.Thread(target=self.sample,daemon=True)
    def sample(self):
        with (OUT/f'raw/state_resources_batch_{self.batch}.jsonl').open('x') as f:
            while not self.done.is_set():
                root=psutil.Process();processes=[]
                for p in [root]+root.children(recursive=True):
                    try:processes.append({'pid':p.pid,'rss':p.memory_info().rss,'cpu_sec':sum(p.cpu_times()[:2]),'fds':p.num_fds()})
                    except psutil.Error:pass
                f.write(json.dumps({'batch':self.batch,'monotonic_ns':time.perf_counter_ns(),'loadavg':os.getloadavg(),'processes':processes,'sampling_interval_sec':.2})+'\n');f.flush()
                self.done.wait(.2)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):self.done.set();self.thread.join()


def materialize(mode,array):
    if mode=='copy':return array.tobytes(),None
    if mode=='memfd':return SealedSegment.create(array.tobytes()),None
    buffer=SharedVectorBuffer(True,arena=False);nbytes=buffer.put('payload',array)
    descriptor=buffer.descriptor('payload')
    if not descriptor:buffer.close();raise RuntimeError('SHM backend degraded')
    return buffer,asdict(descriptor)


def release(mode,obj):
    if mode!='copy':obj.close()


def transfer(sock,mode,obj,desc,size,digest):
    header={'mode':mode,'size':size,'sha256':digest}
    if desc:header['descriptor']=desc
    encoded=json.dumps(header,separators=(',',':')).encode();frame=struct.pack('!I',len(encoded))+encoded
    start=time.perf_counter_ns();cpu=time.process_time_ns()
    sent=socket.send_fds(sock,[frame],[obj.fd] if mode=='memfd' else [])
    if sent!=len(frame):raise RuntimeError('short descriptor header write')
    if mode=='copy':sock.sendall(obj);sent+=len(obj)
    alen=struct.unpack('!I',exact(sock,4))[0];response=json.loads(exact(sock,alen))
    return response,time.perf_counter_ns()-start,time.process_time_ns()-cpu,sent+4+alen


def batch(index):
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM);parent.settimeout(60)
    process=mp.get_context('spawn').Process(target=consumer,args=(child,));process.start();child.close()
    shm_before=set(p.name for p in Path('/dev/shm').iterdir());fd_before=len(os.listdir('/proc/self/fd'))
    count=0
    try:
        with Sampler(index),(OUT/f'raw/state_paths_batch_{index}.jsonl').open('x') as f:
            for size in SIZES:
                array=np.arange(size//4,dtype=np.float32);digest=hashlib.sha256(memoryview(array).cast('B')).hexdigest()
                for mode in PATHS:
                    # A cold result includes production and release. Hot rows reuse the same
                    # already materialized immutable state and record its one-time setup apart.
                    for condition in ['cold','hot']:
                        warm_obj=warm_desc=None;initial=0
                        if condition=='hot':
                            start=time.perf_counter_ns();warm_obj,warm_desc=materialize(mode,array);initial=time.perf_counter_ns()-start
                        try:
                            for iteration in range(-20,200):
                                wall=time.perf_counter_ns();cpu=time.process_time_ns()
                                prep_start=time.perf_counter_ns()
                                obj,desc=materialize(mode,array) if condition=='cold' else (warm_obj,warm_desc)
                                preparation_ns=time.perf_counter_ns()-prep_start if condition=='cold' else 0
                                response,handoff_ns,handoff_cpu_ns,socket_bytes=transfer(parent,mode,obj,desc,size,digest)
                                release_start=time.perf_counter_ns()
                                if condition=='cold':release(mode,obj)
                                release_ns=time.perf_counter_ns()-release_start if condition=='cold' else 0
                                end=time.perf_counter_ns();cpu_ns=time.process_time_ns()-cpu
                                if not response['success']:raise AssertionError('consumer failed actual state verification')
                                if iteration<0:continue
                                row={'batch':index,'path':mode,'condition':condition,'logical_bytes':size,'iteration':iteration,
                                    'producer_pid':os.getpid(),'producer_prepare_ns':preparation_ns,'handoff_roundtrip_ns':handoff_ns,
                                    'producer_release_ns':release_ns,'task_wall_ns':end-wall,'producer_cpu_ns':cpu_ns,
                                    'handoff_producer_cpu_ns':handoff_cpu_ns,'hot_one_time_setup_ns':initial,
                                    'local_materialization_bytes':size if condition=='cold' else 0,
                                    'socket_observed_bytes':socket_bytes,'fd_count_sent':int(mode=='memfd'),
                                    'producer_fd_count':len(os.listdir('/proc/self/fd')),'payload_sha256':digest,
                                    'producer_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                                    'timing_partition':'producer preparation + handoff RTT + release; child time nested, never added',
                                    'byte_boundary':'AF_UNIX stream payload/framing/ACK; excludes kernel ancillary bookkeeping',**response}
                                f.write(json.dumps(row)+'\n');count+=1
                            f.flush()
                        finally:
                            if warm_obj is not None:release(mode,warm_obj)
                    print(json.dumps({'batch':index,'size':size,'path':mode,'rows':count}),flush=True)
            f.flush();os.fsync(f.fileno())
    finally:
        with contextlib.suppress(OSError):
            stop=json.dumps({'mode':'stop'}).encode();parent.sendall(struct.pack('!I',len(stop))+stop)
        parent.close();process.join(10)
        if process.is_alive():process.terminate();process.join()
    residue=sorted(set(p.name for p in Path('/dev/shm').iterdir())-shm_before)
    result={'batch':index,'measured_rows':count,'planned_rows':8400,'consumer_exit':process.exitcode,
        'shm_residue':residue,'fd_before':fd_before,'fd_after':len(os.listdir('/proc/self/fd')),
        'fd_note':'process sentinel descriptors retained until Process.close; compare after close below'}
    process.close();result['fd_after_process_close']=len(os.listdir('/proc/self/fd'))
    (OUT/f'analysis/state_batch_{index}.json').write_text(json.dumps(result,indent=2))
    if residue or result['consumer_exit']!=0:raise RuntimeError('state consumer exit or cleanup failure')
    return count


if __name__=='__main__':
    plan={'protocol_section':'4.2 state paths','sizes':SIZES,'paths':PATHS,'repeat':200,'warmup':20,
        'batches':3,'first_and_hot_separate':True,'planned_rows':25200,'consumer':'same AF_UNIX worker across all three paths',
        'mock_scope':'synthetic numeric transport state; no provider/model quality claims'}
    (OUT/'manifests/state_matrix_plan.json').write_text(json.dumps(plan,indent=2))
    try:
        total=sum(batch(i) for i in range(1,4));status={'status':'COMPLETED','rows':total,'planned_rows':25200}
    except Exception as exc:
        status={'status':'FAILED','error':str(exc),'traceback':traceback.format_exc()};raise
    finally:(OUT/'analysis/state_matrix_status.json').write_text(json.dumps(status,indent=2))
