"""Project saved results only. No estimators, raw model I/O, network or rendering.

Run only after collection and the separate statistical analysis have terminated.
All input paths and SHA256s must be supplied in an actual binding manifest.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

PAIRS = (("T0", "S0"), ("S0", "SS0"), ("T0", "T1"), ("S0", "S1"), ("SS0", "SS1"))
ARMS = ("T0", "S0", "SS0", "T1", "S1", "SS1")
METRICS = ("provider_input_tokens", "observable_ipc_bytes", "quality", "wall_sec")
CONDITIONS = ("memory_related", "memory_unrelated", "memory_stale", "memory_wrong")
BASE_RENDERER_SHA = "6e87b343e50323ac86e36419418440ec2254c1e4cfc4a866ef221b4ca9f050df"
FONT_SHA = "7076eda296dba38bdd60a14d7259b6ed1470dddf66d1e05a88134e65e4d073f3"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def interval(value):
    if value is None:
        return None, None
    require(isinstance(value, list) and len(value) == 2, "malformed saved CI")
    require(all(type(v) in (int, float) and math.isfinite(v) for v in value), "nonfinite saved CI")
    require(value[0] <= value[1], "reversed saved CI")
    return tuple(value)


def number(value):
    if value in (None, "", "null", "None", "UNKNOWN"):
        return None
    value = float(value)
    require(math.isfinite(value), "nonfinite saved number")
    return value


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def dump_csv(path, rows):
    require(bool(rows), "empty output table")
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def verify_projection(data_path):
    """Keep packaged CSVs and the rendered JSON bound to one actual projection."""
    require(data_path.name == "figure_data.json", "expected actual projection file")
    receipt = load(data_path.parent / "projection_receipt.json")
    require(receipt["status"] == "ACTUAL_DATA_PROJECTION_COMPLETE_NO_STATISTICS_OR_RENDERING",
            "actual projection receipt required")
    for name, record in receipt["outputs"].items():
        require(Path(name).name == name, "projection member must be a basename")
        path = data_path.parent / name
        require(path.is_file() and sha(path) == record["sha256"] and path.stat().st_size == record["bytes"],
                "projected data changed: " + name)
    require("figure_data.json" in receipt["outputs"], "projection JSON is not receipt-bound")
    return receipt


def bound(manifest, name):
    item = manifest["bindings"][name]
    path = Path(item["path"]).resolve()
    require(path.is_file() and len(item["sha256"]) == 64, "missing actual binding: " + name)
    require(sha(path) == item["sha256"].lower(), "input SHA differs: " + name)
    return path


def primary_rows(path, batch_id, is_new):
    doc = load(path)
    require(doc["planned_units"] == doc["observed_units"] == 2160 and doc["missing_units"] == 0,
            "primary must contain the completed 2160-unit main batch")
    require(doc["collection_status"] == "COMPLETE", "primary collection is not terminal COMPLETE")
    require(doc["formal_unique_tasks"] == 120 and doc["state_applicable_unique_tasks"] == 108,
            "primary populations differ from fixed 120/108")
    require(doc["primary_family_test_count"] == 10 and doc["active_confirmatory_families"] == 1,
            "primary family differs; presentation never constructs a new family")
    matches = [(i, s) for i, s in enumerate(doc["strata"])
               if s["model"] == "main" and s["topology"] == "runtime_uds"]
    require(len(matches) == 1, "expected one main/runtime_uds stratum")
    si, stratum = matches[0]
    comparisons = stratum["comparisons"]
    require([(c["baseline"], c["target"]) for c in comparisons] == list(PAIRS), "five-pair order differs")
    rows = []
    for ci, comp in enumerate(comparisons):
        tasks = 108 if PAIRS[ci] == ("S0", "SS0") else 120
        require(comp["planned_unique_tasks"] == tasks and comp["planned_chains"] == 20
                and comp["planned_repeats"] == 3 and comp["planned_paired_units"] == tasks * 3,
                "pair denominator/population differs")
        for metric in METRICS:
            endpoint = comp["quality"] if metric == "quality" else comp["metrics"][metric]
            complete = endpoint["status"] == "COMPLETE"
            require(not is_new or complete, "new batch endpoint not complete: " + metric)
            if complete:
                require(endpoint.get("unique_tasks") == tasks and endpoint.get("chain_count") == 20,
                        "endpoint task/chain denominator differs")
                for key in ("baseline_mean", "target_mean", "paired_difference"):
                    require(number(endpoint.get(key)) is not None, "complete endpoint lacks " + key)
                lo, hi = interval(endpoint.get("difference_ci95"))
                require(lo is not None, "complete primary endpoint lacks saved absolute CI")
                rlo, rhi = interval(endpoint.get("relative_change_ci95"))
            else:
                # The old five-token INCOMPLETE results remain explicitly missing.
                require(not is_new and metric == "provider_input_tokens" and endpoint["status"] == "INCOMPLETE",
                        "unexpected incomplete endpoint")
                lo = hi = rlo = rhi = None
            pointer = f"/strata/{si}/comparisons/{ci}/" + ("quality" if metric == "quality" else "metrics/" + metric)
            rows.append(dict(batch_id=batch_id, baseline=comp["baseline"], target=comp["target"], metric=metric,
                unique_tasks=tasks, chain_count=20, repeats=3, paired_units=tasks * 3, status=endpoint["status"],
                baseline_mean=endpoint.get("baseline_mean") if complete else None,
                target_mean=endpoint.get("target_mean") if complete else None,
                paired_difference=endpoint.get("paired_difference") if complete else None,
                ci95_low=lo, ci95_high=hi, relative_change=endpoint.get("relative_change") if complete else None,
                relative_ci95_low=rlo, relative_ci95_high=rhi,
                source_pointer=pointer, source_sha256=sha(path), inference_scope="saved original analysis; no recomputation"))
    return rows


def timing_rows(path):
    result = []
    for row in csv_rows(path):
        # Same figure_04.csv projection as the completed Chinese delivery.
        exclusive = row.get("kind") == "recorded_exclusive_partition_mass"
        if not exclusive:
            if row.get("phase") != "main" or row.get("model") != "main" or row.get("topology") != "runtime_uds":
                continue
            if row.get("population_facet") != "all_formal" or row.get("dimension") != "overall":
                continue
        for key in ("value", "ci95_low", "ci95_high", "p50", "p95", "p99", "value_sec"):
            if key in row:
                row[key] = number(row[key])
        result.append(row)
    for metric in ("wall_sec", "provider_wait_fraction"):
        selected = [r for r in result if r.get("metric") == metric]
        require(len(selected) == 6 and {r["arm"] for r in selected} == set(ARMS), "incomplete timing summary")
        require(all(int(r["planned_units"]) == int(r["terminal_units"]) == int(r["known_units"]) == 360
                    and int(r["unknown_units"]) == int(r["missing_units"]) == 0 for r in selected),
                "timing summary does not cover all planned units")
    exclusive = [r for r in result if r.get("kind") == "recorded_exclusive_partition_mass"]
    require(bool(exclusive), "exclusive partition absent; do not invent read/queue spans")
    for category in {r["category"] for r in exclusive}:
        selected = [r for r in exclusive if r["category"] == category]
        require(len(selected) == 6 and {r["arm"] for r in selected} == set(ARMS)
                and all(r["value_sec"] is not None for r in selected), "partial exclusive category")
    return result


def seed_rows(path, summary):
    require(summary["status"] == "COMPLETE_720_REAL_LOCAL_SEED_WRITES_WITH_TIMING"
            and summary["new_initialization_measurements"] == 720 and summary["model_API_network_calls"] == 0,
            "seed supplement not terminal or not initialization-only")
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    require(len(rows) == 720 and len({r["run_id"] for r in rows}) == 720, "seed row count/identity differs")
    projected = []
    for row in rows:
        require(row["status"] == "SUCCESS_LOCAL_SEED_WRITE" and row["full_model_task_run"] is False,
                "seed write failed or is a full model task")
        require(row["logical_write_span_count"] == 1 and row["child_index_span_count"] == 2
                and row["child_spans_enclosed"] is True, "seed write/index span boundary differs")
        values = [row["write_inclusive_sec"], row["write_exclusive_partition_sec"]["memory"],
                  row["write_exclusive_partition_sec"]["memory_index"]]
        require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values),
                "missing/nonfinite seed duration; never impute zero")
        projected.append(dict(condition=row["condition"], run_id=row["run_id"], original_run_id=row["original_run_id"],
            task_id=row["task_id"], original_repeat=row["original_repeat"],
            write_inclusive_sec=row["write_inclusive_sec"],
            memory_exclusive_sec=row["write_exclusive_partition_sec"]["memory"],
            index_exclusive_sec=row["write_exclusive_partition_sec"]["memory_index"],
            source_line=len(projected)+1))
    require(all(sum(r["condition"] == c for r in projected) == 180 for c in CONDITIONS), "seed condition coverage differs")
    return projected


def stress_rows(path, identity, method):
    require(identity.get("status") == "ALL30_CELLS_ORIGINAL_POINTS_AND_DENOMINATORS_EXACT"
            and len(identity.get("cells", [])) == 30,
            "pressure source requires an exact old-point identity receipt")
    result = csv_rows(path)
    require(len(result) == 30 and len({(r["length"], r["concurrency"]) for r in result}) == 30,
            "pressure cell coverage differs")
    require(len({r["length"] for r in result}) == 6 and len({r["concurrency"] for r in result}) == 5,
            "expected the original 6-by-5 pressure grid")
    for row in result:
        require(row["status"] == "POSTCOLLECTION_POINTWISE_CI" and int(row["recorded_batches"]) == 30
                and int(row["bootstrap_draws"]) == 10000 and int(row["master_seed"]) == 20261021,
                "pressure supplement method differs")
        for key in row:
            if key not in ("status", "sampling_unit", "provider_tokens"):
                row[key] = number(row[key])
    require(bool(method), "pressure method must be bound")
    return result


def prepare(manifest_path, out):
    manifest = load(manifest_path)
    require(manifest["schema_version"] == "completion-figure-inputs-v1"
            and manifest["status"] == "ACTUAL_COMPLETED_INPUTS", "actual binding manifest required")
    names = ("old_primary", "new_primary", "new_timing_csv", "seed_measurements_jsonl", "seed_result",
             "stress_csv", "stress_point_identity", "stress_method", "old_delivery_manifest", "font")
    sources = {name: bound(manifest, name) for name in names}
    require(sha(sources["old_primary"]) != sha(sources["new_primary"]), "old/new batch inputs are identical")
    require(manifest["old_batch_id"] != manifest["new_batch_id"], "old/new batch labels must be separate")
    require(sha(sources["font"]) == FONT_SHA, "use the approved static Regular400 Chinese font")
    require(sha(Path(__file__).with_name("base_renderer_v8.py")) == BASE_RENDERER_SHA, "base renderer changed")
    out = out.resolve()
    # Fail before writing if a caller accidentally selects an old input directory.
    require(not out.exists(), "destination must be fresh")
    require(not out.is_relative_to(Path(manifest["old_delivery_root"]).resolve()), "old delivery is read-only")
    require(all(out not in p.parents and p not in out.parents for p in sources.values()), "input/output ancestry overlap")
    paired = (primary_rows(sources["old_primary"], manifest["old_batch_id"], False)
              + primary_rows(sources["new_primary"], manifest["new_batch_id"], True))
    timing = timing_rows(sources["new_timing_csv"])
    seed = seed_rows(sources["seed_measurements_jsonl"], load(sources["seed_result"]))
    stress = stress_rows(sources["stress_csv"], load(sources["stress_point_identity"]), load(sources["stress_method"]))
    out.mkdir(parents=True, exist_ok=False)
    tables = {"figure_10_pairs.csv": paired, "figure_11_timing.csv": timing,
              "figure_12_seed.csv": seed, "figure_13_stress.csv": stress}
    for name, rows in tables.items():
        dump_csv(out/name, rows)
    payload = dict(schema_version="completion-figure-data-v1", status="PROJECTED_SAVED_VALUES_ONLY",
        old_batch_id=manifest["old_batch_id"], new_batch_id=manifest["new_batch_id"],
        paired=paired, timing=timing, seed=seed, stress=stress,
        binding_manifest={"path": str(Path(manifest_path).resolve()), "sha256": sha(manifest_path)},
        bindings=manifest["bindings"], old_delivery_root=manifest["old_delivery_root"],
        limits=["旧2160与新2160独立列示，不拼池；公共校准不进入推断。",
                "旧五token主比较仍未完成；新批次结果不能回填旧107项。",
                "质量为canonical自动诊断/动作fixture，不是通用Linux准确率；不作不劣声明。",
                "主拓扑runtime_uds；运行时IPC字节不能外推独立Agent进程总线成本。",
                "wall是guest单调计时，SDK等待不是纯推理或provider排队。",
                "初始化补测不回填历史720运行/1440未知；inclusive父时长不与index子时长相加。",
                "压力CI为采后、逐格pointwise批次bootstrap，不是同步曲线带；非provider token。",
                "原5/6机制点与9外部不可比边界逐字节保留。"])
    write_json(out/"figure_data.json", payload)
    receipt = dict(status="ACTUAL_DATA_PROJECTION_COMPLETE_NO_STATISTICS_OR_RENDERING",
        source_sha256=sha(__file__), manifest_sha256=sha(manifest_path), source_bindings=manifest["bindings"],
        row_counts={k: len(v) for k, v in tables.items()},
        outputs={p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()},
        model_calls=0, benchmark_calls=0, statistical_estimators=0, rendered_figures=0)
    write_json(out/"projection_receipt.json", receipt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.manifest, args.out)
