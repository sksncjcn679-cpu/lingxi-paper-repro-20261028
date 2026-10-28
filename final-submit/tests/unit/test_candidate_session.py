import sys
import numpy as np
import pytest
from agent_runtime.agents.candidate_session import CandidateSession, corpus_identity
from agent_runtime.config import Config, EmbeddingConfig
from agent_runtime.providers.factory import build_embedding
from agent_runtime.state_exchange.fd_passing import fd_passing_supported

DOCS=['service bind conflict port 8080','permission denied state directory','DNS resolver failure']

def reference(query,documents):
    embedding=build_embedding(Config(embedding=EmbeddingConfig(provider='lexical')))
    matrix=embedding.embed(documents);vector=embedding.embed([query])[0]
    return np.argsort(-(matrix@vector),kind='stable').tolist()

@pytest.mark.parametrize('mode',['text','copy','sealed_fd'])
def test_reuse_changed_query_and_invalidated_document(mode):
    if mode=='sealed_fd' and not fd_passing_supported():pytest.skip('sealed fd unsupported')
    with CandidateSession(mode=mode,embedding_provider='lexical',reuse=True) as session:
        first=session.rank('port conflict',DOCS)
        second=session.rank('name lookup DNS',DOCS)
        assert len(session._identity_cache) == 1
        assert first['ranked_indices']==reference('port conflict',DOCS)
        assert second['ranked_indices']==reference('name lookup DNS',DOCS)
        assert second['producer_memory_hit'] and second['receiver_memory_hit']
        assert second['candidate_rows_materialized']==0
        assert second['query_rows_embedded']==1
        changed=[*DOCS[:-1],'filesystem ENOSPC disk full']
        third=session.rank('DNS',changed)
        assert len(session._identity_cache) == 2
        assert not third['producer_memory_hit']
        assert third['corpus_id']!=first['corpus_id']
        assert third['ranked_indices']==reference('DNS',changed)
        rebuilt=session.rank('DNS',DOCS,force_refresh=True)
        assert len(session._identity_cache) == 2
        assert not rebuilt['producer_memory_hit']
        assert rebuilt['ranked_indices']==reference('DNS',DOCS)
        assert len({rebuilt['planner_pid'],rebuilt['retriever_pid'],rebuilt['controller_pid']})==3
    assert all(not p.is_alive() for p in session._processes)

def test_eviction_requires_rematerialization():
    with CandidateSession(mode='copy',embedding_provider='lexical',max_entries=1) as session:
        first=session.rank('port',DOCS)
        session.rank('disk',[*DOCS,'disk full'])
        again=session.rank('port',DOCS)
        assert not again['producer_memory_hit']
        assert len(session._identity_cache) == 1
        assert again['ranked_indices']==first['ranked_indices']


def test_identity_cache_observes_in_place_document_mutation():
    docs=list(DOCS)
    with CandidateSession(mode='copy',embedding_provider='lexical',reuse=True) as session:
        first=session.rank('port conflict',docs)
        docs[0]='filesystem disk full'
        changed=session.rank('disk full',docs)
        assert changed['corpus_id'] != first['corpus_id']
        assert not changed['producer_memory_hit']
        assert changed['ranked_indices']==reference('disk full',docs)

def test_provider_identity_and_order_bind_content():
    assert corpus_identity(DOCS,'one')!=corpus_identity(DOCS,'two')
    assert corpus_identity(DOCS,'one')!=corpus_identity(list(reversed(DOCS)),'one')
