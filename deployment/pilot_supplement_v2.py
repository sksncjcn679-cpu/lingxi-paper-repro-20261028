"""Fixed-size migration supplement; append-only evidence, no success-conditioned reruns."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import uuid

import pilot_openeuler as driver
from pilot_corpus_v2 import VERSION, audit, corpus

OUT = Path("/evidence/supplements/corpus_v2")
FILES = ["pilot_supplement_v2.py", "pilot_corpus_v2.py", "test_pilot_corpus_v2.py",
         "pilot_openeuler.py", "pilot_corpus.py"]
CURRENT = {}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_v1():
    root = Path("/evidence")
    checked = 0
    for line in (root / "checksums.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if driver.sha((root / name).read_bytes()) != digest:
            raise RuntimeError(f"original evidence changed: {name}")
        checked += 1
    return checked


def prepare():
    checked = verify_v1()
    OUT.mkdir(parents=True, exist_ok=False)
    for name in ["raw", "corpus", "manifests", "analysis", "logs", "drivers"]:
        (OUT / name).mkdir()
    cases = corpus()
    assert not audit(cases)
    (OUT / "corpus/tasks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in cases))
    dump(OUT / "analysis/corpus_audit.json", {
        "original_checksums_verified": checked, "cases_scanned": len(cases),
        "original_conflicts": audit(driver.corpus()), "corrected_conflicts": audit(cases),
        "changed_observations": [r["task_id"] for r in cases if r["domain"] == "migration"],
        "automated_scope": "known ABI contradiction and expected/action evidence consistency; not human or exhaustive semantic review",
        "human_review": "PENDING"})
    plan = []
    for repeat in range(1, 4):
        for index, case in enumerate(r for r in cases if r["domain"] == "migration"):
            arms = list(driver.ARMS)
            offset = (index + repeat - 1) % len(arms)
            for arm in arms[offset:] + arms[:offset]:
                unit = {"phase": f"v2_repeat_{repeat:02d}", "repeat": repeat, "model": "M1", "arm": arm,
                        "task_id": case["task_id"], "processes": False, "corpus_version": VERSION}
                unit["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(unit, sort_keys=True)).hex
                plan.append(unit)
    assert len(plan) == 108
    dump(OUT / "manifests/plan.json", plan)
    dump(OUT / "protocol.json", {"version": VERSION, "purpose": "corrected migration-chain supplemental pilot",
        "planned_units": 108, "repeats": 3, "cases_per_chain": 6, "groups": driver.ARMS,
        "model": driver.MODELS["M1"], "temperature": 0, "max_tokens": 1500, "timeout_sec": 60,
        "max_attempts_per_unit": 3, "attempt_ceiling": 120, "observed_token_stop_before_next_request": 200000,
        "memory": "new isolated SQLite store per repeat/model/arm/chain, empty at checkpoint 1",
        "failure_policy": "keep every terminal result; no terminal unit rerun or replacement",
        "selection": "entire affected migration chain, all six groups; predeclared three rounds independent of success",
        "timing": "descriptive only; added model IO logging and uncontrolled WSL host load",
        "human_review": "PENDING", "formal_experiment_started": False,
        "causal_limit": "new provider calls after data correction; not a controlled counterfactual or proof of sole failure cause"})
    for name in FILES:
        shutil.copy2(Path(__file__).with_name(name), OUT / "drivers" / name)
    paths = ["corpus/tasks.jsonl", "manifests/plan.json", "protocol.json"]
    frozen = {"source_sha256": driver._source_tree_sha256(driver.ROOT),
              "files": {p: driver.sha((OUT / p).read_bytes()) for p in paths},
              "drivers": {name: driver.sha(Path(__file__).with_name(name).read_bytes()) for name in FILES},
              "frozen_at_utc": driver.now()}
    dump(OUT / "manifests/freeze.json", frozen)
    print(json.dumps({"prepared_units": len(plan), "original_checksums_verified": checked}), flush=True)


def run():
    verify_v1()
    frozen = json.loads((OUT / "manifests/freeze.json").read_text())
    assert frozen["source_sha256"] == driver._source_tree_sha256(driver.ROOT)
    for path, digest in frozen["files"].items():
        assert driver.sha((OUT / path).read_bytes()) == digest, path
    for name, digest in frozen["drivers"].items():
        assert driver.sha(Path(__file__).with_name(name).read_bytes()) == digest, name
    driver.OUT, driver.RAW = OUT, OUT / "raw"
    driver.ATTEMPT_LIMIT, driver.TOKEN_STOP = 120, 200000
    original_append = driver.append
    def append(name, row):
        original_append(name, {**row, "repeat": CURRENT["repeat"], "corpus_version": VERSION})
    driver.append = append
    original_builder = driver.build_context
    def builder(*args, **kwargs):
        ctx = original_builder(*args, **kwargs)
        create = ctx.llm._client.chat.completions.create
        def capture(**request):
            response = create(**request)
            append("model_io.jsonl", {"run_id": CURRENT["run_id"], "timestamp": driver.now(),
                "messages": request["messages"], "response_text": response.choices[0].message.content,
                "messages_sha256": driver.sha(request["messages"])})
            return response
        ctx.llm._client.chat.completions.create = capture
        return ctx
    driver.build_context = builder
    plan = json.loads((OUT / "manifests/plan.json").read_text())
    cases = {r["task_id"]: r for r in corpus()}
    done = {r["run_id"] for r in driver.rows("task_runs.jsonl")}
    starts = {r["request_id"] for r in driver.rows("provider_attempt_starts.jsonl")}
    ends = {r["request_id"] for r in driver.rows("provider_calls.jsonl")}
    if starts - ends:
        raise RuntimeError("unresolved provider attempt; automatic replay blocked")
    stores = {}
    try:
        for unit in plan:
            if unit["run_id"] in done:
                continue
            CURRENT.clear()
            CURRENT.update(unit)
            case = cases[unit["task_id"]]
            key = (unit["phase"], unit["model"], unit["arm"], case["chain_id"])
            memory = None
            if driver.ARMS[unit["arm"]][2]:
                if key not in stores:
                    path = Path("/opt/lingxi/pilot-memory-v2") / ("__".join(key)+".sqlite")
                    path.parent.mkdir(exist_ok=True)
                    if path.exists() and not any(r["phase"] == unit["phase"] and r["arm"] == unit["arm"] for r in driver.rows("task_runs.jsonl")):
                        raise RuntimeError("unexpected nonempty store path before first unit")
                    stores[key] = driver.MemoryStore(driver.build_embedding(driver.configuration(unit["model"])), db_path=str(path))
                memory = stores[key]
            result = driver.execute(unit, case, memory)
            if (result.get("error") or {}).get("type") == "BudgetExceeded":
                raise driver.BudgetExceeded("supplement budget reached; incomplete units retained")
    finally:
        for store in stores.values():
            store.close()
    print(json.dumps({"original_checksums_verified_after_run": verify_v1()}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "run"])
    args = parser.parse_args()
    {"prepare": prepare, "run": run}[args.action]()
