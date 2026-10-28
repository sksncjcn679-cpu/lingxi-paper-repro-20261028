"""Versioned correction of synthetic pilot evidence; the v1 generator stays frozen."""
import copy
import hashlib
import json

from pilot_corpus import corpus as corpus_v1

VERSION = "pilot_corpus_v2_shared_abi_evidence"


def corpus():
    rows = copy.deepcopy(corpus_v1())
    for row in rows:
        row["previous_content_sha256"] = row.pop("content_sha256")
        row["corpus_version"] = VERSION
        if row["domain"] == "migration":
            # Both decisions consult the same observed ABI facts. Alternative actions
            # must not manufacture mutually contradictory observations.
            probe = row["expected"]["probe"]
            row["observations"]["checks"]["keep_binary"] = probe
            row["observations"]["checks"]["rebuild_binary"] = probe
        row["content_sha256"] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return rows


def audit(rows):
    problems = []
    for row in rows:
        checks = row["observations"]["checks"]
        if row["domain"] == "migration" and row["expected"]["diagnosis"] == "rebuild_required":
            if checks["keep_binary"] == "ABI: interpreter, architecture, native dependencies and symbols match":
                problems.append({"task_id": row["task_id"], "issue": "incompatible symptom conflicts with healthy ABI check"})
        if checks.get(row["expected"]["action"]) != row["expected"]["probe"]:
            problems.append({"task_id": row["task_id"], "issue": "expected probe disagrees with action evidence"})
    return problems
