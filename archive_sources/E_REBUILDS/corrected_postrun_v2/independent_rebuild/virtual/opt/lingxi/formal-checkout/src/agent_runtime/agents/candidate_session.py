"""Persistent Planner/Retriever processes and bounded, immutable corpus state.

All transports execute identical float32 embeddings and stable cosine ranking.
Text uses compact JSON floats; copy uses bytes; sealed_fd uses a verified read-only
mapping. Corpus memory reuses the matrix, not an old query's answer. Every query
is embedded and ranked again. Content, provider and representation bind the key.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import mmap
import multiprocessing as mp
import os
from pathlib import Path
import socket
import struct
import threading
import time

import numpy as np

from agent_runtime.providers.base import validate_embedding_matrix
from agent_runtime.state_exchange.fd_passing import SealedSegment, fd_passing_supported

MAX_FRAME = 128 << 20
MAX_STATE = 32 << 20
VERSION = 'candidate-state/2'


def _read(sock, size):
    out = bytearray()
    while len(out) < size:
        block = sock.recv(size - len(out))
        if not block:
            raise EOFError('candidate peer closed')
        out.extend(block)
    return bytes(out)


def _send(sock, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > MAX_FRAME:
        raise ValueError('candidate frame too large')
    sock.sendall(struct.pack('!I', len(data)) + data)
    return 4 + len(data)


def _recv(sock):
    size = struct.unpack('!I', _read(sock, 4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise ValueError('invalid candidate frame size')
    return json.loads(_read(sock, size)), 4 + size


def provider_identity(provider):
    """Bind cache to code and shipped weights, rather than a misleading model label."""
    root = Path(__file__).resolve().parents[1] / 'providers'
    digest = hashlib.sha256(provider.encode())
    for path in sorted([*root.glob('*.py'), *root.glob('data/*.npz')]):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def corpus_identity(documents, provider_id):
    encoded = json.dumps(documents, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(provider_id.encode() + b'\0float32\0' + encoded).hexdigest()


def _evict(cache, max_entries, max_bytes):
    while cache and (len(cache) > max_entries or sum(v['nbytes'] for v in cache.values()) > max_bytes):
        _, entry = cache.popitem(last=False)
        entry['close']()


def _receiver(parent, peer, mode, reuse, max_entries, max_bytes):
    parent.close()
    peer.settimeout(30)
    cache = OrderedDict()
    try:
        while True:
            header, incoming = _recv(peer)
            action = header.get('action')
            if action == 'close':
                _send(peer, {'ok': True}); break
            if action == 'invalidate':
                old = cache.pop(header['corpus_id'], None)
                if old: old['close']()
                _send(peer, {'ok': True}); continue
            if action != 'rank' or header.get('protocol') != VERSION:
                raise ValueError('unnegotiated candidate operation')
            key = header['corpus_id']
            entry = cache.get(key)
            descriptor = None; mapping = None
            if header['reuse']:
                if entry is None:
                    _send(peer, {'ok': False, 'need_state': True}); continue
                if entry['sha256'] != header['sha256'] or entry['shape'] != header['shape']:
                    raise ValueError('cached candidate identity mismatch')
                cache.move_to_end(key)
            else:
                shape = header['shape']; nbytes = header['nbytes']
                if (len(shape) != 2 or not all(isinstance(v, int) and v > 0 for v in shape)
                        or nbytes != shape[0] * shape[1] * 4 or nbytes > MAX_STATE):
                    raise ValueError('invalid candidate shape')
                if mode == 'text':
                    body, count = _recv(peer); incoming += count
                    matrix = validate_embedding_matrix(np.asarray(body['matrix'], dtype=np.float32),
                        expected_rows=shape[0], expected_dim=shape[1])
                    buffer = matrix.tobytes()
                elif mode == 'copy':
                    buffer = _read(peer, nbytes); incoming += nbytes
                else:
                    marker, descriptors, _, _ = socket.recv_fds(peer, 1, 1)
                    if marker != b'F' or len(descriptors) != 1:
                        for descriptor in descriptors: os.close(descriptor)
                        raise ValueError('missing candidate state fd')
                    descriptor = descriptors[0]; incoming += 1
                    import fcntl
                    seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
                    required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
                    if seals & required != required or os.fstat(descriptor).st_size != nbytes:
                        os.close(descriptor); raise ValueError('unsealed or mismatched candidate state')
                    mapping = mmap.mmap(descriptor, nbytes, flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ)
                    buffer = mapping
                if hashlib.sha256(buffer).hexdigest() != header['sha256']:
                    if mapping: mapping.close()
                    if descriptor is not None: os.close(descriptor)
                    raise ValueError('candidate state digest mismatch')
                matrix = np.frombuffer(buffer, dtype=np.float32).reshape(shape)
                if not np.all(np.isfinite(matrix)):
                    del matrix
                    if mapping: mapping.close()
                    if descriptor is not None: os.close(descriptor)
                    raise ValueError('nonfinite candidate state')
                def close_entry(mapping=mapping, descriptor=descriptor):
                    if mapping is not None: mapping.close()
                    if descriptor is not None: os.close(descriptor)
                entry = {'buffer': buffer, 'shape':shape, 'nbytes':nbytes,
                         'sha256':header['sha256'], 'close':close_entry}
                del matrix
                old = cache.pop(key, None)
                if old: old['close']()
                if reuse and nbytes <= max_bytes:
                    cache[key] = entry; _evict(cache, max_entries, max_bytes)
            matrix = np.frombuffer(entry['buffer'], dtype=np.float32).reshape(entry['shape'])
            query = validate_embedding_matrix(np.asarray([header['query']], dtype=np.float32),
                expected_rows=1, expected_dim=entry['shape'][1])[0]
            scores = matrix @ query
            order = np.argsort(-scores, kind='stable').tolist()
            del matrix
            result = {'ok':True, 'ranked_indices':order, 'top_score':float(scores[order[0]]),
                'retriever_pid':os.getpid(), 'digest_verified':True, 'receiver_memory_hit':bool(header['reuse']),
                'receiver_input_bytes':incoming, 'read_mode':mode if mode != 'sealed_fd' else 'mmap_private_ro'}
            if not reuse or entry['nbytes'] > max_bytes: entry['close']()
            _send(peer, result)
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try: _send(peer, {'ok':False,'error':f'{type(exc).__name__}: {exc}'})
        except OSError: pass
    finally:
        for entry in cache.values(): entry['close']()
        peer.close()


def _producer(control, unused, peer, other_peer, mode, reuse, provider, provider_id, max_entries, max_bytes):
    unused.close(); other_peer.close()
    control.settimeout(30);peer.settimeout(30)
    from agent_runtime.config import Config, EmbeddingConfig
    from agent_runtime.providers.factory import build_embedding
    embedding = build_embedding(Config(embedding=EmbeddingConfig(provider=provider)))
    cache = OrderedDict()
    _send(control, {'protocol':VERSION,'agent':'planner','pid':os.getpid(),
                    'capabilities':['embed_corpus','embed_query','rank','invalidate'], 'model_id':provider_id})
    try:
        while True:
            request, controller_bytes = _recv(control)
            if request['action'] == 'close':
                _send(peer, {'action':'close'}); _recv(peer); _send(control, {'ok':True}); break
            key = request['corpus_id']
            entry = cache.get(key)
            if request.get('force_refresh'):
                old = cache.pop(key, None)
                if old: old['close']()
                _send(peer, {'action':'invalidate','corpus_id':key}); _recv(peer); entry=None
            hit = entry is not None
            rows = 0; fresh = 0
            if entry is None:
                documents = request.get('documents')
                if documents is None:
                    _send(control, {'need_corpus':True}); continue
                if corpus_identity(documents, provider_id) != key:
                    raise ValueError('corpus content/provider fingerprint mismatch')
                local_cache = getattr(embedding, '_cache', {})
                fresh = sum(document not in local_cache for document in documents)
                matrix = validate_embedding_matrix(embedding.embed(documents), expected_rows=len(documents))
                payload = matrix.tobytes(); rows = len(documents)
                if len(payload) > MAX_STATE: raise ValueError('candidate state too large')
                segment = SealedSegment.create(payload) if mode == 'sealed_fd' else None
                entry = {'payload':payload, 'shape':list(matrix.shape),'sha256':hashlib.sha256(payload).hexdigest(),
                         'nbytes':len(payload),'segment':segment,
                         'close':segment.close if segment is not None else lambda:None}
                if reuse and len(payload) <= max_bytes:
                    cache[key]=entry; _evict(cache,max_entries,max_bytes)
            else:
                cache.move_to_end(key)
            query = validate_embedding_matrix(embedding.embed([request['query']]), expected_rows=1,
                                              expected_dim=entry['shape'][1])[0]
            header={'protocol':VERSION,'action':'rank','corpus_id':key,'shape':entry['shape'],
                    'nbytes':entry['nbytes'],'sha256':entry['sha256'],'query':query.tolist(),'reuse':hit}
            wire = _send(peer,header); text_bytes=wire; sent_state=0; state_count=0
            def transmit():
                nonlocal wire,text_bytes,sent_state,state_count
                if mode == 'text':
                    matrix=np.frombuffer(entry['payload'],dtype=np.float32).reshape(entry['shape'])
                    count=_send(peer,{'matrix':matrix.tolist()});wire+=count;text_bytes+=count
                elif mode == 'copy':
                    peer.sendall(entry['payload']);wire+=entry['nbytes'];sent_state=entry['nbytes'];state_count=1
                else:
                    socket.send_fds(peer,[b'F'],[entry['segment'].fd]);wire+=1;sent_state=entry['nbytes'];state_count=1
            if not hit: transmit()
            receiver, reply_bytes = _recv(peer); wire+=reply_bytes;text_bytes+=reply_bytes
            if receiver.get('need_state'):
                header['reuse']=False
                count=_send(peer,header);wire+=count;text_bytes+=count;transmit()
                receiver, count=_recv(peer);wire+=count;text_bytes+=count
            if not receiver.get('ok'):raise ValueError(str(receiver))
            result={**receiver,'planner_pid':os.getpid(),'mode':mode,'corpus_id':key,'provider_id':provider_id,
                'shape':entry['shape'],'state_sha256':entry['sha256'],'state_bytes':entry['nbytes'],
                'candidate_rows_materialized':rows,'new_candidate_text_embeddings':fresh,'query_rows_embedded':1,
                'producer_memory_hit':hit,'state_materialized_bytes':rows*entry['shape'][1]*4,
                'nontext_transfers':state_count,'nontext_logical_bytes':sent_state,
                'agent_socket_bytes':wire,'agent_text_bytes':text_bytes,
                'controller_request_bytes':controller_bytes,
                'cached_corpora':len(cache),'cached_state_bytes':sum(v['nbytes'] for v in cache.values())}
            _send(control,result)
            if not reuse or entry['nbytes'] > max_bytes: entry['close']()
    except (EOFError,BrokenPipeError):
        pass
    except Exception as exc:
        try:_send(control,{'ok':False,'error':f'{type(exc).__name__}: {exc}'})
        except OSError:pass
    finally:
        for entry in cache.values():entry['close']()
        control.close();peer.close()


class CandidateSession:
    """Explicit session lifecycle; safe to call through the existing Agent RPC proxy."""
    def __init__(self, *, mode='auto', reuse=True, embedding_provider='docsem',
                 max_entries=4, max_bytes=16 << 20, timeout=30):
        if mode == 'auto':mode='sealed_fd' if fd_passing_supported() else 'copy'
        if mode not in {'text','copy','sealed_fd'}:raise ValueError('unsupported candidate transport')
        if mode=='sealed_fd' and not fd_passing_supported():raise RuntimeError('sealed fd unsupported')
        if max_entries<1 or max_bytes<1 or timeout<=0:raise ValueError('invalid candidate session budget')
        self.mode=mode;self.reuse=reuse;self.provider=embedding_provider;self.provider_id=provider_identity(embedding_provider)
        self.max_entries=max_entries;self.max_bytes=max_bytes;self.timeout=timeout
        self._known=set();self._lock=threading.RLock();self._closed=False;self._processes=[]
        self._identity_cache=OrderedDict()
        self._identity_cache_lock=threading.Lock()
        self._start()

    def _start(self):
        context=mp.get_context('spawn')
        controller,worker=socket.socketpair();a,b=socket.socketpair()
        for sock in (controller,worker,a,b):sock.settimeout(self.timeout)
        receiver=context.Process(target=_receiver,args=(a,b,self.mode,self.reuse,self.max_entries,self.max_bytes))
        producer=context.Process(target=_producer,args=(worker,controller,a,b,self.mode,self.reuse,
            self.provider,self.provider_id,self.max_entries,self.max_bytes))
        try:
            receiver.start();producer.start()
            worker.close();a.close();b.close()
            hello,_=_recv(controller)
            if hello.get('protocol')!=VERSION or hello.get('model_id')!=self.provider_id:
                raise ValueError('candidate handshake mismatch')
            self._socket=controller;self._processes=[producer,receiver];self._hello=hello
        except BaseException:
            for proc in (producer,receiver):
                if proc.pid is not None:
                    if proc.is_alive():proc.terminate()
                    proc.join(2)
            for sock in (controller,worker,a,b):sock.close()
            raise

    def _identity_with_cache(self,documents):
        key=tuple(documents)
        with self._identity_cache_lock:
            cached=self._identity_cache.get(key)
            if cached is not None:
                self._identity_cache.move_to_end(key)
                return cached,True
        digest=corpus_identity(key,self.provider_id)
        with self._identity_cache_lock:
            cached=self._identity_cache.get(key)
            if cached is not None:
                self._identity_cache.move_to_end(key)
                return cached,True
            self._identity_cache[key]=digest
            while len(self._identity_cache)>self.max_entries:
                self._identity_cache.popitem(last=False)
        return digest,False

    def identity(self,documents):
        """Reuse fingerprints for equal immutable document sequences within this session."""
        return self._identity_with_cache(documents)[0]

    def rank(self,query,documents,*,force_refresh=False):
        documents=[str(v) for v in documents]
        if not documents or len(documents)>32768 or not isinstance(query,str) or not query:
            raise ValueError('candidate query requires 1..32768 documents and nonempty text')
        key,_=self._identity_with_cache(documents)
        with self._lock:
            if self._closed:raise RuntimeError('candidate session closed')
            start=time.perf_counter()
            request={'action':'rank','corpus_id':key,'query':query,'force_refresh':force_refresh,
                     'documents':documents if force_refresh or key not in self._known or not self.reuse else None}
            try:
                sent=_send(self._socket,request);result,received=_recv(self._socket)
                if result.get('need_corpus'):
                    request['documents']=documents;sent+=_send(self._socket,request)
                    result,count=_recv(self._socket);received+=count
                if not result.get('ok'):raise RuntimeError(result.get('error','candidate worker failed'))
            except BaseException:
                self.close();raise
            self._known.add(key)
            # Bound controller identity metadata as well as worker state. Eviction is safe:
            # it causes another request upload, not reuse of a mismatched matrix.
            if len(self._known)>self.max_entries:self._known={key}
            result.update(controller_pid=os.getpid(),controller_bytes=sent+received,
                strict_socket_bytes=sent+received+result['agent_socket_bytes'],
                strict_text_bytes=sent+received+result['agent_text_bytes'],
                task_latency_ms=(time.perf_counter()-start)*1000)
            if len({result['planner_pid'],result['retriever_pid'],os.getpid()})!=3:
                raise RuntimeError('candidate roles did not run in three distinct processes')
            return result

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True
            try:_send(self._socket,{'action':'close'});_recv(self._socket)
            except (OSError,EOFError,ValueError):pass
            self._socket.close()
            for proc in self._processes:
                proc.join(2)
                if proc.is_alive():proc.terminate();proc.join(2)
                if proc.is_alive():proc.kill();proc.join(2)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
