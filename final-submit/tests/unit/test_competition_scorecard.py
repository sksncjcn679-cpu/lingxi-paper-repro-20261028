"""Scorecard tests run against an isolated temp project root (never write into the repo)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime import RELEASE_NAME, __version__
from agent_runtime.evaluation.competition_scorecard import (
    _deliverable_status,
    _is_real_mp4,
    run_competition_scorecard,
    write_competition_results,
)

_REPO = Path(__file__).resolve().parents[2]


def _fake_root(tmp_path: Path, *, video: bytes | None) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "src").symlink_to(_REPO / "src", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        if getattr(exc, "winerror", None) == 1314 or not hasattr(Path, "symlink_to"):
            pytest.skip("directory symlinks require Windows developer mode or elevated privilege")
        raise
    # The scorecard behavior test owns its document fixture. A user's unfinished
    # release documentation must not turn it into an unrelated functional failure.
    (root / "docs").mkdir()
    for name in ("DESIGN.md", "DEPLOYMENT.md", "EXPERIMENT_REPORT.md"):
        (root / "docs" / name).write_text("# Verified fixture\n\n" + "Technical fixture content and evidence explanation.\n" * 16)
    (root / "deliverables").mkdir()
    if video is not None:
        (root / "deliverables" / f"演示视频_{RELEASE_NAME}.mp4").write_bytes(video)
    return root


def _mp4_like(size: int = 70 * 1024) -> bytes:
    head = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    return head + b"\x00" * (size - len(head))


def test_video_validation_rejects_placeholders(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"test-placeholder")
    assert not _is_real_mp4(p)
    assert _deliverable_status("video", p).startswith("INVALID")
    p.write_bytes(_mp4_like())
    assert _is_real_mp4(p)
    assert _deliverable_status("video", tmp_path / "missing.mp4") == "MISSING"


def test_competition_scorecard_passes_all_non_environment_requirements(tmp_path):
    root = _fake_root(tmp_path, video=_mp4_like())
    payload = run_competition_scorecard(root=root, rounds=10)
    failed = [c for c in payload["checks"] if c["mandatory"] and c["id"] != "openeuler" and not c["passed"]]
    assert payload["summary"]["all_non_environment_mandatory_passed"] is True, failed
    ids = {c["id"] for c in payload["checks"]}
    assert {"agents", "dual_mode", "state", "memory_retrieval", "related_groups", "stability", "deliverables", "openeuler"} <= ids
    assert isinstance(payload["summary"]["openeuler_target_verified"], bool)
    metrics = next(c for c in payload["checks"] if c["id"] == "metrics")



def test_scorecard_flags_placeholder_video(tmp_path):
    root = _fake_root(tmp_path, video=b"test-placeholder")
    payload = run_competition_scorecard(root=root, rounds=10)
    deliverables = next(c for c in payload["checks"] if c["id"] == "deliverables")
    assert deliverables["passed"] is False and "video=INVALID" in deliverables["evidence"]


def test_competition_scorecard_artifacts_have_manifest(tmp_path):
    root = _fake_root(tmp_path, video=_mp4_like())
    p = run_competition_scorecard(root=root, rounds=10)
    out = write_competition_results(p, root=str(tmp_path / "results"))
    assert (out / "report.md").exists()
    assert "summary.json" in (out / "MANIFEST.sha256").read_text()
