"""Three independent process batches, complete raw codec/AF_UNIX observations; no model calls."""
from pathlib import Path
import hashlib
import json
import multiprocessing as mp
import os
import socket
import struct
import sys
import time
import traceback

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.protocol.message import AgentMessage, ActionType
from agent_runtime.protocol.serialization import (render_text_lean,encode_structured,decode_structured,
    encode_structured_msgpack,decode_structured_msgpack)
from agent_runtime.protocol.session_codec import SessionWireCodec

OUT=Path('/evidence/formal_20261019')
ACK=struct.Struct('!?Q32sI')
SIZES=[512,2048,8192,32768,131072]
CODECS=['lean_text','json','msgpack','session_msgpack']


def exact(sock,n):
    data=bytearray()
    while len(data)<n:
        part=sock.recv(n-len(data))
        if not part: raise EOFError('peer closed before complete frame')
        data.extend(part)
    return bytes(data)


def consumer(sock):
    session=SessionWireCodec(value_dictionary=True,predictive=True,schema_atoms=True)
    try:
        while True:
            header=exact(sock,5)
            mode,size=header[0],struct.unpack('!I',header[1:])[0]
            if mode==255: break
            wire=exact(sock,size)
            start=time.perf_counter_ns()
            if mode==0:
                facts=json.loads(wire.decode().split(' | input=',1)[1])
            elif mode==1: facts=decode_structured(wire.decode()).input_parameters
            elif mode==2: facts=decode_structured_msgpack(wire).input_parameters
            else: facts=session.decode_msgpack(wire).input_parameters
            elapsed=time.perf_counter_ns()-start
            digest=hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).digest()
            sock.sendall(ACK.pack(True,elapsed,digest,os.getpid()))
    finally: sock.close()


def batch(index):
    raw=OUT/f'raw/protocol_batch_{index}.jsonl'
    if raw.exists(): raise RuntimeError('batch already has evidence; do not silently overwrite or replay')
    ctx=mp.get_context('spawn')
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    parent.settimeout(30)
    worker=ctx.Process(target=consumer,args=(child,))
    setup=time.perf_counter_ns()
    worker.start(); child.close()
    setup=time.perf_counter_ns()-setup
    enc=SessionWireCodec(value_dictionary=True,predictive=True,schema_atoms=True)
    previous='0'*64
    count=0
    try:
        with raw.open('x') as output:
            for size in SIZES:
                # Fixed reproducible synthetic protocol payload; this is not real model data.
                body=hashlib.shake_256(f'protocol-{index}-{size}'.encode()).hexdigest(size//2)
                facts={'payload':body,'items':3,'case':'protocol-only'}
                digest=hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).digest()
                for iteration in range(-100,1000):
                    order=list(range(4)); shift=(iteration+index)%4
                    for mode in order[shift:]+order[:shift]:
                        message=AgentMessage(message_id=f'msg_{iteration+100:012x}',task_id='formal-codec',
                            trace_id='trace_formal',step_id='s1',sequence_no=iteration+100,
                            source_agent='planner',target_agent='retriever',action_type=ActionType.RETRIEVE,
                            capability='retrieval',timestamp=1792416000.0,input_parameters=facts)
                        start=time.perf_counter_ns()
                        if mode==0: wire=render_text_lean(message).encode()
                        elif mode==1: wire=encode_structured(message,reference_first=False).encode()
                        elif mode==2: wire=encode_structured_msgpack(message,reference_first=False)
                        else: wire=enc.encode_msgpack(message)
                        encoded=time.perf_counter_ns()
                        parent.sendall(bytes([mode])+struct.pack('!I',len(wire))+wire)
                        ok,decode_ns,received,consumer_pid=ACK.unpack(exact(parent,ACK.size))
                        end=time.perf_counter_ns()
                        if received!=digest: raise AssertionError('complete business payload did not survive decoding')
                        if iteration<0: continue
                        row={'batch':index,'payload_bytes':size,'codec':CODECS[mode],'iteration':iteration,
                            'encode_ns':encoded-start,'consumer_decode_ns':decode_ns,
                            'socket_roundtrip_ns':end-encoded,'end_to_end_ns':end-start,
                            'payload_wire_bytes':len(wire),'frame_bytes':5,'ack_bytes':ACK.size,
                            'observable_socket_bytes':len(wire)+5+ACK.size,'success':bool(ok),
                            'producer_pid':os.getpid(),'consumer_pid':consumer_pid,'warmup_per_cell':100,
                            'process_setup_ns':setup,'payload_sha256':digest.hex(),'prev_sha256':previous,
                            'scope':'synthetic mechanism; actual AF_UNIX; integrity hashing and ACK included in RTT'}
                        previous=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
                        row['record_sha256']=previous
                        output.write(json.dumps(row)+'\n'); count+=1
                    if iteration>=0 and iteration%100==0: output.flush()
                print(json.dumps({'batch':index,'payload_bytes':size,'measured_rows':count}),flush=True)
            output.flush(); os.fsync(output.fileno())
    finally:
        try: parent.sendall(bytes([255])+struct.pack('!I',0))
        except OSError: pass
        parent.close(); worker.join(10)
        if worker.is_alive():
            worker.terminate();worker.join()
    if worker.exitcode: raise RuntimeError(f'codec consumer exit={worker.exitcode}')
    return count


if __name__=='__main__':
    plan={'sizes':SIZES,'codecs':CODECS,'warmup':100,'repeats':1000,'independent_process_batches':3,
        'planned_measured_rows':60000,'model_calls':0,'latency_scope':'encode + socket send/receive + decode, validation and ACK',
        'native_lean_decoder':'no native lean parser; benchmark parser extracts complete input JSON; full header roundtrip not certified',
        'failure_policy':'keep incomplete raw files; no overwrite or silent restart'}
    target=OUT/'manifests/protocol_microbenchmark_plan.json'
    target.write_text(json.dumps(plan,indent=2))
    total=0
    try:
        for index in range(1,4): total+=batch(index)
        status={'status':'COMPLETED','measured_rows':total,'planned_rows':60000}
    except Exception as exc:
        status={'status':'FAILED','error':str(exc),'traceback':traceback.format_exc(),'completed_batch_rows':total}
        raise
    finally:
        (OUT/'analysis/protocol_matrix_status.json').write_text(json.dumps(status,indent=2))
