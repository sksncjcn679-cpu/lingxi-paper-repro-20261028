"""Recompute only the independently recorded state-stage supplement.

No model, native transport, old experiment raw, or plotting code is imported.
Missing child phases remain N/A; nested spans never become additive RTT phases.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from itertools import zip_longest
import json
import math
from pathlib import Path
import csv
import re

SIZES = (1024, 4096, 16384, 65536, 262144, 1048576, 4194304)
PATHS = ("copy", "shm", "memfd")
CONDITIONS = ("cold", "hot")
STAGES = (
    "receiver_precheck", "receiver_attach_map_view", "receiver_object_open", "receiver_native_map",
    "receiver_payload_read_integrity", "receiver_payload_buffer_assembly",
    "receiver_handle_map_release", "receiver_descriptor_close",
)
PRODUCER_NS = (
    "producer_prepare_ns", "handoff_roundtrip_ns", "producer_release_ns",
    "task_wall_ns", "producer_cpu_ns", "handoff_producer_cpu_ns",
    "hot_one_time_setup_ns", "receiver_read_verify_ns", "receiver_cpu_ns",
)
CHILD_NS = (
    "common_recvmsg_wait_prefetch_ns", "common_header_assembly_parse_ns",
    "consumer_total_ns", "phase_partition_residual_ns",
)
RESOURCE_FIELDS = (
    "local_materialization_bytes", "socket_observed_bytes", "fd_count_sent",
    "producer_fd_count", "consumer_fd_count", "producer_peak_rss_bytes",
    "consumer_peak_rss_bytes",
)
HEX = frozenset("0123456789abcdef")
SOURCE_IDENTITY = "a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562"
READ_MODES = {
    "copy": {"socket_copy"}, "shm": {"named_shm_readonly"},
    "memfd": {"mmap_private_ro", "mmap_shared_ro", "pread"},
}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def fingerprint(path):
    value = path.stat()
    return {"bytes": value.st_size, "mtime_ns": value.st_mtime_ns}


def is_hash(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def nonnegative_int(value):
    return type(value) is int and value >= 0


def percentile(values, probability):
    """Linear (Hyndman-Fan type7) empirical quantile; no bootstrap implied."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class Checks:
    def __init__(self):
        self.passed = Counter()
        self.failed = Counter()
        self.examples = []

    def check(self, label, condition, **evidence):
        (self.passed if condition else self.failed)[label] += 1
        if not condition and len(self.examples) < 100:
            self.examples.append({"check": label, **evidence})
        return bool(condition)

    def export(self):
        return {"pass_counts": dict(self.passed), "failure_counts": dict(self.failed),
                "failure_examples": self.examples,
                "failure_examples_cap": 100, "all_failure_counts_retained": True}


def metadata(path, checks, evidence):
    try:
        if path.is_symlink():
            raise ValueError("metadata symlink is not accepted")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        evidence[str(path)] = {"sha256": sha(path), **fingerprint(path)}
        return value
    except (OSError, ValueError) as error:
        checks.check("required_metadata_readable", False, path=str(path), error_type=type(error).__name__)
        return {}


def records(path, checks, evidence):
    """Preserve every physical record and source line; invalid rows cause FAIL."""
    if not path.is_file() or path.is_symlink():
        checks.check("raw_regular_file", False, path=str(path))
        return
    before = fingerprint(path)
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        for number, data in enumerate(stream, 1):
            count += 1
            digest.update(data)
            source = {"path": str(path), "source_line": number,
                      "source_record_sha256": hashlib.sha256(data).hexdigest()}
            try:
                row = json.loads(data)
                if not isinstance(row, dict):
                    raise ValueError("non-object record")
            except (ValueError, UnicodeError):
                checks.check("raw_valid_json_object", False, **source)
                row = {}
            yield row, source
    actual = digest.hexdigest()
    stable = fingerprint(path) == before and sha(path) == actual
    checks.check("raw_unchanged_during_analysis", stable, path=str(path))
    evidence[str(path)] = {"sha256": actual, "rows": count, **before,
                           "unchanged_during_analysis": stable}


def union_ns(intervals):
    total, end = 0, None
    for start, stop in sorted(intervals):
        if end is None or start >= end:
            total += stop - start
        elif stop > end:
            total += stop - end
        end = stop if end is None else max(end, stop)
    return total


def inspect_child(row, checks, source):
    """Validate inclusive phase coordinates; retain declared N/A instead of0."""
    total = row.get("consumer_total_ns")
    checks.check("consumer_total_nonnegative_ns", nonnegative_int(total), **source)
    observation_end = row.get("consumer_observation_end_ns")
    checks.check("consumer_observation_end", nonnegative_int(observation_end) and
                 nonnegative_int(total) and observation_end >= total, **source)
    stages, totals = row.get("stages"), row.get("stage_totals_ns")
    if not checks.check("child_stage_schema", isinstance(stages, list) and isinstance(totals, dict), **source):
        return {}
    computed = defaultdict(int)
    body_intervals = []
    valid_stages = []
    for stage in stages:
        if not checks.check("stage_object", isinstance(stage, dict), **source):
            continue
        name = stage.get("name")
        start, end, duration = (stage.get(k) for k in ("offset_start_ns", "offset_end_ns", "duration_ns"))
        valid = name in STAGES and all(nonnegative_int(v) for v in (start, end, duration))
        limit = observation_end if name == "receiver_descriptor_close" else total
        valid = valid and nonnegative_int(limit) and start <= end <= limit and end - start == duration
        if checks.check("stage_bounds_duration", valid, stage=name, **source):
            computed[name] += duration
            valid_stages.append(stage)
            if name != "receiver_descriptor_close":
                body_intervals.append((start, end))
            if name == "receiver_native_map":
                expected_parent = "receiver_attach_map_view" if row.get("path") == "shm" else None
                checks.check("native_map_declared_nested_parent", "nested_parent" in stage and
                             stage.get("nested_parent") == expected_parent, **source)
    for stage in valid_stages:
        parent = stage.get("nested_parent")
        contained = parent is None or any(outer is not stage and outer["name"] == parent and
            outer["offset_start_ns"] <= stage["offset_start_ns"] <= stage["offset_end_ns"] <= outer["offset_end_ns"]
            for outer in valid_stages)
        checks.check("declared_nested_parent_interval", "nested_parent" in stage and contained,
                     stage=stage["name"], **source)
    checks.check("stage_totals_match_actual_intervals", set(totals) == set(computed) and all(
        nonnegative_int(value) and value == computed[name] for name, value in totals.items()), **source)
    residual = row.get("phase_partition_residual_ns")
    checks.check("phase_union_residual", nonnegative_int(residual) and nonnegative_int(total) and
                 residual == total - union_ns(body_intervals), **source)
    checks.check("phase_error_absent", row.get("phase_error", "MISSING") is None, **source)
    required = {"receiver_payload_read_integrity"}
    if row.get("path") == "copy":
        required.add("receiver_payload_buffer_assembly")
    elif row.get("path") == "shm":
        required.update(("receiver_attach_map_view", "receiver_object_open", "receiver_native_map",
                         "receiver_handle_map_release"))
    elif row.get("path") == "memfd":
        required.update(("receiver_precheck", "receiver_native_map", "receiver_descriptor_close"))
        if row.get("read_mode") in {"mmap_private_ro", "mmap_shared_ro"}:
            required.add("receiver_handle_map_release")
        elif row.get("read_mode") == "pread":
            required.add("receiver_payload_buffer_assembly")
    checks.check("required_actual_path_phases", required <= set(computed), **source)
    for key in ("common_recvmsg_wait_prefetch_ns", "common_header_assembly_parse_ns"):
        checks.check("common_window_nonnegative_ns", nonnegative_int(row.get(key)), field=key, **source)
    return dict(computed)


def source_view(value):
    return isinstance(value, dict) and value.get("aggregate_sha256") == SOURCE_IDENTITY and \
        is_hash(value.get("baseline_driver_sha256")) and isinstance(value.get("native_files"), dict) and \
        bool(value["native_files"]) and all(is_hash(digest) for digest in value["native_files"].values())


def source_stability(record, checks, label, **context):
    before, after = record.get("source_before"), record.get("source_after")
    checks.check(label, record.get("source_stable") is True and source_view(before) and
                 before == after, **context)


def fd_targets(value):
    if isinstance(value, dict) and all(isinstance(v, str) for v in value.values()):
        return Counter(value.values())
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return Counter(value)
    return None


def cleanup(record, checks, batch):
    before = fd_targets(record.get("fd_targets_before"))
    after = fd_targets(record.get("fd_targets_after"))
    declared = fd_targets(record.get("new_fd_targets"))
    actual = after - before if before is not None and after is not None else None
    known_helpers = record.get("known_helper_fd_targets")
    helpers_valid = isinstance(known_helpers, dict) and all(isinstance(target, str) for target in known_helpers.values())
    checks.check("batch_cleanup_shm", record.get("shm_residue") == [], batch=batch)
    checks.check("batch_reported_cleanup_ok", record.get("cleanup_ok") is True, batch=batch)
    checks.check("batch_cleanup_fd_evidence", actual is not None and declared is not None and actual == declared,
                 batch=batch)
    checks.check("batch_cleanup_fd_bound", actual is not None and
                 record.get("allowed_new_helper_fds_bound") == 5 and
                 sum(actual.values()) <= 5 and all(re.fullmatch(r"pipe:\[\d+\]", target) for target in actual),
                 batch=batch)
    checks.check("batch_new_pipes_are_observed_known_helpers", helpers_valid and actual is not None and
                 all(target in known_helpers.values() for target in actual), batch=batch)


def metric_summary(result, name, values, expected, time_unit=False):
    result[name + "_n"] = len(values)
    result[name + "_na"] = expected - len(values)
    statistics = {"mean": math.fsum(values) / len(values) if values else None,
                  "p50": percentile(values, .5), "p95": percentile(values, .95),
                  "p99": percentile(values, .99)}
    for statistic, value in statistics.items():
        if time_unit:
            result[name + "_" + statistic + "_ns"] = value
            result[name + "_" + statistic + "_ms"] = value / 1_000_000 if value is not None else None
        else:
            result[name + "_" + statistic] = value


def analyze(root, destination=None):
    root = Path(root).resolve()
    destination = Path(destination).resolve() if destination is not None else root / "analysis/recomputed_v2"
    if destination.exists():
        raise RuntimeError("destination must be new; prior audits/results are never overwritten")
    if destination == root or root.is_relative_to(destination) or destination.is_relative_to(root / "raw"):
        raise RuntimeError("analysis destination overlaps source root/raw")
    destination.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    checks, evidence = Checks(), {}
    cells = {(batch, path, size, condition): {"measured": [], "warmup": [], "values": defaultdict(list),
                                             "producer_pids": set(), "consumer_pids": set(), "successes": 0,
                                             "read_modes": Counter(), "stage_error_events": Counter()}
             for batch in (1, 2, 3) for path in PATHS for size in SIZES for condition in CONDITIONS}
    joined, producers, consumers = 0, 0, 0
    unexpected = 0
    recorded_stage_error_types = Counter()
    fatal = None
    try:
        registration = metadata(root / "prospective_registration.json", checks, evidence)
        manifest = metadata(root / "manifest.json", checks, evidence)
        execution = metadata(root / "analysis/execution.json", checks, evidence)
        checks.check("overall_completed", execution.get("status") == "COMPLETED" and
                     execution.get("measured_rows") == 25200 and execution.get("warmup_rows") == 2520 and
                     execution.get("total_transfers") == 27720 and execution.get("batches") == 3)
        steps = execution.get("steps")
        checks.check("three_actual_batch_steps", isinstance(steps, list) and len(steps) == 3 and
                     all(s.get("status") == "PASS" and s.get("exit_code") == 0 for s in steps))
        # Exact driver/source/cleanup evidence keys are intentionally explicit.
        # Missing evidence is not inferred from a successfully parsed schema.
        checks.check("registration_present", bool(registration))
        checks.check("manifest_present", bool(manifest))
        expected_identity = registration.get("source_identity_sha256")
        checks.check("registered_source_identity", expected_identity == SOURCE_IDENTITY)
        registration_sha = evidence.get(str(root / "prospective_registration.json"), {}).get("sha256")
        checks.check("registration_manifest_execution_sha", is_hash(registration_sha) and
                     manifest.get("registration_sha256") == registration_sha == execution.get("prospective_registration_sha256"))
        checks.check("manifest_analyzer_driver_identity", manifest.get("schema_version") == 1 and
                     manifest.get("analyzer_sha256") == sha(Path(__file__)) and
                     manifest.get("analyzer_sha256") == registration.get("analyzer_sha256") and
                     is_hash(manifest.get("driver_sha256")) and manifest.get("driver_sha256") == registration.get("driver_sha256") and
                     manifest.get("source_identity_sha256") == SOURCE_IDENTITY)
        checks.check("manifest_execution_identity", manifest.get("status") == "COMPLETED" and
                     manifest.get("execution_sha256") == evidence.get(str(root / "analysis/execution.json"), {}).get("sha256"))
        checks.check("manifest_native_source_stable", source_view(manifest.get("source_before")) and
                     manifest.get("source_before") == manifest.get("source_after"))
        source_stability(execution, checks, "execution_native_source_stable")
        checks.check("execution_same_registered_native_source", execution.get("source_before") == manifest.get("source_before"))
        registered_inputs = registration.get("registered_input_sha256")
        checks.check("registered_original_inputs_stable", execution.get("registered_inputs_stable") is True and
                     isinstance(registered_inputs, dict) and bool(registered_inputs) and
                     all(is_hash(value) for value in registered_inputs.values()) and
                     execution.get("registered_input_sha256_after") == registered_inputs)
        for batch in (1, 2, 3):
            batch_meta = metadata(root / f"analysis/batch_{batch}.json", checks, evidence)
            child_meta_path = root / f"analysis/child_{batch}.json"
            child_meta = metadata(child_meta_path, checks, evidence)
            checks.check("batch_child_metadata_identity", child_meta.get("status") == "COMPLETED" and
                         child_meta.get("rows") == 9240 and child_meta.get("error") is None and
                         batch_meta.get("child_status_sha256") == evidence.get(str(child_meta_path), {}).get("sha256"), batch=batch)
            proofs = child_meta.get("ast_proofs", {})
            proof_keys = {"stdlib_shared_memory_init", "verify_sealed_fd", "original_consumer"}
            checks.check("recorded_ast_proof_set", isinstance(proofs, dict) and set(proofs) == proof_keys, batch=batch)
            for proof_name in proof_keys:
                proof = proofs.get(proof_name, {}) if isinstance(proofs, dict) else {}
                checks.check("recorded_ast_equivalence_and_source_hash", proof.get("stripped_ast_equivalent") is True and
                             is_hash(proof.get("source_file_sha256")) and is_hash(proof.get("instrumented_ast_sha256")) and
                             is_hash(proof.get("original_ast_sha256")) and
                             proof.get("original_ast_sha256") == proof.get("stripped_ast_sha256"), batch=batch, proof=proof_name)
            checks.check("recorded_original_consumer_source", proofs.get("original_consumer", {}).get("source_file_sha256") ==
                         manifest.get("source_before", {}).get("baseline_driver_sha256"), batch=batch)
            expected_fd_source = manifest.get("source_before", {}).get("native_files", {}).get(
                "src/agent_runtime/state_exchange/fd_passing.py")
            checks.check("recorded_memfd_native_source", is_hash(expected_fd_source) and
                         proofs.get("verify_sealed_fd", {}).get("source_file_sha256") == expected_fd_source, batch=batch)
            checks.check("batch_closed_counts", batch_meta.get("status") == "COMPLETED" and
                         batch_meta.get("measured_rows") == 8400 and batch_meta.get("warmup_rows") == 840 and
                         batch_meta.get("producer_rows") == 9240 and batch_meta.get("consumer_rows") == 9240 and
                         batch_meta.get("consumer_exit") == 0, batch=batch)
            cleanup(batch_meta, checks, batch)
            source_stability(batch_meta, checks, "batch_native_source_stable", batch=batch)
            checks.check("batch_same_registered_native_source", batch_meta.get("source_before") == manifest.get("source_before"), batch=batch)
            paths = [root / f"raw/producer_batch_{batch}.jsonl", root / f"raw/child_stage_batch_{batch}.jsonl"]
            before_counts = producers, consumers, joined
            for ordinal, pair in enumerate(zip_longest(*(records(path, checks, evidence) for path in paths))):
                producer_item, child_item = pair
                producers += producer_item is not None
                consumers += child_item is not None
                if not checks.check("strict_one_to_one_join", producer_item is not None and child_item is not None,
                                    batch=batch, expected_ordinal=ordinal):
                    continue
                p, ps = producer_item
                c, cs = child_item
                identity = (p.get("batch"), p.get("transfer_ordinal"))
                valid_id = identity == (batch, ordinal) == (c.get("batch"), c.get("transfer_ordinal"))
                checks.check("real_sequential_transfer_identity", valid_id, batch=batch, expected_ordinal=ordinal,
                             producer_source_line=ps["source_line"], child_source_line=cs["source_line"])
                joined += 1
                key = (batch, p.get("path"), p.get("logical_bytes"), p.get("condition"))
                if key not in cells:
                    unexpected += 1
                    checks.check("expected_cell", False, **ps)
                    continue
                cell = cells[key]
                iteration, warmup = p.get("iteration"), p.get("warmup")
                valid_iteration = type(iteration) is int and type(warmup) is bool and (
                    (-20 <= iteration < 0) if warmup else (0 <= iteration < 200))
                checks.check("iteration_warmup_identity", valid_iteration, **ps)
                if valid_iteration:
                    cell["warmup" if warmup else "measured"].append(iteration)
                checks.check("producer_consumer_facts_match", all(p.get(field) == c.get(field) for field in
                             ("batch", "transfer_ordinal", "path", "logical_bytes", "payload_sha256", "consumer_pid", "read_mode")), **ps)
                checks.check("actual_positive_process_ids", all(type(p.get(field)) is int and p[field] > 0 for field in
                             ("producer_pid", "consumer_pid")) and p.get("producer_pid") != p.get("consumer_pid"), **ps)
                checks.check("process_ids_match_actual_batch_child", p.get("producer_pid") == batch_meta.get("producer_pid") and
                             p.get("consumer_pid") == child_meta.get("consumer_pid"), **ps)
                checks.check("payload_sha256_present", is_hash(p.get("payload_sha256")), **ps)
                checks.check("actual_read_mode", p.get("read_mode") in READ_MODES[p["path"]], **ps)
                checks.check("source_identity_match", is_hash(expected_identity) and
                             p.get("source_identity_sha256") == expected_identity == c.get("source_identity_sha256"), **ps)
                checks.check("transfer_success", p.get("success") is True and c.get("success") is True, **ps)
                checks.check("receiver_total_matches_ack", nonnegative_int(c.get("consumer_total_ns")) and
                             c.get("consumer_total_ns") == p.get("receiver_read_verify_ns"), **ps)
                expected_fd = int(p.get("path") == "memfd")
                checks.check("actual_descriptor_handoff", p.get("fd_count_sent") == expected_fd ==
                             c.get("frame_received_fd_count"), **ps)
                phases = inspect_child(c, checks, cs)
                for stage in c.get("stages", []):
                    if isinstance(stage, dict) and isinstance(stage.get("error_type"), str):
                        recorded_stage_error_types[(p["path"], str(stage.get("name")), stage["error_type"])] += 1
                for field in PRODUCER_NS + RESOURCE_FIELDS:
                    checks.check("producer_numeric_nonnegative", nonnegative_int(p.get(field)), field=field, **ps)
                if valid_iteration and not warmup:
                    cell["successes"] += p.get("success") is True and c.get("success") is True
                    if isinstance(p.get("read_mode"), str):
                        cell["read_modes"][p["read_mode"]] += 1
                    for stage in c.get("stages", []):
                        if isinstance(stage, dict) and stage.get("error_type") is not None:
                            cell["stage_error_events"][str(stage.get("name"))] += 1
                    for field in ("producer_pid", "consumer_pid"):
                        if type(p.get(field)) is int:
                            cell[field + "s"].add(p[field])
                    for field in PRODUCER_NS + RESOURCE_FIELDS:
                        if nonnegative_int(p.get(field)):
                            cell["values"][field].append(p[field])
                    for field in CHILD_NS:
                        if nonnegative_int(c.get(field)):
                            cell["values"][field].append(c[field])
                    for name, value in phases.items():
                        cell["values"][name].append(value)
            checks.check("batch_actual_raw_counts", tuple(a - b for a, b in zip((producers, consumers, joined), before_counts))
                         == (9240, 9240, 9240), batch=batch)
            for path, declared_name in zip(paths, ("producer_sha256", "consumer_sha256")):
                recorded = evidence.get(str(path), {})
                checks.check("batch_recorded_raw_sha_match", is_hash(batch_meta.get(declared_name)) and
                             batch_meta.get(declared_name) == recorded.get("sha256"), batch=batch, field=declared_name)
            checks.check("child_raw_identity", child_meta.get("consumer_sha256") ==
                         evidence.get(str(paths[1]), {}).get("sha256"), batch=batch)
            checks.check("child_buffer_and_flush_evidence", child_meta.get("buffer_rows_max") == 9240 and
                         child_meta.get("stage_events_per_transfer_max") == 64 and
                         nonnegative_int(child_meta.get("buffer_deep_size_bytes_at_flush")) and
                         child_meta.get("buffer_deep_size_bytes_at_flush", 0) > 0 and
                         child_meta.get("serialized_bytes") == evidence.get(str(paths[1]), {}).get("bytes") and
                         child_meta.get("flush_after_timed_transfers") is True, batch=batch)
            resource_path = root / f"raw/resources_batch_{batch}.jsonl"
            resource_rows = 0
            prior_time = -1
            for sample, source in records(resource_path, checks, evidence):
                resource_rows += 1
                now = sample.get("monotonic_ns")
                checks.check("resource_sample_time_batch", sample.get("batch") == batch and
                             nonnegative_int(now) and now >= prior_time, **source)
                if nonnegative_int(now):
                    prior_time = now
                processes = sample.get("processes")
                checks.check("resource_process_list", isinstance(processes, list) and bool(processes), **source)
                for process in processes if isinstance(processes, list) else []:
                    valid = isinstance(process, dict) and type(process.get("pid")) is int and process["pid"] > 0 and \
                        nonnegative_int(process.get("rss")) and nonnegative_int(process.get("fds")) and \
                        type(process.get("cpu_sec")) in (int, float) and math.isfinite(process["cpu_sec"]) and process["cpu_sec"] >= 0
                    checks.check("resource_process_actual_units", valid, **source)
            checks.check("actual_resource_records_and_sha", resource_rows > 0 and
                         is_hash(batch_meta.get("resource_sha256")) and batch_meta.get("resource_sha256") ==
                         evidence.get(str(resource_path), {}).get("sha256"), batch=batch)
    except Exception as error:
        fatal = {"error_type": type(error).__name__, "error_text_sha256": hashlib.sha256(str(error).encode()).hexdigest()}
        checks.check("analysis_completed_without_exception", False, **fatal)

    rows = []
    for (batch, path, size, condition), cell in cells.items():
        measured, warmup = cell["measured"], cell["warmup"]
        checks.check("cell_measured_exact200", sorted(measured) == list(range(200)), batch=batch, path=path,
                     logical_bytes=size, condition=condition)
        checks.check("cell_warmup_exact20", sorted(warmup) == list(range(-20, 0)), batch=batch, path=path,
                     logical_bytes=size, condition=condition)
        row = {"batch": batch, "path": path, "logical_bytes": size, "condition": condition,
               "planned_measured_rows": 200, "observed_measured_rows": len(measured),
               "planned_warmup_rows": 20, "observed_warmup_rows": len(warmup),
               "successful_measured_rows": cell["successes"],
               "producer_pids": json.dumps(sorted(cell["producer_pids"])),
               "consumer_pids": json.dumps(sorted(cell["consumer_pids"])),
               "read_modes": json.dumps(dict(cell["read_modes"]), sort_keys=True),
               "stage_error_events": json.dumps(dict(cell["stage_error_events"]), sort_keys=True)}
        for name in PRODUCER_NS + CHILD_NS + STAGES:
            base = name.removesuffix("_ns")
            metric_summary(row, base, cell["values"][name], len(measured), time_unit=True)
        for name in RESOURCE_FIELDS:
            metric_summary(row, name, cell["values"][name], len(measured))
        rows.append(row)
    checks.check("full_matrix_totals", producers == consumers == joined == 27720 and
                 sum(len(c["measured"]) for c in cells.values()) == 25200 and
                 sum(len(c["warmup"]) for c in cells.values()) == 2520 and unexpected == 0)
    for filename, record in list(evidence.items()):
        path = Path(filename)
        checks.check("all_recorded_inputs_unchanged_at_close", path.is_file() and
                     fingerprint(path) == {"bytes": record["bytes"], "mtime_ns": record["mtime_ns"]} and
                     sha(path) == record["sha256"], path=filename)
    status = "PASS" if not checks.failed else "FAIL_RETAINED"
    with (destination / "stage_cells.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    shared = {"status": status, "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
              "analyzer_sha256": sha(Path(__file__)), "source_root": str(root),
              "paid_api_calls": 0, "source_files": evidence, "fatal_error": fatal,
              "boundary": "Independent actual state supplement only; no old/provider raw, no plotting, no main-effect replacement. Original full-path state matrix remains valid and separate; new phase rows are table4 supplements, never pooled or retrospectively inferred for old rows.",
              "read_integrity_scope": "Payload read/validation includes actual hash traversal; never labelled pure read.",
              "nested_phase_scope": "Phases are inclusive; SHM native_map/object_open nest within attach/view, while memfd map/consume/release are distinct contexts. Repeated or failed-map durations remain in declared stage totals. Stage durations, common windows and consumer total are not added to RTT.",
              "common_window_scope": "recvmsg wait/prefetch and header assembly/parse precede consumer_total; descriptor close may follow it. All remain separately measured, never added to RTT or body-union residual. No end-to-end partition claim.",
              "missing_stage_scope": "Absent phase = N/A; n and na exported per cell. No implicit zero for unmeasured phases.",
              "statistics": "All measured rows including failed transfers retained; warmup20 excluded. Mean and empirical linear type7 p50/p95/p99, ns and ms; no CI or significance claim.",
              "instrumentation_overhead": "NOT_MEASURED_SEPARATELY: timestamp/phase-buffer overhead remains in observed times; no subtraction, paired overhead calibration or pure read claim.",
              "resource_boundary": "Per-transfer producer/consumer ru_maxrss fields are lifetime high-water bytes, not exclusive per-transfer peaks. Resource raw is sampled process-list RSS/CPU/FD evidence. Phase-buffer storage remains included. FD closure uses final before/after actual targets, not ACK-time counts. Repeated hot_one_time_setup values describe the one setup for their cell and must not be summed as per-transfer work.",
              "ast_proof_boundary": "Recorded original/instrumented/stripped AST and source hashes checked for internal identity and declared equivalence; analyzer does not execute or regenerate instrumentation.",
              "recorded_stage_error_types_including_warmup": [
                  {"path": path, "stage": stage, "error_type": error_type, "events": count}
                  for (path, stage, error_type), count in sorted(recorded_stage_error_types.items())],
              "checks": checks.export()}
    join = {**shared, "producer_rows": producers, "consumer_rows": consumers, "joined_rows": joined,
            "join_key": ["batch", "transfer_ordinal"], "expected_raw_rows_including_warmup": 27720,
            "join_order_contract": "Both streams must use each batch ordinal0..9239 exactly once and in order; malformed, duplicate, out-of-order and unpaired rows fail rather than deduplicate."}
    matrix = {**shared, "planned_cells": 126, "exported_cells": len(rows), "planned_measured_rows": 25200,
              "observed_measured_rows": sum(len(c["measured"]) for c in cells.values()),
              "planned_warmup_rows": 2520, "observed_warmup_rows": sum(len(c["warmup"]) for c in cells.values()),
              "stage_cells_sha256": sha(destination / "stage_cells.csv")}
    for filename, value in (("stage_join_audit.json", join), ("matrix_audit.json", matrix)):
        with (destination / filename).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.destination)
    print(json.dumps({"status": result["status"], "output": str(args.destination), "paid_api_calls": 0}))
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
