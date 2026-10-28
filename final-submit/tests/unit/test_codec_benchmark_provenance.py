from __future__ import annotations

import hashlib

from agent_runtime.evaluation.codec_benchmark import run_codec_benchmark, write_codec_results


def test_codec_result_bundle_has_source_identity_and_valid_manifest(tmp_path):
    payload = run_codec_benchmark(repeat=2)
    assert payload["env"]["source_tree_sha256"]
    out = write_codec_results(payload, root=tmp_path)
    assert (out / "metadata.json").is_file()
    manifest = out / "MANIFEST.sha256"
    assert manifest.is_file()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    assert payload["env"]["source_tree_sha256"] in (out / "report.md").read_text(encoding="utf-8")
