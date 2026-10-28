"""Six predeclared M2 reference checks with the same corrected semantic prompts."""
import argparse
import json
from pathlib import Path
import shutil
import uuid

import pilot_supplement_v2 as supplement
from pilot_corpus_v2 import VERSION, corpus, audit

driver = supplement.driver
OUT = Path("/evidence/supplements/model2_reference_v3")
FILES = supplement.FILES + ["pilot_model2_reference_v3.py"]


def prepare():
    supplement.verify_v1()
    OUT.mkdir(parents=True, exist_ok=False)
    for folder in ["raw", "corpus", "manifests", "analysis", "logs", "drivers"]:
        (OUT / folder).mkdir()
    cases = corpus()
    assert not audit(cases)
    (OUT / "corpus/tasks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in cases))
    plan = []
    for case in (r for r in cases if r["domain"] == "migration"):
        unit = {"phase": "v3_m2_reference", "repeat": 1, "model": "M2", "arm": "SS0",
                "task_id": case["task_id"], "processes": False, "corpus_version": VERSION,
                "prompt_version": "semantic_task_only_v3"}
        unit["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(unit, sort_keys=True)).hex
        plan.append(unit)
    supplement.dump(OUT / "manifests/plan.json", plan)
    supplement.dump(OUT / "protocol.json", {"purpose": "bounded M2 reference, not main model replacement",
        "planned_units": 6, "repeats": 1, "corpus_version": VERSION, "llm_context_mode": "none",
        "model": driver.MODELS["M2"], "extra_body": {"enable_thinking": False},
        "temperature": 0, "max_tokens": 1500, "timeout_sec": 60, "max_attempts_per_unit": 3,
        "attempt_ceiling": 120, "observed_token_stop_before_next_request": 200000,
        "selection": "all six migration cases, SS0 only; one round; no answer replacement or failed-unit rerun",
        "formal_experiment": False, "human_review": "PENDING", "currency_cost": "UNKNOWN"})
    for name in FILES:
        shutil.copy2(Path(__file__).with_name(name), OUT / "drivers" / name)
    paths = ["corpus/tasks.jsonl", "manifests/plan.json", "protocol.json"]
    supplement.dump(OUT / "manifests/freeze.json", {
        "source_sha256": driver._source_tree_sha256(driver.ROOT),
        "files": {p: driver.sha((OUT / p).read_bytes()) for p in paths},
        "drivers": {name: driver.sha(Path(__file__).with_name(name).read_bytes()) for name in FILES},
        "frozen_at_utc": driver.now()})
    print(json.dumps({"prepared_units": len(plan)}), flush=True)


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
