"""opus5: the copy baseline must be warm and reuse one consumer process."""

from agent_runtime.evaluation.state_fd_handoff_benchmark import run_state_fd_handoff_benchmark
import pytest
import socket


@pytest.mark.skipif(not hasattr(socket, 'AF_UNIX'), reason='AF_UNIX unavailable')
def test_large_state_variants_report_setup_separately_and_copy_is_not_strawman():
    p = run_state_fd_handoff_benchmark(repeat=3, dim=300_000)
    v = p["variants"]
    for name in ("copy_socket", "raw_copy_socket"):
        assert "channel_setup_ms" in v[name]
    # A warm raw copy of ~1.2 MB over AF_UNIX must be far below process start-up time.
    assert v["raw_copy_socket"]["handoff_p50_ms"] < 50.0
