"""Planner-generated candidate vectors consumed by a Retriever in another OS process.

The sealed-fd branch maps received bytes directly as a read-only numpy view. The copy
baseline uses identical embeddings and receiver math; setup and end-to-end time include
both processes. Small states use copy by default, with a configurable 1 MiB crossover.
"""
from __future__ import annotations
import hashlib
import json
import mmap
import multiprocessing as mp
import os
import socket
import struct
import time
import numpy as np

from agent_runtime.state_exchange.fd_passing import SealedSegment, fd_passing_supported

MAX_STATE = 64 * 1024 * 1024

def _receive(sock, n):
    data = bytearray()
    while len(data) < n:
        block = sock.recv(n - len(data))
        if not block:
            raise EOFError('candidate stream truncated')
        data.extend(block)
    return bytes(data)

def _planner(sock, outcome, query, candidates, mode, embedding_provider):
    try:
        sock.settimeout(30)
        from agent_runtime.config import Config, EmbeddingConfig
        from agent_runtime.providers.factory import build_embedding
        embedding = build_embedding(Config(embedding=EmbeddingConfig(provider=embedding_provider)))
        matrix = np.asarray(embedding.embed([*candidates, query]), dtype=np.float32)
        payload = matrix.tobytes(order='C')
        if len(payload) > MAX_STATE:
            raise ValueError('candidate state exceeds 64 MiB')
        header = {'shape': list(matrix.shape), 'nbytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
                  'planner_pid': os.getpid(), 'mode': mode}
        segment = None
        try:
            if mode == 'sealed_fd':
                segment = SealedSegment.create(payload)
                try:
                    os.pwrite(segment.fd, b'x', 0)
                    header['sealed_write_rejected'] = False
                except PermissionError:
                    header['sealed_write_rejected'] = True
            blob = json.dumps(header, sort_keys=True).encode()
            sock.sendall(struct.pack('!I', len(blob)) + blob)
            if mode == 'sealed_fd':
                socket.send_fds(sock, [b'F'], [segment.fd])
                wire = 4 + len(blob) + 1
            else:
                sock.sendall(payload)
                wire = 4 + len(blob) + len(payload)
            # Keep the producer alive until the consumer acknowledges actual downstream use.
            if _receive(sock, 1) != b'K':
                raise ValueError('missing Retriever acknowledgment')
            outcome.send({'ok': True, 'planner_pid': os.getpid(), 'state_bytes': len(payload), 'socket_bytes': wire + 1,
                          'sealed_write_rejected': header.get('sealed_write_rejected')})
        finally:
            if segment is not None:
                segment.close()
    except Exception as exc:
        outcome.send({'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
    finally:
        sock.close()
        outcome.close()

def _retriever(sock, outcome):
    descriptor = None
    mapping = None
    matrix = None
    try:
        sock.settimeout(30)
        hsize = struct.unpack('!I', _receive(sock, 4))[0]
        if not 1 <= hsize <= 4096:
            raise ValueError('invalid candidate header length')
        header = json.loads(_receive(sock, hsize))
        shape = tuple(header['shape'])
        size = int(header['nbytes'])
        if len(shape) != 2 or min(shape) < 1 or size != shape[0] * shape[1] * 4 or size > MAX_STATE:
            raise ValueError('candidate shape/size mismatch')
        if header['mode'] == 'sealed_fd':
            msg, fds, _, _ = socket.recv_fds(sock, 1, 1)
            if msg != b'F' or len(fds) != 1:
                for fd in fds:
                    os.close(fd)
                raise ValueError('missing candidate descriptor')
            descriptor = fds[0]
            mapping = mmap.mmap(descriptor, size, flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ)
            buffer = mapping
            read_mode = 'mmap_private_ro'
        else:
            buffer = _receive(sock, size)
            read_mode = 'socket_copy'
        if hashlib.sha256(buffer).hexdigest() != header['sha256']:
            raise ValueError('candidate state digest mismatch')
        matrix = np.frombuffer(buffer, dtype=np.float32).reshape(shape)
        sims = matrix[:-1] @ matrix[-1]
        order = np.argsort(-sims, kind='stable')
        result = {'ok': True, 'retriever_pid': os.getpid(), 'planner_pid': header['planner_pid'],
                  'read_mode': read_mode, 'shape': list(shape), 'ranked_indices': order.tolist(),
                  'top_score': float(sims[order[0]]), 'digest_verified': True,
                  'state_shared_in_socket': header['mode'] != 'sealed_fd'}
        # Release all numpy views before closing the mapping and descriptor.
        del matrix
        matrix = None
        if mapping is not None:
            mapping.close()
            mapping = None
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        sock.sendall(b'K')
        outcome.send(result)
    except Exception as exc:
        outcome.send({'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
    finally:
        matrix = None
        if mapping is not None:
            mapping.close()
        if descriptor is not None:
            os.close(descriptor)
        sock.close()
        outcome.close()

def run_candidate_workflow(query, candidates, *, mode='auto', threshold=1 << 20, embedding_provider='docsem', timeout=30):
    candidates = [str(c) for c in candidates]
    if not candidates or len(candidates) > 32768:
        raise ValueError('need 1..32768 candidates')
    if mode not in {'auto', 'copy', 'sealed_fd'}:
        raise ValueError('handoff mode must be auto, copy or sealed_fd')
    # The shipped configuration is 256-dimensional. Auto is a policy, not a speed claim.
    if mode == 'auto':
        mode = 'sealed_fd' if (len(candidates) + 1) * 256 * 4 >= threshold and fd_passing_supported() else 'copy'
    if mode == 'sealed_fd' and not fd_passing_supported():
        raise RuntimeError('sealed fd handoff unsupported on this host')
    start = time.perf_counter()
    context = mp.get_context('fork' if hasattr(os, 'fork') else 'spawn')
    a, b = socket.socketpair()
    a.settimeout(timeout)
    b.settimeout(timeout)
    pp, pc = context.Pipe(duplex=False)
    rp, rc = context.Pipe(duplex=False)
    producer = context.Process(target=_planner, args=(a, pc, query, candidates, mode, embedding_provider))
    consumer = context.Process(target=_retriever, args=(b, rc))
    try:
        consumer.start()
        producer.start()
        a.close(); b.close(); pc.close(); rc.close()
        if not rp.poll(timeout):
            raise TimeoutError('Retriever candidate timeout')
        receiver = rp.recv()
        if not pp.poll(timeout):
            raise TimeoutError('Planner candidate timeout')
        sender = pp.recv()
        if not sender.get('ok') or not receiver.get('ok'):
            raise RuntimeError(str({'planner': sender, 'retriever': receiver}))
        if sender['planner_pid'] == receiver['retriever_pid'] or sender['planner_pid'] == os.getpid():
            raise RuntimeError('candidate producer and receiver are not distinct processes')
        return {**sender, **receiver, 'mode': mode, 'controller_pid': os.getpid(),
                'end_to_end_ms': (time.perf_counter() - start) * 1000}
    finally:
        for proc in (producer, consumer):
            if proc.pid is not None:
                proc.join(1)
                if proc.is_alive():
                    proc.terminate(); proc.join(2)
        for obj in (a, b, pp, pc, rp, rc):
            obj.close()

def rank_candidates(evidence, ctx):
    session = ctx.candidate_session
    if session is not None:
        result = session.rank(evidence['query'], evidence['candidates'])
        if ctx.memory is not None:
            ctx.memory.remember_candidate_state(result, task_id=ctx.task_id)
    else:
        result = run_candidate_workflow(evidence['query'], evidence['candidates'], mode=ctx.config.large_state_handoff)
    ctx.blackboard['candidate_handoff'] = result
    return result['ranked_indices'][:3]
