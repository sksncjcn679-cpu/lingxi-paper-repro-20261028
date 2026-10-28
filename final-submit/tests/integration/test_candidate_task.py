import pytest
from agent_runtime.agents.candidate_workflow import run_candidate_workflow
from agent_runtime.state_exchange.fd_passing import fd_passing_supported

@pytest.mark.skipif(not fd_passing_supported(),reason='sealed memfd requires Linux')
def test_real_planner_retriever_memfd_consumption():
    docs=['bind port address already in use','CPython binary ABI mismatch','filesystem full']
    copied=run_candidate_workflow('port cannot bind address',docs,mode='copy')
    sealed=run_candidate_workflow('port cannot bind address',docs,mode='sealed_fd')
    assert copied['ranked_indices']==sealed['ranked_indices']
    assert sealed['planner_pid']!=sealed['retriever_pid']!=sealed['controller_pid']
    assert sealed['read_mode']=='mmap_private_ro' and sealed['digest_verified']
    assert sealed['sealed_write_rejected']
    assert sealed['socket_bytes']<sealed['state_bytes']
