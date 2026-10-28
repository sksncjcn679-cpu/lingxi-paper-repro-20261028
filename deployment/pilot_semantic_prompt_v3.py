"""One fixed round controlling model evidence presentation across all six groups."""
import argparse
import json
from pathlib import Path
import shutil
import uuid

import pilot_supplement_v2 as supplement
from pilot_corpus_v2 import VERSION, corpus, audit

driver = supplement.driver
OUT = Path("/evidence/supplements/semantic_prompt_v3")
FILES = supplement.FILES + ["pilot_semantic_prompt_v3.py"]


def prepare():
    verified = supplement.verify_v1()
    OUT.mkdir(parents=True, exist_ok=False)
    for folder in ["raw", "corpus", "manifests", "analysis", "logs", "drivers"]:
        (OUT / folder).mkdir()
    cases = corpus()
    assert not audit(cases)
    (OUT / "corpus/tasks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in cases))
    plan = []
    for index, case in enumerate(r for r in cases if r["domain"] == "migration"):
        arms = list(driver.ARMS)
        offset = index % len(arms)
        for arm in arms[offset:] + arms[:offset]:
            unit = {"phase": "v3_repeat_01", "repeat": 1, "model": "M1", "arm": arm,
                    "task_id": case["task_id"], "processes": False, "corpus_version": VERSION,
                    "prompt_version": "semantic_task_only_v3"}
            unit["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(unit, sort_keys=True)).hex
            plan.append(unit)
    supplement.dump(OUT / "manifests/plan.json", plan)
    supplement.dump(OUT / "protocol.json", {"purpose": "semantic input presentation deployment check",
        "planned_units": 36, "repeats": 1, "corpus_version": VERSION,
        "llm_context_mode": "none", "model": driver.MODELS["M1"],
        "temperature": 0, "max_tokens": 1500, "timeout_sec": 60, "max_attempts_per_unit": 3,
        "attempt_ceiling": 120, "observed_token_stop_before_next_request": 200000,
        "selection": "all six migration cases and all six groups, one predeclared round; no failed-unit rerun",
        "memory": "new stores keyed by v3 phase/model/arm/chain; starts empty",
        "ipc": "all six IPC/state/memory paths retained; only model-facing inbound wire preamble disabled for all groups",
        "claim_boundary": "model prompt token savings cannot be claimed when prompts are identical; IPC measurements remain separate",
        "failure_policy": "retain all failures; no answer fallback, no expected-label prompt injection",
        "formal_experiment": False, "human_review": "PENDING", "currency_cost": "UNKNOWN"})
    for name in FILES:
        shutil.copy2(Path(__file__).with_name(name), OUT / "drivers" / name)
    paths = ["corpus/tasks.jsonl", "manifests/plan.json", "protocol.json"]
    supplement.dump(OUT / "manifests/freeze.json", {
        "source_sha256": driver._source_tree_sha256(driver.ROOT),
        "files": {p: driver.sha((OUT / p).read_bytes()) for p in paths},
        "drivers": {name: driver.sha(Path(__file__).with_name(name).read_bytes()) for name in FILES},
        "frozen_at_utc": driver.now()})
    print(json.dumps({"prepared_units": len(plan), "original_checksums_verified": verified}), flush=True)


def run():
    supplement.OUT = OUT
    original_configuration = driver.configuration
    def configuration(model, processes=False):
        return original_configuration(model, processes).with_overrides(llm_context_mode="none")
    driver.configuration = configuration
    supplement.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "run"])
    args = parser.parse_args()
    {"prepare": prepare, "run": run}[args.action]()
