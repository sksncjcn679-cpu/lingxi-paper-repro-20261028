from __future__ import annotations

import pytest

from agent_runtime.evaluation.codec_benchmark import run_codec_benchmark


def test_codec_benchmark_reports_correct_roundtrip_and_reduction():
    pytest.importorskip("msgpack")
    payload = run_codec_benchmark(repeat=3)
    assert payload["suite"] == "codec"
    assert len(payload["cases"]) == 3
    assert payload["aggregate"]["msgpack_bytes"] < payload["aggregate"]["json_bytes"]
    assert payload["aggregate"]["byte_reduction"] > 0
