"""Rebuild protocol figures/tables from real formal phases and labelled offline data.

Incomplete phases produce availability records, never effect numbers filled with
public calibration or invented zero costs. Raw evidence is only read, never edited.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import beta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from formal_statistics import analyze as frozen_analyze, cluster_bootstrap, quality_safety_bound, PAIRS
from formal_statistics_protocol_aligned_v3 import analyze as protocol_analyze
from formal_recording import sha as recorded_sha

ARMS = ("T0", "S0", "SS0", "T1", "S1", "SS1")
DOMAINS = ("service", "migration", "resource", "network")
COLORS = {"T0": "#183153", "T1": "#183153", "S0": "#087F8C", "S1": "#087F8C", "SS0": "#6356A5", "SS1": "#6356A5"}
GREYS = ("#374151", "#6B7280", "#9CA3AF", "#4B5563", "#B8BDC6")
RISK = "#D96C3F"
METRICS = ("provider_input_tokens", "provider_output_tokens", "provider_total_tokens", "observable_ipc_bytes",
           "local_state_materialized_bytes", "logical_state_bytes", "provider_attempts", "wall_sec", "provider_wait_sec",
           "runtime_cpu_sec", "sampled_peak_process_group_rss", "sampled_peak_process_group_fds")
BOOTSTRAPS = 10000
# Exact bytes of the pre-main registered selection metadata; a relocated
# reproducer copies this file instead of drawing a new sample.
REGISTERED_SPOTCHECK_PLAN_SHA256 = "66bbf35fd4b1c39abdfe0acc36505a19446acc5427faede4f026388d8ddd44df"
REGISTERED_SPOTCHECK_SELECTOR_SHA256 = "401db69f9fc77d638dc5e616f02f032e671e0794306c8eeb18aaa84b1b8acc42"


def json_read(path, fallback=None):
    return json.loads(path.read_text()) if path.exists() else fallback


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def csv_read(path):
    if not path.exists():
        return []
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(k for row in rows for k in row)) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def complete_phase(plan, results):
    ids = [row["run_id"] for row in plan]
    actual = [row["run_id"] for row in results]
    if len(set(ids)) != len(ids) or len(set(actual)) != len(actual):
        raise ValueError("duplicate planned or terminal run ID; no silent deduplication")
    return bool(plan) and set(actual) == set(ids)


def cluster_mean(rows, metric, seed=20261019):
    """Mean task value; repeats averaged first, domain-stratified chain bootstrap."""
    if not rows or not all(finite(row.get(metric)) for row in rows):
        return None, None, None
    tasks = defaultdict(list)
    for row in rows:
        tasks[(row["domain"], row["chain_id"], row["task_id"])].append(row[metric])
    chains = defaultdict(list)
    for (domain, chain, task), values in tasks.items():
        chains[(domain, chain)].append(float(np.mean(values)))
    grouped = defaultdict(list)
    for (domain, chain), values in chains.items():
        grouped[domain].append((len(values), float(np.mean(values))))
    rng = np.random.default_rng(seed)
    numerator, denominator = np.zeros(BOOTSTRAPS), np.zeros(BOOTSTRAPS)
    for domain, values in sorted(grouped.items()):
        lengths = np.asarray([v[0] for v in values])
        means = np.asarray([v[1] for v in values])
        selected = rng.integers(0, len(values), size=(BOOTSTRAPS, len(values)))
        numerator += (lengths[selected]*means[selected]).sum(axis=1)
        denominator += lengths[selected].sum(axis=1)
    low, high = np.quantile(numerator/denominator, [.025, .975])
    return float(np.mean([np.mean(v) for v in tasks.values()])), float(low), float(high)


def quality_interval(rows):
    """Conservative domain-stratified exact chain-event CI, including all-pass."""
    if not rows:
        return None, None, None
    chains = defaultdict(list)
    for row in rows:
        chains[(row["domain"],row["chain_id"])].append(row)
    domains=defaultdict(list)
    for (domain,chain),values in chains.items():
        domains[domain].append({"tasks":len({r['task_id'] for r in values}),
            "any_failure":any(not r["success"] for r in values),"any_success":any(r["success"] for r in values)})
    total_tasks=sum(c["tasks"] for values in domains.values() for c in values)
    lower=upper=0.
    alpha=.05/(2*len(domains))
    for values in domains.values():
        n=len(values); domain_tasks=sum(c["tasks"] for c in values)
        factor=n*max(c["tasks"] for c in values)/domain_tasks
        failure=sum(c["any_failure"] for c in values)
        success=sum(c["any_success"] for c in values)
        failure_upper=1. if failure==n else float(beta.ppf(1-alpha,failure+1,n-failure))
        success_upper=1. if success==n else float(beta.ppf(1-alpha,success+1,n-success))
        weight=domain_tasks/total_tasks
        lower+=weight*max(0.,1-factor*failure_upper)
        upper+=weight*min(1.,factor*success_upper)
    return float(np.mean([r["success"] for r in rows])), lower, upper


class Report:
    def __init__(self, out, destination, retrieval_root=None):
        self.out, self.destination = out, destination
        for name in ("analysis", "figures", "tables"):
            (destination/name).mkdir(parents=True, exist_ok=True)
        self.plan = json_read(out/"manifests/formal_plan.json", [])
        corpus_path=out/"corpus/automatic_validated_v1/tasks.jsonl"
        self.applicable_task_ids={r["task_id"] for r in jsonl(corpus_path)
            if r["split"]=="formal" and r["state_spec"]["applicable"]}
        self.all_results = jsonl(out/"raw/task_runs.jsonl")
        planned_ids = {row["run_id"] for row in self.plan}
        unknown = [r["run_id"] for r in self.all_results if r["run_id"] not in planned_ids]
        if unknown:
            raise ValueError(f"{len(unknown)} real terminal rows lack a frozen planned ID")
        if any(r.get("model_evidence") != "real_provider" for r in self.all_results):
            raise ValueError("non-real fixture found in the real terminal ledger; isolate it before reporting")
        self.phases = sorted({row["phase"] for row in self.plan if row["phase"] != "public_calibration"})
        self.figures, self.tables, self.claims, self.sources = {}, {}, [], {}
        selection_path = out/"analysis/final_artifact_selection_v2.json"
        selection = json_read(selection_path, {})
        selected_root = (out/selection["authoritative_retrieval_root"]).resolve() if selection else out.resolve()
        self.retrieval_out = Path(retrieval_root).resolve() if retrieval_root else selected_root
        if selection and self.retrieval_out != selected_root:
            raise ValueError("retrieval root differs from the registered input-correction selection")
        if self.retrieval_out != out.resolve():
            if not self.retrieval_out.is_relative_to(out.resolve()):
                raise ValueError("selected retrieval evidence must be inside the current evidence view")
            registration_path = self.retrieval_out/"analysis/before_new_outcome_registration.json"
            replay_path = self.retrieval_out/"analysis/retrieval_v2_analysis.json"
            registration, replay = json_read(registration_path, {}), json_read(replay_path, {})
            query_path = self.retrieval_out/"corpus/queries.jsonl"
            if not registration.get("before_new_outcome") or registration.get("corrected_unique_texts") != 240 or \
               digest(query_path) != registration.get("queries_v2_sha256") or \
               (selection and digest(query_path) != selection.get("authoritative_queries_sha256")):
                raise ValueError("selected retrieval input identity or distinct-query registration is invalid")
            if replay.get("status") != "COMPLETE_REQUIRES_REPORT_NUMERIC_AND_VISUAL_QA" or \
               replay.get("observed_query_units") != 14400 or replay.get("planned_query_units") != 14400 or \
               replay.get("cells") != 60 or replay.get("actual_native_unique_text_input_count_per_semantic_hybrid_cell") != 240:
                raise ValueError("corrected full retrieval matrix has not been independently analyzed")
            table_path = self.retrieval_out/"tables/retrieval_quality_scale.csv"
            if digest(table_path) != replay.get("table_sha256"):
                raise ValueError("selected retrieval table changed after analysis")
            for relative, expected_sha in replay["raw_sha256"].items():
                raw_path = (self.retrieval_out/relative).resolve()
                if not raw_path.is_relative_to(self.retrieval_out) or digest(raw_path) != expected_sha:
                    raise ValueError("selected retrieval raw evidence changed after analysis")
                self.source(raw_path)
            for path in (selection_path, registration_path, replay_path, query_path):
                self.source(path)
        self.source(out/"manifests/formal_plan.json")
        self.source(corpus_path)
        self.source(out/"raw/task_runs.jsonl")
        self.source(out/"raw/provider_calls.jsonl")
        self.source(Path(__file__))
        self.source(Path(__file__).with_name("formal_statistics.py"))
        self.source(Path(__file__).with_name("formal_statistics_protocol_aligned_v3.py"))
        self.source(out/"analysis/report_protocol_alignment_v3.json")
        self.source(out/"analysis/report_spotcheck_authority_alignment.json")
        plt.rcParams.update({"font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
            "legend.fontsize": 6, "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
            "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "white",
            "axes.facecolor": "white", "savefig.facecolor": "white", "axes.prop_cycle": plt.cycler(color=GREYS)})

    def source(self, path):
        self.sources[str(path)] = digest(path)

    def phase(self, phase):
        plan = [r for r in self.plan if r["phase"] == phase]
        results = [r for r in self.all_results if r["phase"] == phase]
        return plan, results, complete_phase(plan, results)

    def table(self, number, rows, complete, caption, sources):
        self.tables[f"table_{number:02}"] = {"status": "COMPLETE" if complete else "INCOMPLETE", "caption": caption,
            "rows": len(rows), "source_paths": [str(s) for s in sources]}
        write_csv(self.destination/"tables"/f"table_{number:02}.csv", rows)
        for source in sources:
            self.source(source)

    def figure(self, number, rows, complete, caption, sources, draw=None, height=3.2):
        key = f"figure_{number:02}"
        metadata = {"status": "COMPLETE" if complete else "INCOMPLETE", "caption": caption,
                    "source_paths": [str(s) for s in sources], "csv_rows": len(rows),
                    "CI": "specified in caption and CSV", "exports": []}
        write_csv(self.destination/"figures"/(key+".csv"), rows,
                  fields=None if rows else ["status", "scope", "metric", "value", "ci95_low", "ci95_high"])
        for source in sources:
            self.source(source)
        if complete and draw:
            for label, width in (("single", 3.35), ("double", 7.0)):
                fig = draw(width, height if label == "double" else max(height, height*1.12))
                fig.set_layout_engine("constrained")
                for ext in ("pdf", "svg", "png"):
                    path = self.destination/"figures"/f"{key}_{label}.{ext}"
                    fig.savefig(path, dpi=600)
                    metadata["exports"].append(str(path))
                plt.close(fig)
        elif not complete:
            # Remove obsolete derived exports if inputs have been explicitly
            # replaced; never leave a stale numeric chart beside INCOMPLETE.
            for label in ("single", "double"):
                for ext in ("pdf", "svg", "png"):
                    old = self.destination/"figures"/f"{key}_{label}.{ext}"
                    if old.exists():
                        old.unlink()
        write_json(self.destination/"figures"/(key+".json"), metadata)
        self.figures[key] = metadata

    def overview(self):
        edges = [
            {"from": "planner", "to": "retriever", "path": "logical_role_graph", "meaning": "plan"},
            {"from": "retriever", "to": "executor", "path": "logical_role_graph", "meaning": "evidence"},
            {"from": "executor", "to": "reviewer", "path": "logical_role_graph", "meaning": "model_decision"},
            {"from": "runtime", "to": "UDS_router", "path": "control_plane", "meaning": "framed_message_and_ACK"},
            {"from": "planner", "to": "state_exchange", "path": "nontext_state", "meaning": "produce_reference"},
            {"from": "state_exchange", "to": "retriever", "path": "nontext_state", "meaning": "read_decode_rank_operands"},
            {"from": "retriever", "to": "memory", "path": "memory", "meaning": "candidate_retrieval"},
            {"from": "executor", "to": "memory", "path": "memory", "meaning": "actual_adoption"},
            {"from": "reviewer", "to": "memory", "path": "memory", "meaning": "feedback_and_write"},
        ]
        def draw(width, height):
            fig, ax = plt.subplots(figsize=(width, height))
            ax.set(xlim=(0, 10), ylim=(0, 7)); ax.axis("off")
            def box(x, y, w, h, text, color):
                ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=.08", facecolor="white", edgecolor=color, linewidth=1))
                ax.text(x+w/2, y+h/2, text, ha="center", va="center", fontsize=6.5 if width<4 else 8, color=color)
            for index, role in enumerate(("Planner", "Retriever", "Executor", "Reviewer")):
                box(.25+index*2.5, 5.55, 2., .8, role, "#183153")
                if index < 3:
                    ax.annotate("", (.25+(index+1)*2.5, 5.95), (2.25+index*2.5, 5.95), arrowprops={"arrowstyle": "->", "color": "#183153"})
            ax.text(5, 6.75, "Same four-role task graph", ha="center", fontsize=8)
            box(.5, 3.75, 9., .9, "Runtime owns mutable services and measured boundaries", "#6B7280")
            for x, text, color in ((.4, "UDS router\nframe + payload + ACK", "#087F8C"),
                (3.65, "State + artifacts\nproduce / resolve / consume", "#6356A5"),
                (6.9, "Shared memory\nretrieve / adopt / feedback", "#4B5563")):
                if width < 4:
                    text = text.replace("frame + payload + ACK", "frame + payload\n+ ACK").replace(
                        "produce / resolve / consume", "produce / resolve\n/ consume").replace(
                        "retrieve / adopt / feedback", "retrieve / adopt\n/ feedback")
                box(x, 1.8, 2.7, 1.05, text, color)
                ax.annotate("", (x+1.35, 2.92), (x+1.35, 3.65), arrowprops={"arrowstyle": "<->", "color": color})
            for x in (1.25, 3.75, 6.25, 8.75):
                ax.annotate("", (x, 4.75), (x, 5.45), arrowprops={"arrowstyle": "<->", "color": "#6B7280"})
            ax.text(5, .85, "Optional AgentHost: four worker processes; services via runtime RPC.\nState is decoded and affects Retriever operand ranking.", ha="center", va="center", fontsize=6.1 if width<4 else 7)
            return fig
        self.figure(1, edges, True,
            "Method overview from frozen protocol/native pipeline. No observational sample size. Runtime owns services; optional AgentHost workers obtain state values via RPC, rather than a claimed direct worker SHM mapping. Provider HTTP is separate from control IPC.",
            [self.out/"manifests/original_protocol.md", self.out/"manifests/native_configurations.json"], draw, 3.6)

    def statistics(self):
        reports = {}
        for phase in self.phases:
            plan, rows, complete = self.phase(phase)
            # Do not turn missing formal runs into apparent all-failure observations.
            if not complete:
                reports[phase] = {"status": "INCOMPLETE", "planned_units": len(plan), "observed_units": len(rows),
                                  "effect_numbers": "WITHHELD_UNTIL_ALL_PLANNED_TERMINAL_UNITS_EXIST"}
            elif phase.startswith("external_"):
                reports[phase] = {"status": "COMPLETE", "planned_units": len(plan), "observed_units": len(rows),
                                  "scope": "SYSTEM_LEVEL_REFERENCE_NO_SINGLE_MECHANISM_ATTRIBUTION"}
            else:
                result = protocol_analyze(rows, plan, self.applicable_task_ids) if phase=="main" else frozen_analyze(rows, plan)
                result["phase"] = phase
                result["inference_scope"] = "CONFIRMATORY_UNIQUE_TEN_TEST_FAMILY_STATE108_OTHER_PAIRS120" if phase == "main" else "EXPLORATORY_SEPARATE_PHASE_NOT_MAIN_CONFIRMATORY_FAMILY"
                result["quality_noninferiority_wording"] = "WITHHELD_BY_PROSPECTIVE_GATE"
                if phase=="main":
                    archive=result.get("all120_frozen_replay_archive")
                    if archive is None:
                        raise ValueError("protocol-aligned main statistics must retain the frozen all120 replay archive")
                    write_json(self.destination/"analysis/archive/statistics_main_frozen_all120_archive_only.json",archive)
                    result["reporting_policy"]={
                        "unique_primary_family":"S0↔SS0 uses predeclared 108 state-applicable tasks for two endpoints; other four pairs use all120 for eight endpoints; one combined Holm family of ten",
                        "archived_all120_p_holm":"ARCHIVE_ONLY_NEVER_SELECTED_OR_DISPLAYED_FOR_CONFIRMATORY_CLAIMS",
                        "state108_second_family":"PROHIBITED",
                        "correction_basis":"Original protocol section4.1 excludes NOT_APPLICABLE cases from state increment; read-only design audit found an implementation omission before inspecting formal effects; this restores the original design and is not an added research condition or another human-review exception."}
                reports[phase] = result
            write_json(self.destination/"analysis"/f"statistics_{phase}.json", reports[phase])
        return reports

    def main_tables(self):
        env_rows = []
        for filename in ("environment.json", "source_identity.json", "models.json", "budget_limits.json", "formal-freeze.json"):
            source = self.out/"manifests"/filename
            record = json_read(source)
            self.source(source)
            if record is None:
                env_rows.append({"section": filename, "field": "availability", "value": None, "status": "INCOMPLETE", "source": str(source)})
            else:
                for key, value in record.items():
                    if any(word in key.lower() for word in ("api_key", "authorization", "secret")):
                        continue
                    env_rows.append({"section": filename, "field": key, "value": value, "status": "RECORDED", "source": str(source)})
        formal = [r for r in self.plan if r["phase"] == "main"]
        env_rows.extend({"section": "formal_sample", "field": key, "value": value, "status": "PLANNED", "source": "formal_plan.json"}
            for key, value in {"task_units": len(formal), "unique_tasks": len({r['task_id'] for r in formal}),
                "chains": len({r['chain_id'] for r in formal}), "repeats": len({r['repeat'] for r in formal})}.items())
        self.table(1, env_rows, all(r["status"] != "INCOMPLETE" for r in env_rows),
                   "Environment and frozen protocol metadata; WSL kernel/userspace identity remains explicit. Planned sample size is not completed evidence.",
                   [self.out/"manifests/environment.json", self.out/"manifests/formal_plan.json"])
        definitions = []
        facts = json_read(self.out/"analysis/fact_equivalence_calibration.json", {})
        for arm in ARMS:
            definitions.append({"arm": arm, "communication": "lean full-payload text" if arm.startswith("T") else "native structured",
                "state": arm.startswith("SS"), "memory": arm.endswith("1"),
                "same_task_roles_model_verifier": True,
                "facts_fixture_audit": facts.get("status", "INCOMPLETE"),
                "fixture_units": facts.get("runtime_units"), "audit_scope": "offline public-task fact coverage, not formal model quality",
                "real_phase_completion": "COMPLETE" if self.phase("main")[2] else "INCOMPLETE"})
        self.table(2, definitions, facts.get("status") == "PASS",
                   "Frozen six-arm definitions. Representation/handoff differ; business facts and acceptance contract are shared. State-disabled cases remain NOT_APPLICABLE in state-effect analyses.",
                   [self.out/"manifests/native_configurations.json", self.out/"analysis/fact_equivalence_calibration.json"])
        main_plan, main_rows, complete = self.phase("main")
        summary = []
        for arm in ARMS:
            planned = [r for r in main_plan if r["arm"] == arm]
            observed = [r for r in main_rows if r["arm"] == arm]
            base = {"arm": arm, "status": "COMPLETE" if complete else "INCOMPLETE", "planned_units": len(planned),
                    "observed_terminal_units": len(observed), "unique_tasks": len({r['task_id'] for r in planned}),
                    "chains": len({r['chain_id'] for r in planned}), "scope": "formal main real provider only",
                    "cache_token_authority":"provider_calls.usage_raw explicit cache fields only; missing details UNKNOWN; task-run default0 is not authoritative",
                    "provider_wait_definition":"client-observed SDK call elapsed including request/response handling and instrumentation; retry backoff separate; neither provider queue time nor pure network RTT",
                    "provider_queue_latency_sec":"UNKNOWN_PROVIDER_DOES_NOT_EXPOSE_SEPARATE_QUEUE_LATENCY",
                    "pure_network_RTT_sec":"UNKNOWN_NOT_SEPARABLE_FROM_CLIENT_SDK_WAIT"}
            mean, lo, hi = quality_interval(observed) if complete else (None, None, None)
            base.update(success_rate=mean, success_ci95_low=lo, success_ci95_high=hi,
                        quality_ci_method="conservative chain-all-success/any-success exact event bound",
                        noninferiority_claim="WITHHELD_BY_PROSPECTIVE_GATE")
            for metric in METRICS:
                mean, lo, hi = cluster_mean(observed, metric) if complete else (None, None, None)
                base.update({metric+"_mean": mean, metric+"_ci95_low": lo, metric+"_ci95_high": hi})
            for percentile in (50, 95, 99):
                base["wall_p"+str(percentile)+"_sec"] = float(np.percentile([r["wall_sec"] for r in observed], percentile)) if complete and all(finite(r.get("wall_sec")) for r in observed) else None
            summary.append(base)
        self.table(3, summary, complete,
                   "Formal main, all planned terminal successes/failures retained. Repeats averaged within task for means; domain-stratified chain bootstrap 10,000 draws for cost CIs. Missing usage/cost remains blank. Cache counts require explicit provider_calls.usage_raw cache fields; task-run cached-token default0 is not a provider report and missing details are UNKNOWN. Provider wait is client-observed SDK call elapsed, not provider queue latency or pure RTT, which are separately UNKNOWN. Quality interval is conservative at all-pass; no noninferiority wording.",
                   [self.out/"raw/task_runs.jsonl", self.out/"manifests/formal_plan.json"])
        return summary

    def main_figures(self, stats, summary):
        plan, rows, complete = self.phase("main")
        figrows = []
        if complete:
            for stratum in stats.get("main", {}).get("strata", []):
                for pair in stratum["comparisons"]:
                    for metric in ("provider_input_tokens", "observable_ipc_bytes", "local_state_materialized_bytes"):
                        item = pair["metrics"].get(metric, {})
                        if item.get("status") != "COMPLETE":
                            continue
                        base = {"metric": metric, "comparison": pair["baseline"]+" to "+pair["target"],
                                "baseline": pair["baseline"], "target": pair["target"], "value": item["paired_difference"],
                                "ci95_low": item["difference_ci95"][0], "ci95_high": item["difference_ci95"][1],
                                "relative_change": item["relative_change"], "p_holm": item.get("p_holm"),
                                "n_unique_tasks": item["unique_tasks"], "n_chains": item["chain_count"],
                                "planned_pairs": pair["planned_paired_units"], "scope": "formal main paired task costs"}
                        figrows.append({**base, "kind": "effect"})
                        grouped = defaultdict(list)
                        for task in item["task_pair_distribution"]:
                            grouped[task["chain_id"]].append(task["difference"])
                        figrows.extend({**base, "kind": "chain_point", "chain_id": chain,
                                        "value": float(np.mean(values)), "ci95_low": None, "ci95_high": None}
                                       for chain, values in grouped.items())
        def draw2(width, height):
            fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 5.2)))
            for ax, metric, label in zip(axes, ("provider_input_tokens", "observable_ipc_bytes", "local_state_materialized_bytes"),
                ("Paired input-token change", "Paired observable IPC change (KiB)", "Paired local state-materialization change (KiB)")):
                factor = 1 if metric == "provider_input_tokens" else 1024
                for index, (a, b) in enumerate(PAIRS):
                    selected = [r for r in figrows if r["metric"] == metric and r["baseline"] == a and r["target"] == b]
                    point = next((r for r in selected if r["kind"] == "effect"), None)
                    if point:
                        raw = [r["value"]/factor for r in selected if r["kind"] == "chain_point"]
                        ax.scatter(raw, [index]*len(raw), color=COLORS[b], s=8, alpha=.25)
                        # Draw literal bounds: floating subtraction can put the
                        # estimate just outside a degenerate bootstrap interval.
                        lower, upper = point["ci95_low"]/factor, point["ci95_high"]/factor
                        ax.hlines(index, lower, upper, color=COLORS[b])
                        ax.plot([lower, upper], [index, index], linestyle="none", marker="|", color=COLORS[b], markersize=4)
                        ax.plot(point["value"]/factor, index, linestyle="none", marker="o" if b.endswith("0") else "s", color=COLORS[b],
                            markerfacecolor="white" if b.endswith("0") else COLORS[b], markersize=4)
                    else:
                        ax.text(.98, index, "INCOMPLETE", transform=ax.get_yaxis_transform(), ha="right", fontsize=6, color="#6B7280")
                ax.axvline(0, color="#6B7280", linewidth=.7); ax.set_yticks(range(5), [a+"→"+b for a,b in PAIRS]); ax.invert_yaxis(); ax.set_xlabel(label); ax.grid(axis="x", alpha=.2)
            return fig
        self.figure(2, figrows, complete and bool(figrows),
            "Formal main only; five original-protocol arm pairs. S0→SS0 uses the predeclared 108 state-applicable tasks; the other four pairs use120. The two primary endpoints form one combined ten-test Holm family, never a second108-task family. Faint points are chain-mean paired differences; 95% intervals use domain-stratified chain bootstrap (10,000). CSV records actual task/chain/pair denominators. IPC includes declared runtime AF_UNIX framing/ACK/RPC once; local state bytes are separate. Provider input tokens are reported usage; unknown usage remains INCOMPLETE.",
            [self.out/"raw/task_runs.jsonl", self.destination/"analysis/statistics_main.json"], draw2, 5.3)
        quality = []
        if complete:
            for domain in sorted({r["domain"] for r in plan}):
                for arm in ARMS:
                    current = [r for r in rows if r["domain"] == domain and r["arm"] == arm]
                    point, low, high = quality_interval(current)
                    quality.append({"kind": "success_rate", "domain": domain, "arm": arm, "value": point,
                        "ci95_low": low, "ci95_high": high, "planned_units": len(current),
                        "unique_tasks": len({r['task_id'] for r in current}), "chains": len({r['chain_id'] for r in current})})
                    errors = Counter(error_type(r) for r in current if not r["success"])
                    quality.extend({"kind": "failure_type", "domain": domain, "arm": arm, "failure_type": k, "count": v} for k,v in errors.items())
        def draw3(width, height):
            fig = plt.figure(figsize=(width, max(height, 6.0)))
            gs = fig.add_gridspec(3, 2)
            domains = sorted({r["domain"] for r in quality})
            for idx, domain in enumerate(domains):
                ax = fig.add_subplot(gs[idx//2, idx%2])
                for index, arm in enumerate(ARMS):
                    r = next(v for v in quality if v["kind"] == "success_rate" and v["domain"] == domain and v["arm"] == arm)
                    ax.errorbar(index, r["value"], yerr=[[r["value"]-r["ci95_low"]], [r["ci95_high"]-r["value"]]], fmt="s" if arm.endswith("1") else "o", markerfacecolor=COLORS[arm] if arm.endswith("1") else "white", color=COLORS[arm], capsize=2, markersize=4)
                ax.set(title=domain, ylim=(-.03, 1.03), xticks=range(6), xticklabels=ARMS, ylabel="Task success rate"); ax.tick_params(axis="x", labelsize=6); ax.grid(axis="y", alpha=.2)
            ax = fig.add_subplot(gs[2, :]); categories = sorted({r["failure_type"] for r in quality if r["kind"] == "failure_type"})
            left = np.zeros(6)
            for index, category in enumerate(categories):
                values = [sum(r["count"] for r in quality if r["kind"] == "failure_type" and r["arm"] == arm and r["failure_type"] == category) for arm in ARMS]
                ax.barh(range(6), values, left=left, label=category, color=RISK if index==0 else GREYS[index%5]); left += values
            ax.set(yticks=range(6), yticklabels=ARMS, xlabel="Failed planned task units")
            if categories: ax.legend(loc="upper left", bbox_to_anchor=(0, -.2), ncol=2)
            else: ax.text(.5, .5, "No observed failures", transform=ax.transAxes, ha="center")
            return fig
        self.figure(3, quality, complete,
            "Formal main only, all planned units including failures. Each domain has its unique tasks and chains reported in CSV; repeats are not additional independent cases. 95% quality intervals are conservative exact bounds on chain-all-success/chain-any-success events, hence remain nondegenerate at all-pass. Failure types come from retained terminal records; no noninferiority claim.",
            [self.out/"raw/task_runs.jsonl"], draw3, 6.1)
        timing = []
        if complete:
            for arm in ARMS:
                current = [r for r in rows if r["arm"] == arm]
                for category in sorted({k for r in current for k in r.get("exclusive_spans_sec", {})}):
                    timing.append({"kind": "exclusive_mean", "arm": arm, "category": category,
                        "value_sec": float(np.mean([r.get("exclusive_spans_sec", {}).get(category, 0.) for r in current])),
                        "n_task_units": len(current)})
                for quantile in (50, 95, 99):
                    timing.append({"kind": "wall_percentile", "arm": arm, "percentile": quantile,
                                   "value_sec": float(np.percentile([r["wall_sec"] for r in current], quantile)),
                                   "n_task_units": len(current)})
        def draw4(width, height):
            fig, axes = plt.subplots(2, 1, figsize=(width, max(height, 4.8)))
            categories = sorted({r["category"] for r in timing if r["kind"] == "exclusive_mean"})
            left = np.zeros(6)
            for index, category in enumerate(categories):
                values = [next((r["value_sec"] for r in timing if r["kind"] == "exclusive_mean" and r["arm"] == arm and r["category"] == category), 0) for arm in ARMS]
                label="client SDK wait" if category=="provider_wait" else category
                axes[0].barh(range(6), values, left=left, label=label, color=GREYS[index%5], hatch=("", "//", "..", "xx")[index%4]); left += values
            axes[0].set(yticks=range(6), yticklabels=ARMS, xlabel="Mean wall time: exclusive partition (s)")
            axes[0].legend(ncol=3, loc="upper left", bbox_to_anchor=(0, -.18), fontsize=5 if width<4 else 6)
            for index, percentile in enumerate((50, 95, 99)):
                values = [next(r["value_sec"] for r in timing if r["kind"] == "wall_percentile" and r["arm"] == arm and r["percentile"] == percentile) for arm in ARMS]
                axes[1].plot(range(6), values, marker=("o", "s", "^")[index], color=GREYS[index], label=f"p{percentile}")
            axes[1].set(xticks=range(6), xticklabels=ARMS, ylabel="Task wall time (s)"); axes[1].legend(); axes[1].grid(alpha=.2)
            return fig
        self.figure(4, timing, complete,
            "Formal main task-unit wall time in the recorded openEuler/WSL VM. Stacked categories partition parent runtime wall time without summing nested child/service spans twice. Client SDK wait includes observed request/response processing and instrumentation; retry backoff is separate. Provider queue latency is UNKNOWN and pure network RTT is UNKNOWN: neither is inferred from the SDK wait. Quantiles describe task executions; paired chain-level latency effects and CIs are in statistics_main.json. CSV records n; imports/setup/cleanup and recorder scope follow frozen timing definition.",
            [self.out/"raw/task_runs.jsonl", self.out/"raw/spans.jsonl"], draw4, 5.0)

    def offline_figures(self):
        state_source = self.out/"tables/state_paths.csv"
        states = csv_read(state_source)
        state_ok = len(states) == 42 and all(int(r["n"]) == 600 for r in states)
        def draw5(width, height):
            fig, axes = plt.subplots(2, 1, figsize=(width, max(height, 4.8)), sharex=True)
            for ax, condition in zip(axes, ("cold", "hot")):
                for index, path in enumerate(("copy", "shm", "memfd")):
                    values = sorted([r for r in states if r["condition"] == condition and r["path"] == path], key=lambda r: int(r["logical_bytes"]))
                    x = [int(r["logical_bytes"])/1024 for r in values]
                    ax.plot(x, [float(r["p50_ms"]) for r in values], marker=("o", "s", "^")[index], color=GREYS[index], label=path)
                    ax.plot(x, [float(r["p95_ms"]) for r in values], linestyle=":", color=GREYS[index])
                ax.set(xscale="log", ylabel="Task wall time (ms)", title="First materialization" if condition == "cold" else "Reused materialization")
                ax.grid(alpha=.2)
                handles, labels = ax.get_legend_handles_labels()
                handles += [Line2D([0], [0], color="#6B7280", linestyle="-"), Line2D([0], [0], color="#6B7280", linestyle=":")]
                ax.legend(handles, labels+["p50", "p95"], ncol=3)
            axes[-1].set_xlabel("Logical state size (KiB)")
            return fig
        self.figure(5, states, state_ok,
            "Offline actual AF_UNIX transport, not model latency. Each path×size×cold/hot cell has n=600 measured transfers across 3 independent receiver batches, 20 warmups per cell/batch. Solid p50, dotted p95; no CI is inferred from pooled quantiles. Socket bytes include declared stream framing/ACK; memfd ancillary kernel overhead is excluded. Logical size differs from actual encoded/materialized bytes.",
            [state_source, self.out/"analysis/state_matrix_status.json"], draw5, 4.8)
        quant_source = self.out/"tables/quantization.csv"
        quant = csv_read(quant_source)
        quant_ok = len(quant) == 64 and all(int(r["unique_states"]) == 200 for r in quant)
        def draw6(width, height):
            fig, axes = plt.subplots(2, 2, figsize=(width, max(height, 5.2)), sharey=True)
            for ax, distribution in zip(axes.flat, ("gaussian", "sparse", "heavy_tail", "near_tie")):
                for index, precision in enumerate(("float32", "float16", "int8", "adaptive")):
                    values = [r for r in quant if r["distribution"] == distribution and r["precision"] == precision]
                    rate = [float(r["decision_rate"]) for r in values]
                    ax.errorbar([float(r["encoded_embedding_scale_bytes_mean"]) for r in values], rate,
                        yerr=[[max(0, y-float(r["wilson95_low"])) for y,r in zip(rate, values)],
                              [max(0, float(r["wilson95_high"])-y) for y,r in zip(rate, values)]],
                        fmt=("o", "s", "^", "D")[index], color=RISK if precision=="int8" else GREYS[index], markersize=3.5, capsize=2, label=precision)
                ax.set(xscale="log", ylim=(-.02, 1.02), title=distribution.replace("_", " "),
                    xlabel="Encoded payload\n(bytes)" if width<4 else "Encoded payload (bytes)", ylabel="Full ranking preserved")
                ax.grid(alpha=.2); ax.legend(fontsize=5 if width<4 else 6)
            return fig
        self.figure(6, quant, quant_ok,
            "Offline native state encoding and actual receiver ranking, not model decisions. n=200 unique states per dimension×distribution×requested precision (3,200 unique source vectors, 12,800 precision observations). Error bars are Wilson 95% CIs on full-ranking preservation. X includes encoded embedding and scale only; excludes verification helpers/socket framing. Shapes identify precision; orange marks a lossy precision condition rather than a communication mode.",
            [quant_source, self.out/"analysis/quantization_status.json"], draw6, 5.3)
        retrieval_source = self.retrieval_out/"tables/retrieval_quality_scale.csv"
        retrieval = csv_read(retrieval_source)
        retrieval_audit = json_read(self.retrieval_out/"analysis/retrieval_quality_scale_audit.json", {})
        retrieval_ok = retrieval_audit.get("complete", False) and len(retrieval) == 20
        def draw7(width, height):
            fig, axes = plt.subplots(2, 2, figsize=(width, max(height, 5.3)))
            methods = sorted({r["mode"] for r in retrieval})
            for ax, metric, title in zip(axes.flat, ("recall_at_10", "mrr", "false_retrieval", "latency_p95_ms"),
                ("Answerable queries: Recall@10", "Answerable queries: MRR@10", "No-answer queries: false retrieval", "Query latency p95 (ms)")):
                if width < 4:
                    title = title.replace(": ", ":\n").replace("Query latency p95 (ms)", "Query latency\np95 (ms)")
                for index, method in enumerate(methods):
                    values = sorted([r for r in retrieval if r["mode"] == method], key=lambda r:int(r["pool_size"]))
                    yy = [float(r[metric]) for r in values]
                    lo_key, hi_key = ("latency_p95_ci95_low_ms", "latency_p95_ci95_high_ms") if metric=="latency_p95_ms" else (metric+"_ci95_low", metric+"_ci95_high")
                    lo, hi = [float(r[lo_key]) for r in values], [float(r[hi_key]) for r in values]
                    ax.errorbar([int(r["pool_size"]) for r in values], yy,
                        yerr=[[max(0,a-b) for a,b in zip(yy,lo)], [max(0,b-a) for a,b in zip(yy,hi)]],
                        color=GREYS[index%5], marker=("o", "s", "^", "D", "v")[index%5],
                        linestyle=("-", "--", ":", "-.", "-")[index%5], markersize=3, capsize=2, label=method)
                ax.set(xscale="log", title=title, xlabel="Candidate pool size"); ax.grid(alpha=.2)
                if metric != "latency_p95_ms": ax.set_ylim(-.02,1.02)
            handles, labels = axes[0,0].get_legend_handles_labels()
            fig.legend(handles, labels, loc="outside lower center", ncol=2 if width<4 else 5, fontsize=5.5)
            return fig
        self.figure(7, retrieval, retrieval_ok,
            "Offline native MemoryStore/DocSem-256 retrieval with user-authorized automatic causal labels; no provider calls or model-quality claim. 240 distinct queries from 120 tasks/20 chains, 3 independent pool builds, 4 sizes, 5 methods (14,400 query executions). Answerable metrics use 160 queries/build; no-answer rates use 80/build. MRR is truncated at 10. CIs and latency bounds come from the retrieval analyzer's declared chain/build resampling. Main real-model memory adoption, paired benefit and negative transfer remain separate table 5 outcomes."
            + (" Selected input-correction v2 was registered after the original run and before new outcomes: 30 duplicate texts receive existing public task observations; the original 14,400 executions are retained as input-defect archive_only and never pooled." if self.retrieval_out != self.out.resolve() else ""),
            [retrieval_source, self.retrieval_out/"analysis/retrieval_quality_scale_audit.json"], draw7, 5.4)
        stress_source = self.out/"tables/stress_chain_concurrency.csv"
        stress = csv_read(stress_source)
        stress_audit = json_read(self.out/"analysis/stress_matrix_audit.json", {})
        stress_ok = (stress_audit.get("pressure_cells_complete") == 30 and len(stress) == 30 and
                     all(int(r["recorded_batches"]) == 30 for r in stress))
        def draw8(width, height):
            fig, axes = plt.subplots(3, 1, figsize=(width, max(height, 6.3)))
            for index, concurrency in enumerate((1,2,4,8,16)):
                values = sorted([r for r in stress if int(r["concurrency"]) == concurrency], key=lambda r:int(r["length"]))
                x = [int(r["length"]) for r in values]
                mean = [float(r["throughput_mean_tasks_per_sec"]) for r in values]
                axes[0].errorbar(x, mean, yerr=[[max(0,y-float(r["throughput_ci95_low"])) for y,r in zip(mean,values)],
                    [max(0,float(r["throughput_ci95_high"])-y) for y,r in zip(mean,values)]],
                    marker=("o","s","^","D","v")[index], color=GREYS[index], capsize=2, label=f"concurrency {concurrency}")
                axes[1].plot(x, [float(r["task_latency_p95_sec"]) for r in values], marker=("o","s","^","D","v")[index], color=GREYS[index])
                axes[2].plot(x, [float(r["native_observable_frame_ack_bytes_per_chain"])/1024 for r in values], marker=("o","s","^","D","v")[index], color=GREYS[index])
            for ax in axes: ax.set_xscale("log", base=2); ax.grid(alpha=.2); ax.set_xlabel("Causal chain length")
            axes[0].set_ylabel("Throughput (tasks/s)"); axes[0].legend(ncol=2)
            axes[1].set_ylabel("Task latency p95 (s)")
            axes[2].set_ylabel("Native frame + ACK (KiB/chain)")
            return fig
        self.figure(8, stress, stress_ok,
            "Offline actual native SS1 four-role pipeline, no provider tokens/model-quality claim. Frozen 6 chain lengths×5 concurrency levels×30 independent batches; throughput CIs use 10,000 batch resamples, tail latency is pooled observed task executions. Frame/ACK communication counts exclude native handshake counters and are not equated to the wider real-driver IPC boundary. Every cell/failed task is retained; incomplete scale cells prevent this full figure being exported.",
            [stress_source, self.out/"analysis/stress_matrix_audit.json"], draw8, 6.3)
        table4 = [{"section": "state_paths", **r} for r in states] + [{"section": "quantization", **r} for r in quant]
        # These receiver times are nested inside transport RTT; retain them as
        # separate diagnostics, never add them to end-to-end latency.
        state_raw=[]
        for batch in (1,2,3):
            path=self.out/"raw"/f"state_paths_batch_{batch}.jsonl"
            state_raw.extend(jsonl(path));self.source(path)
        grouped=defaultdict(list)
        for row in state_raw:grouped[(row["path"],row["condition"],row["logical_bytes"])].append(row)
        for (path,condition,size),values in sorted(grouped.items()):
            table4.append({"section":"state_resource_and_nested_decode","path":path,"condition":condition,"logical_bytes":size,
                "n":len(values),"receiver_read_verify_ms_mean":float(np.mean([r['receiver_read_verify_ns']/1e6 for r in values])),
                "producer_cpu_ms_mean":float(np.mean([r['producer_cpu_ns']/1e6 for r in values])),
                "receiver_cpu_ms_mean":float(np.mean([r['receiver_cpu_ns']/1e6 for r in values])),
                "producer_peak_rss_bytes":max(r['producer_peak_rss_bytes'] for r in values),
                "receiver_peak_rss_bytes":max(r['consumer_peak_rss_bytes'] for r in values),
                "producer_max_fd":max(r['producer_fd_count'] for r in values),
                "receiver_max_fd":max(r['consumer_fd_count'] for r in values),
                "scope":"receiver time nested in RTT; process RSS counts shared pages per process"})
        quant_raw=self.out/"raw/quantization.jsonl"
        self.source(quant_raw)
        grouped=defaultdict(list)
        for row in jsonl(quant_raw):grouped[(row['dimension'],row['distribution'],row['requested_precision'])].append(row)
        for (dim,dist,precision),values in sorted(grouped.items()):
            table4.append({"section":"quantization_time","dimension":dim,"distribution":dist,"precision":precision,"unique_states":len(values),
                "encode_materialize_ms_mean":float(np.mean([r['producer_encode_materialize_ns']/1e6 for r in values])),
                "producer_encode_cpu_ms_mean":float(np.mean([r['producer_encode_cpu_ns']/1e6 for r in values])),
                "receiver_decode_rank_ms_mean":float(np.mean([r['receiver_ns']/1e6 for r in values])),
                "receiver_cpu_ms_mean":float(np.mean([r['receiver_cpu_ns']/1e6 for r in values])),
                "scope":"actual encoded-state receiver decode plus 8-candidate ranking; synthetic mechanism only"})
        self.table(4, table4, state_ok and quant_ok,
            "Offline transport/precision measurements, actual logical/materialized/socket byte boundaries retained. Mechanism-off real-model phase comparisons are separately listed in analysis/matched_phase_effects.json; incomplete phases are not filled with these synthetic observations.",
            [state_source, quant_source])
        return retrieval, stress

    def memory_and_references(self, stats, retrieval):
        plan, rows, complete = self.phase("main")
        main_ids = {r["run_id"] for r in plan}
        memory_events = [r for r in jsonl(self.out/"raw/memory_events.jsonl") if r.get("run_id") in main_ids]
        table5 = [{"section": "offline_retrieval", "status": "COMPLETE" if len(retrieval)==20 else "INCOMPLETE", **r} for r in retrieval]
        for arm in ("T1", "S1", "SS1"):
            current = [r for r in rows if r["arm"] == arm]
            queries = [r for r in memory_events if r.get("arm") == arm and r.get("event") == "retrieve"]
            hits = sum(any(c.get("related_under_automatic_causal_rule") for c in r.get("candidates", [])) for r in queries)
            eligible = [r for r in current if r.get("memory_count_before",0)>0]
            adopted = [r for r in current if r.get("memory_adopted_id")]
            table5.append({"section": "formal_main_memory", "arm": arm, "status": "COMPLETE" if complete else "INCOMPLETE",
                "planned_task_units": sum(r["arm"] == arm for r in plan), "observed_task_units": len(current),
                "candidate_queries": len(queries) if complete else None,
                "automatic_related_candidate_queries": hits if complete else None,
                "candidate_related_rate_query_denominator": hits/len(queries) if complete and queries else None,
                "eligible_nonempty_store_units": len(eligible) if complete else None,
                "actually_adopted_units": len(adopted) if complete else None,
                "adoption_rate_nonempty_store_denominator": len(adopted)/len(eligible) if complete and eligible else None,
                "success_after_adoption": sum(r["success"] for r in adopted)/len(adopted) if complete and adopted else None,
                "scope": "candidate/adoption/feedback separated; post-adoption success is not causal benefit; automatic label scope restricted to recorded parent/chain rule"})
        for stratum in stats.get("main",{}).get("strata",[]):
            for pair in stratum["comparisons"]:
                if (pair["baseline"],pair["target"]) not in (("T0","T1"),("S0","S1"),("SS0","SS1")):
                    continue
                table5.append({"section":"formal_paired_memory_effect", "baseline":pair["baseline"], "target":pair["target"],
                    "quality_difference":pair["quality"]["paired_difference"],
                    "quality_conservative_ci95":pair["quality"]["quality_conservative_ci95"],
                    "quality_one_sided_lower95":pair["quality"]["quality_difference_one_sided_lower95"],
                    "input_token_effect":pair["metrics"].get("provider_input_tokens"),
                    "IPC_effect":pair["metrics"].get("observable_ipc_bytes"),
                    "negative_transfer_definition":"paired baseline success and memory-arm failure; conservative chain-any-loss details in quality domains",
                    "status":pair["status"]})
        self.table(5, table5, complete and len(retrieval)==20,
            "Offline query quality and formal real-provider memory effects are separate sections. Query relevance uses the recorded automatic rule; nonempty-store eligibility is observed before the task. Candidate hits, actual adoption and feedback are distinct. Paired all-planned memory-on/off effects support benefit claims; success after adoption does not establish causality.",
            [self.retrieval_out/"tables/retrieval_quality_scale.csv", self.out/"raw/memory_events.jsonl", self.out/"raw/task_runs.jsonl"])
        faults_source = self.out/"tables/stress_fault_recovery.csv"
        faults = csv_read(faults_source)
        table6 = [{"section":"offline_fault_recovery", "status":"COMPLETE" if int(r["recorded_trials"])==30 else "INCOMPLETE", **r} for r in faults]
        semantic_path=self.out/"analysis/stress_fault_semantic_audit.json"
        semantic=json_read(semantic_path,{})
        for name in ("corruption","wrong_memory"):
            if name in semantic:
                table6.append({"section":"offline_fault_semantic_limit","fault":name,"status":"RECORDED_LIMITATION",
                               "evidence":semantic[name],"scope":semantic.get("evidence_class")})
        self.source(semantic_path)
        reference_rows = []
        external_complete = True
        for framework in ("autogen","langgraph"):
            phase = "external_"+framework
            planned, observed, done = self.phase(phase)
            external_complete &= done
            for metric in ("success", "provider_input_tokens", "wall_sec"):
                if done:
                    estimate, low, high = quality_interval(observed) if metric=="success" else cluster_mean(observed,metric)
                else:
                    estimate=low=high=None
                reference_rows.append({"system":framework,"metric":metric,"value":estimate,"ci95_low":low,"ci95_high":high,
                    "status":"COMPLETE" if done else "INCOMPLETE", "planned_units":len(planned),"observed_units":len(observed),
                    "unique_tasks":len({r['task_id'] for r in planned}),"chains":len({r['chain_id'] for r in planned}),
                    "scope":"SYSTEM_LEVEL_REFERENCE_FIXED_EXTERNAL_PROMPT_INPROCESS_NO_STATE_NO_MEMORY"})
            table6.append({"section":"real_external_reference","system":framework,"status":"COMPLETE" if done else "INCOMPLETE",
                "planned_units":len(planned),"observed_units":len(observed),"successful_units":sum(r["success"] for r in observed) if done else None,
                "observable_ipc_bytes_boundary":"0 within in-process role/control boundary; HTTP excluded",
                "scope":"SYSTEM_LEVEL_REFERENCE_NOT_SINGLE_MECHANISM_EFFECT",
                "equivalence_audit":str(self.out/"analysis/external_framework_equivalence_v1.json")})
        native_plan, native_rows, native_done = self.phase("main")
        external_plan = [r for r in self.plan if r["phase"]=="external_autogen"]
        target_keys = {(r["task_id"],r["repeat"]) for r in external_plan}
        selected = [r for r in native_rows if r["arm"]=="T0" and (r["task_id"],r["repeat"]) in target_keys]
        matched_complete = len(selected)==len(target_keys) and bool(target_keys)
        if external_complete and native_done and matched_complete:
            for metric in ("success","provider_input_tokens","wall_sec"):
                estimate,low,high = quality_interval(selected) if metric=="success" else cluster_mean(selected,metric)
                reference_rows.append({"system":"native_T0","metric":metric,"value":estimate,"ci95_low":low,"ci95_high":high,
                    "status":"COMPLETE","planned_units":len(target_keys),"observed_units":len(selected),
                    "unique_tasks":len({r['task_id'] for r in selected}),"chains":len({r['chain_id'] for r in selected}),
                    "scope":"matched 60-task subset and same first two repeat IDs; native lean wire differs from fixed external template"})
        def draw9(width,height):
            fig,axes=plt.subplots(3,1,figsize=(width,max(height,5.1)))
            for ax,metric,label in zip(axes,("success","provider_input_tokens","wall_sec"),("Task success rate","Provider input tokens","Task wall time (s)")):
                for index,system in enumerate(("native_T0","autogen","langgraph")):
                    item=next(r for r in reference_rows if r["system"]==system and r["metric"]==metric)
                    if item["value"] is not None:
                        ax.errorbar(index,item["value"],yerr=[[max(0,item["value"]-item["ci95_low"])],[max(0,item["ci95_high"]-item["value"])]],
                            fmt=("o","s","^")[index],color=GREYS[index],capsize=3)
                    else:ax.text(index,.5,"UNKNOWN",ha="center",transform=ax.get_xaxis_transform(),fontsize=6)
                ax.set(xticks=range(3),xticklabels=("native T0","AutoGen","LangGraph"),ylabel=label);ax.grid(axis="y",alpha=.2)
                if metric=="success":ax.set_ylim(-.02,1.02)
            return fig
        equivalence=json_read(self.out/"analysis/external_framework_equivalence_v1.json",{})
        reference_ok=external_complete and native_done and matched_complete and equivalence.get("status")=="PASS"
        self.figure(9,reference_rows,reference_ok,
            "System-level reference only, fixed preselected 60 tasks×2 repeats per external framework and same native T0 task/repeat subset. Actual AutoGen/LangGraph scheduling has documented prompt/state semantics; effects cannot isolate one native mechanism. Cost CIs use domain-stratified chain bootstrap; quality uses conservative exact chain events. No public calibration is included; CSV identifies task/chain/unit denominators. Any missing real measurement is unknown, not zero.",
            [self.out/"raw/task_runs.jsonl",self.out/"analysis/external_framework_equivalence_v1.json"],draw9,5.1)
        fault_ok=len(faults)==6 and all(int(r["recorded_trials"])==30 for r in faults)
        self.table(6,table6,external_complete and fault_ok,
            "External real-model system references and offline actual fault injections are distinct sections. Fault rates use all 30 planned trials/fault. Explicit limitations include undetected nonfinite-state consumption/changed ranking and wrong procedural adoption under a deterministic fixture; correct fixture answers or effective feedback do not establish model robustness or memory causality. External topology/prompt differences and unsupported/unmeasured paths remain explicit.",
            [faults_source,semantic_path,self.out/"raw/task_runs.jsonl",self.out/"analysis/external_framework_equivalence_v1.json"])

    def spot_check(self):
        plan,rows,complete=self.phase("main")
        selection_path=self.out/'analysis/stats/automatic_spotcheck_plan.json'
        self.source(selection_path)
        selection=json_read(selection_path,{})
        if not isinstance(selection,dict):selection={}
        chosen=selection.get('units',[]) if isinstance(selection,dict) else []
        valid_units=isinstance(chosen,list) and all(isinstance(u,dict) for u in chosen)
        if not valid_units:chosen=[]
        report={"status":"INCOMPLETE","selection":"exact preselected30 from analysis/stats/automatic_spotcheck_plan.json; no replacement sample or outcome-based reselection",
                "selection_plan_path":str(selection_path),"selection_plan_sha256":digest(selection_path),
                "frozen_formal_plan_sha256":digest(self.out/'manifests/formal_plan.json'),
                "human_review":"NOT_PERFORMED_USER_AUTHORIZED_AUTOMATIC_VALIDATION","human_raters":0,
                "inter_rater_agreement":"NOT_APPLICABLE_NO_HUMAN_RATINGS",
                "selected_run_ids":[r.get('run_id') for r in chosen],
                "selected_unique_tasks":len({r.get('task_id') for r in chosen}),
                "real_model_scope":"the same30 preselected main task execution units; no pilot/offline substitution; direct raw re-audit does not depend on the earlier derived spotcheck report"}
        selection_errors=[]
        if not isinstance(selection,dict) or not valid_units or len(chosen)!=30 or len({r.get('run_id') for r in chosen})!=30 or selection.get('sampled_units')!=30:
            selection_errors.append('PRESELECTED_PLAN_MUST_CONTAIN_EXACTLY30_UNIQUE_UNITS')
        if selection.get('plan_sha256')!=report['frozen_formal_plan_sha256']:
            selection_errors.append('PRESELECTED_FORMAL_PLAN_HASH_MISMATCH')
        if report['selection_plan_sha256']!=REGISTERED_SPOTCHECK_PLAN_SHA256:
            selection_errors.append('PRESELECTED_SELECTION_METADATA_BYTES_CHANGED')
        planned_by_id={r['run_id']:r for r in plan}
        if any(u.get('run_id') not in planned_by_id or u!=planned_by_id.get(u.get('run_id')) for u in chosen):
            selection_errors.append('PRESELECTED_UNIT_NOT_IDENTICAL_TO_FROZEN_MAIN_PLAN')
        actual_arms=dict(Counter(u.get('arm') for u in chosen))
        expected_arms={arm:5 for arm in ARMS}
        domains=sorted({u['domain'] for u in plan})
        expected_domains={domain:8 if index<2 else 7 for index,domain in enumerate(domains)}
        actual_domains=dict(Counter(u.get('domain') for u in chosen))
        if actual_arms!=expected_arms or selection.get('coverage_by_arm')!=actual_arms:
            selection_errors.append('PRESELECTED_ARM_COVERAGE_MUST_BE_FIVE_EACH')
        if len(domains)!=4 or actual_domains!=expected_domains or selection.get('coverage_by_domain')!=actual_domains:
            selection_errors.append('PRESELECTED_DOMAIN_COVERAGE_MUST_BE8_8_7_7_IN_REGISTERED_ORDER')
        freeze=json_read(self.out/'manifests/formal-freeze.json',{})
        if selection.get('source_sha256')!=REGISTERED_SPOTCHECK_SELECTOR_SHA256:
            selection_errors.append('PRESELECTOR_SOURCE_HASH_DIFFERS_FROM_REGISTERED_SOURCE')
        # The selector was registered after the execution freeze but before the
        # first main execution. It is not one of the frozen execution files.
        selector_candidates=(self.out/'drivers/formal_statistics_spotcheck.py',
                             self.out/'drivers/frozen/formal_statistics_spotcheck.py',
                             Path(__file__).with_name('formal_statistics_spotcheck.py'))
        selector_copies={str(p):digest(p) for p in selector_candidates if p.exists()}
        for path,source_hash in selector_copies.items():
            self.source(Path(path))
            if source_hash!=REGISTERED_SPOTCHECK_SELECTOR_SHA256:
                selection_errors.append('COPIED_PRESELECTOR_SOURCE_HASH_MISMATCH:'+path)
        timing_status='INCOMPLETE_PENDING_COMPLETE_MAIN'
        first_main_start=None
        relation_to_freeze='UNKNOWN'
        try:
            selected_at=datetime.fromisoformat(selection['created_utc'])
            frozen_at=datetime.fromisoformat(freeze['frozen_at_utc'])
            if selected_at.tzinfo is None or frozen_at.tzinfo is None:
                raise ValueError('timezone required')
            relation_to_freeze='AFTER_FORMAL_FREEZE' if selected_at>frozen_at else 'AT_OR_BEFORE_FORMAL_FREEZE'
            if complete:
                main_starts=[datetime.fromisoformat(row['start_utc']) for row in rows]
                if len(main_starts)!=len(plan) or any(t.tzinfo is None for t in main_starts):
                    raise ValueError('complete main start times required')
                first_main_start=min(main_starts)
                timing_status='PASS' if selected_at<first_main_start else 'FAIL'
                if timing_status=='FAIL':
                    selection_errors.append('SPOTCHECK_SELECTION_NOT_BEFORE_FIRST_MAIN_START')
        except (KeyError,ValueError,TypeError):
            selection_errors.append('SPOTCHECK_SELECTION_TIME_PROVENANCE_MISSING')
        if (selection.get('seed')!=20261019 or selection.get('human_raters')!=0 or
            selection.get('review_method')!='AUTOMATIC_RECORD_CONSISTENCY_REVIEW' or
            selection.get('inter_rater_agreement')!='NOT_APPLICABLE_NO_HUMAN_RATINGS'):
            selection_errors.append('PRESELECTED_REVIEW_PROTOCOL_METADATA_MISMATCH')
        report.update(coverage_by_arm=actual_arms,coverage_by_domain=actual_domains,
                      preselection_validation=('FAIL' if selection_errors else 'PASS' if timing_status=='PASS' else 'INCOMPLETE'),
                      selector_source_sha256_registered=REGISTERED_SPOTCHECK_SELECTOR_SHA256,
                      selector_copied_source_sha256=selector_copies,
                      selector_execution_freeze_membership='NOT_IN_FROZEN_EXECUTION_FILES',
                      selection_created_utc=selection.get('created_utc'),
                      execution_frozen_at_utc=freeze.get('frozen_at_utc'),
                      selection_relation_to_freeze=relation_to_freeze,
                      first_main_start_utc=first_main_start.isoformat() if first_main_start else None,
                      pre_main_selection_timing_validation=timing_status,
                      preselection_timing_rule='Registered selection must precede the earliest start_utc of all complete main terminal rows; execution freeze is not the timing cutoff. No timestamps are rewritten.',
                      selection_validation_errors=selection_errors)
        if selection_errors:
            report['status']='FAIL'
            write_json(self.destination/"analysis/raw_30_unit_audit.json",report)
            return report
        if not complete:
            write_json(self.destination/"analysis/raw_30_unit_audit.json",report)
            return report
        observed={r['run_id']:r for r in rows}
        calls=defaultdict(list); starts=defaultdict(list); ios=defaultdict(list)
        for row in jsonl(self.out/"raw/provider_calls.jsonl"):calls[row['run_id']].append(row)
        for row in jsonl(self.out/"raw/provider_attempt_starts.jsonl"):starts[row['run_id']].append(row)
        for row in jsonl(self.out/"raw/model_io.jsonl"):ios[row['run_id']].append(row)
        checks=[]
        for item in chosen:
            identity=item['run_id'];row=observed[identity];unit_calls=calls[identity]
            request_ids={c['request_id'] for c in unit_calls}
            starts_match=request_ids=={c['request_id'] for c in starts[identity]}
            io_by_id=defaultdict(list)
            for event in ios[identity]:io_by_id[event['request_id']].append(event)
            io_checks=[]
            for call in unit_calls:
                events=io_by_id[call['request_id']]
                request=next((e for e in events if e.get('direction')=='request'),None)
                response=next((e for e in events if e.get('direction')=='response'),None)
                io_checks.append(bool(request) and recorded_sha(request['messages'])==call['actual_prompt_sha256'] and
                    (call['status']!='OK' or bool(response) and recorded_sha(response['response'])==call['response_sha256']))
            check={"run_id":identity,"terminal_exists":True,"attempt_count_matches":len(unit_calls)==row['provider_attempts'],
                "max_three_attempts":len(unit_calls)<=3,"starts_match_terminal_calls":starts_match,
                "actual_model_io_hashes_match":all(io_checks),"actual_fact_audits_pass":all(c.get('business_facts_equal') for c in unit_calls),
                "IPC_byte_conservation":row.get('ipc_accounting',{}).get('framing_consumption_conserved',False),
                "span_partition_conserved":row.get('span_partition_error_sec',1)<1e-6,
                "successful_state_enabled_task_consumed":not (row['success'] and row.get('state_exchange_enabled')) or bool(row.get('state_consumed')),
                "workers_released":not row.get('workers_remaining'),"SHM_released":not row.get('shm_names_created_but_remaining')}
            check['pass']=all(v for k,v in check.items() if k!='run_id')
            checks.append(check)
        report.update(status="PASS" if all(c['pass'] for c in checks) else "FAILED",checks=checks)
        write_json(self.destination/"analysis/raw_30_unit_audit.json",report)
        return report

    def paired(self, plan_a, rows_a, plan_b, rows_b, label_a, label_b, *, scope):
        def key(row):
            return tuple(row[k] for k in ("task_id","repeat","model","topology"))
        if {key(r) for r in plan_a} != {key(r) for r in plan_b}:
            raise ValueError(f"unmatched planned pair: {label_a}/{label_b}")
        complete_a, complete_b = complete_phase(plan_a,rows_a), complete_phase(plan_b,rows_b)
        result={"baseline":label_a,"target":label_b,"inference_scope":scope,
                "planned_paired_units":len(plan_a),"baseline_terminal_units":len(rows_a),
                "target_terminal_units":len(rows_b),"status":"INCOMPLETE",
                "quality_noninferiority_wording":"WITHHELD_BY_PROSPECTIVE_GATE"}
        if not (complete_a and complete_b):
            return result
        a,b={key(r):r for r in rows_a},{key(r):r for r in rows_b}
        pairs=[]
        for p in plan_a:
            aa,bb=dict(a[key(p)]),dict(b[key(p)])
            aa["quality_success"],bb["quality_success"]=int(aa["success"]),int(bb["success"])
            pairs.append({"task_id":p["task_id"],"chain_id":p["chain_id"],"domain":p["domain"],
                          "repeat":p["repeat"],"a":aa,"b":bb})
        result["quality"]=cluster_bootstrap(pairs,"quality_success")
        result["quality"].update(quality_safety_bound(pairs))
        result["metrics"]={}
        for metric in ("provider_input_tokens","observable_ipc_bytes","wall_sec","local_state_materialized_bytes"):
            valid=[p for p in pairs if finite(p["a"].get(metric)) and finite(p["b"].get(metric))]
            if len(valid)!=len(pairs):
                result["metrics"][metric]={"status":"INCOMPLETE","planned_pairs":len(pairs),
                    "measured_pairs":len(valid),"reason":"unknown cost retained; no success selection or zero substitution"}
            else:
                result["metrics"][metric]=cluster_bootstrap(valid,metric)
                result["metrics"][metric]["significance_scope"]="exploratory p_raw; outside ten-test main confirmatory family"
        result["status"]="COMPLETE" if all(v["status"]=="COMPLETE" for v in result["metrics"].values()) else "COMPLETE_TASKS_UNKNOWN_COST"
        return result

    def matched_effects(self, stats):
        corpus_source=self.out/"corpus/automatic_validated_v1/tasks.jsonl"
        corpus={r["task_id"]:r for r in jsonl(corpus_source)}
        self.source(corpus_source)
        main_plan,main_rows,main_complete=self.phase("main")
        applicable={task for task,row in corpus.items() if row["split"]=="formal" and row["state_spec"]["applicable"]}
        subset_plan=[r for r in main_plan if r["task_id"] in applicable]
        subset_rows=[r for r in main_rows if r["task_id"] in applicable]
        if main_complete and complete_phase(subset_plan,subset_rows):
            main=stats["main"]
            subset={"status":"COMPLETE","scope":"STATE108_PRIMARY_INCREMENT_EXTRACTED_FROM_UNIQUE_COMBINED_TEN_TEST_FAMILY",
                    "strata":[{"model":s['model'],"topology":s['topology'],
                        "comparisons":[p for p in s['comparisons'] if (p['baseline'],p['target'])==('S0','SS0')]}
                        for s in main.get('strata',[])],
                    "other_state_subset_descriptive":main.get("state_applicable_descriptive"),
                    "full120_state_deployment_descriptive":main.get("full120_state_deployment_descriptive"),
                    "confirmatory_family":"one combined Holm family: two state108 tests plus eight full120 tests; no independently selected archived p_holm and no second108 family"}
        else:
            subset={"status":"INCOMPLETE","planned_units":len(subset_plan),"observed_units":len(subset_rows),
                    "planned_unique_tasks":len(applicable),"effect_numbers":"WITHHELD"}
        write_json(self.destination/"analysis/statistics_main_state_applicable.json",subset)
        paired={}
        baseline_plan,baseline_rows,_=self.phase("ablation_baseline")
        for phase in self.phases:
            if not (phase.startswith("ablation_") or phase.startswith("serialization_")) or phase=="ablation_baseline":
                continue
            plan,rows,_=self.phase(phase)
            paired[phase]=self.paired(baseline_plan,baseline_rows,plan,rows,"ablation_baseline",phase,
                                    scope="PREPLANNED_MATCHED_60_TASK_EXPLORATORY_MECHANISM_COMPARISON")
        none_plan,none_rows,_=self.phase("memory_condition_none")
        for condition in ("related","unrelated","stale","wrong"):
            phase="memory_condition_"+condition
            plan,rows,_=self.phase(phase)
            paired[phase]=self.paired(none_plan,none_rows,plan,rows,"memory_condition_none",phase,
                                     scope="PREPLANNED_CONTROLLED_INITIAL_MEMORY_CONDITION_EXPLORATORY_COMPARISON")
        if main_complete:
            overall=self.paired([r for r in main_plan if r["arm"]=="T0"],[r for r in main_rows if r["arm"]=="T0"],
                                [r for r in main_plan if r["arm"]=="SS1"],[r for r in main_rows if r["arm"]=="SS1"],
                                "T0","SS1",scope="FULL_SYSTEM_ABSTRACT_DESCRIPTIVE_PAIR_OUTSIDE_PRIMARY_TEN_TEST_FAMILY")
        else:
            overall={"baseline":"T0","target":"SS1","status":"INCOMPLETE",
                     "scope":"No effect numbers before the entire main phase has terminal outcomes",
                     "planned_paired_units":sum(r['arm']=='T0' for r in main_plan),
                     "baseline_terminal_units":sum(r['arm']=='T0' for r in main_rows),
                     "target_terminal_units":sum(r['arm']=='SS1' for r in main_rows)}
        write_json(self.destination/"analysis/matched_phase_effects.json",paired)
        write_json(self.destination/"analysis/overall_T0_to_SS1.json",overall)
        # Keep every ablation as its own condition. The actual effects are stored
        # in full JSON; a compact CSV indexes all predeclared completed/missing pairs.
        compact=[]
        for name,effect in paired.items():
            compact.append({"comparison":name,"baseline":effect["baseline"],"target":effect["target"],
                "status":effect["status"],"planned_pairs":effect["planned_paired_units"],
                "baseline_terminal_units":effect["baseline_terminal_units"],"target_terminal_units":effect["target_terminal_units"],
                "quality_difference":effect.get("quality",{}).get("paired_difference"),
                "quality_conservative_ci95":effect.get("quality",{}).get("quality_conservative_ci95"),
                "input_token_effect":effect.get("metrics",{}).get("provider_input_tokens"),
                "observable_ipc_effect":effect.get("metrics",{}).get("observable_ipc_bytes"),
                "scope":effect["inference_scope"]})
        write_csv(self.destination/"tables/appendix_all_ablations.csv",compact)
        distribution=[]
        for (domain,difficulty),count in sorted(Counter((r["domain"],r["difficulty"]) for r in corpus.values() if r["split"]=="formal").items()):
            distribution.append({"domain":domain,"difficulty":difficulty,"unique_tasks":count,"scope":"frozen formal corpus, not model outcomes"})
        write_csv(self.destination/"tables/appendix_task_distribution.csv",distribution)
        topology_rows=[]
        for phase in ("cross_model","multiprocess"):
            planned,observed,done=self.phase(phase)
            for arm in sorted({r['arm'] for r in planned}):
                current=[r for r in observed if r['arm']==arm]
                item={"phase":phase,"arm":arm,"status":"COMPLETE" if done else "INCOMPLETE",
                      "planned_units":sum(r['arm']==arm for r in planned),"observed_units":len(current),
                      "models":sorted({r['model'] for r in planned}),"topologies":sorted({r['topology'] for r in planned}),
                      "scope":"separate model/topology corroboration; exploratory, never pooled into main confirmatory family"}
                point,lo,hi=quality_interval(current) if done else (None,None,None)
                item.update(success_rate=point,success_ci95_low=lo,success_ci95_high=hi)
                for metric in ("provider_input_tokens","observable_ipc_bytes","wall_sec","local_state_materialized_bytes"):
                    mean,low,high=cluster_mean(current,metric) if done else (None,None,None)
                    item.update({metric+"_mean":mean,metric+"_ci95_low":low,metric+"_ci95_high":high})
                topology_rows.append(item)
        write_csv(self.destination/"tables/appendix_cross_model_topology.csv",topology_rows)
        return subset,overall,paired,corpus

    def finish(self, stats, subgroup, overall, matched, corpus, spot):
        planned, observed, complete = self.phase("main")
        observed_ids = {r["task_id"] for r in observed}
        abstract = {"status":"INCOMPLETE","formal_planned_unique_tasks":len({r['task_id'] for r in planned}),
            "formal_observed_unique_tasks":len(observed_ids),"formal_planned_task_units":len(planned),
            "formal_terminal_task_units":len(observed),"formal_complete":complete,
            "overall_success_difference":None,"provider_input_token_change":None,"observable_ipc_change":None,
            "state_increment":None,"memory_increment":None,"applicability_boundary":None,
            "quality_noninferiority_wording":"WITHHELD_BY_PROSPECTIVE_GATE",
            "provider_usage_authority":"input/output/total require reported usage; cache claims require explicit cache fields in provider_calls.usage_raw; missing cache fields UNKNOWN, never inferred from task-run cached-token default0",
            "provider_wait_visibility":"client SDK elapsed, not separately observable provider queue latency or pure network RTT; both UNKNOWN",
            "rule":"No public calibration, historical pilot, offline fixture, or unavailable cost can fill these fields."}
        if complete and stats.get("main",{}).get("strata"):
            strata=stats["main"]["strata"]
            abstract["prespecified_pairs"]=[{"baseline":p["baseline"],"target":p["target"],"quality":p.get("quality"),"metrics":p.get("metrics"),"status":p["status"]}
                for s in strata for p in s["comparisons"]]
            abstract["status"]="COMPLETE_MAIN_WITH_REQUIRED_LIMITATIONS" if overall["status"]=="COMPLETE" else "COMPLETE_MAIN_UNKNOWN_PROVIDER_COST"
            abstract["overall_comparison_scope"]="T0 to SS1 descriptive paired comparison; outside the main ten-test confirmatory family"
            abstract["overall_success_difference"]=overall.get("quality")
            abstract["provider_input_token_change"]=overall.get("metrics",{}).get("provider_input_tokens")
            abstract["observable_ipc_change"]=overall.get("metrics",{}).get("observable_ipc_bytes")
            abstract["state_increment"]={"scope":"state-applicable subset and full cohort separately",
                "subset_analysis":"statistics_main_state_applicable.json",
                "state_applicable_unique_tasks":len({r['task_id'] for r in planned if corpus[r['task_id']]['state_spec']['applicable']}),
                "subset_pairs":[p for s in subgroup.get("strata",[]) for p in s["comparisons"] if (p["baseline"],p["target"])==("S0","SS0")],
                "full120_deployment_descriptive":stats['main'].get("full120_state_deployment_descriptive"),
                "interpretation":"state108 is the original-protocol primary increment; full120 S0→SS0 is an overall deployment description, not a substitute primary test"}
            abstract["memory_increment"]=[p for s in strata for p in s["comparisons"] if (p["baseline"],p["target"]) in (("T0","T1"),("S0","S1"),("SS0","SS1"))]
            abstract["applicability_boundary"]={"nonstate_unique_tasks":len({r['task_id'] for r in planned if not corpus[r['task_id']]['state_spec']['applicable']}),
                "state_size_distribution":dict(Counter(corpus[task]['state_spec']['size_class'] for task in {r['task_id'] for r in planned} if corpus[task]['state_spec']['applicable'])),
                "environment":"openEuler 24.03 userspace on WSL2 kernel, fixed container limits; network/provider and host activity remain recorded limits",
                "external_frameworks":"system-level references with different fixed external prompt/state semantics",
                "offline_scope":"transport/quantization/retrieval/stress are separate mechanism evidence, never provider token/model-quality substitutes"}
        write_json(self.destination/"analysis/abstract_metrics.json",abstract)
        all_phases_complete=all(self.phase(phase)[2] for phase in self.phases)
        availability={"material_passport":{"artifact_type":"rebuildable_formal_experiment_report","created_utc":datetime.now(timezone.utc).isoformat(),
                "source_scope":"formal real phases plus explicitly labelled offline mechanisms; public calibration excluded from formal effects"},
            "status":"COMPLETE_DATA_REQUIRES_VISUAL_QA" if all_phases_complete and all(v["status"]=="COMPLETE" for v in [*self.figures.values(),*self.tables.values()]) and all(effect["status"]!="INCOMPLETE" for effect in matched.values()) and spot['status']=='PASS' else "INCOMPLETE",
            "figures":self.figures,"tables":self.tables,"phase_availability":{phase:{"planned_units":len(self.phase(phase)[0]),"terminal_units":len(self.phase(phase)[1]),"complete":self.phase(phase)[2]} for phase in self.phases},
            "source_sha256":self.sources,"plot_style":{"arm_colors":COLORS,"memory_off":"open circle","memory_on":"filled square",
                "offline_noncommunication_factors":"neutral greys and shapes","risk_color":RISK,
                "single_column_inches":3.35,"double_column_inches":7.0,"raster_dpi":600,"vector_pdf_fonttype":42,"svg_text":"searchable text"},
            "limitations":["Formal numeric figures are withheld until their entire planned phase has terminal outcomes.",
                "Known completed failures remain in all planned denominators; unreported provider usage is unknown.",
                "No noninferiority wording; repeated runs are not independent cases.",
                "Raw records are never modified. Derived report outputs may be rebuilt.",
                "State-applicable subgroup and controlled-mechanism/memory comparisons remain separate from full main confirmation."],
            "automatic_30_unit_audit":spot['status'],
            "automatic_30_unit_authority":{"path":"analysis/stats/automatic_spotcheck_plan.json",
                "selection_plan_sha256":spot.get('selection_plan_sha256'),
                "validation":spot.get('preselection_validation'),
                "coverage_by_arm":spot.get('coverage_by_arm'),"coverage_by_domain":spot.get('coverage_by_domain'),
                "replacement_sample_allowed":False},
            "measurement_visibility":{
                "cache_tokens":"only explicitly present provider_calls.usage_raw cache fields are authoritative; missing fields UNKNOWN; no task cached-token default0 used by this report",
                "input_output_total_tokens":"reported usage authority unchanged by missing cache details",
                "provider_wait_sec":"client-observed SDK call elapsed including request/response processing and instrumentation; not provider queue latency or pure RTT",
                "provider_queue_latency_sec":"UNKNOWN_NOT_SEPARATELY_EXPOSED",
                "pure_network_RTT_sec":"UNKNOWN_NOT_SEPARATELY_IDENTIFIABLE",
                "billing":"no cache-based charge estimate may use defaulted or missing cache values; applicable tariff/account billing remains separately required"},
            "primary_statistics_implementation":"formal_statistics_protocol_aligned_v3.py",
            "primary_confirmation_family":"one ten-test Holm family: S0↔SS0 two endpoints on108 applicable tasks and other four pairs eight endpoints on120",
            "visual_qa":"PENDING_SEPARATE_RENDER_AND_LAYOUT_REVIEW; rebuild script does not claim visual inspection"}
        write_json(self.destination/"analysis/report_availability.json",availability)
        lines=["# Claims to evidence", "", "Material Passport: derived report index; automatic verification authorized by the user; no human ratings are synthesized.", "",
               "| Claim/output | Status | Evidence | Boundary |", "|---|---|---|---|"]
        for number,record in self.figures.items():
            paths="; ".join(record["source_paths"])
            lines.append(f"| {number} | {record['status']} | {paths} | {record['caption']} |")
        for number,record in self.tables.items():
            lines.append(f"| {number} | {record['status']} | {'; '.join(record['source_paths'])} | {record['caption']} |")
        lines.extend(["", "Formal abstract metrics: analysis/abstract_metrics.json. Empty fields are outstanding gates, not zero effects.",
                      "", "No external framework result supports single-mechanism causality; no offline retrieval result supports model accuracy.",
                      "No quality noninferiority statement is permitted under the frozen prospective decision.",
                      "", "Provider usage authority: cache counts require explicitly present cache fields in provider_calls.usage_raw. Missing cache details are UNKNOWN; task-run cached-token default0 is not a provider-reported0. This does not invalidate explicitly reported input/output/total tokens. Client SDK wait is a combined observed duration; separate provider queue latency and pure network RTT remain UNKNOWN.",
                      "", "Protocol section4.1 alignment: S0→SS0 excludes the predeclared NOT_APPLICABLE cases and uses108 tasks; the other four primary pairs retain120. Exactly ten cost tests are adjusted together once with Holm. Frozen all120 replay values remain archive_only and are not used to choose significance;108 descriptive outputs never define a second confirmatory family.",
                      "This corrects an implementation omission found by a read-only protocol/design audit before formal effects were inspected. It restores the original text, adds no research condition, and is separate from the user's sole authorized human-review replacement."])
        (self.destination/"claims_to_evidence.md").write_text("\n".join(lines)+"\n")
        (self.destination/"README_reproduce.md").write_text(
            "# Rebuild the derived report\n\nMaterial Passport: reproducible analysis; raw evidence is read-only input.\n\n"
            "Run inside the preserved openEuler container using the formal environment:\n\n"
            "```bash\nPYTHONPATH=/drivers:/opt/lingxi/formal-checkout/src /opt/lingxi/formal-venv/bin/python /drivers/formal_report.py\n```\n\n"
            "The command reads formal_plan.json and real task_runs.jsonl, excludes public calibration from formal effects, calls the protocol-aligned v3 statistics API only for complete main data, and exports available figures as PDF/SVG/600 dpi PNG in single/double column sizes. Main confirmation uses one Holm family of ten: S0→SS0's two endpoints on108 applicable tasks plus the other four pairs' eight endpoints on120. Frozen all120 replay values remain archive_only; no archived p_holm is selected and no second108-task confirmatory family is created. This restores original section4.1 after a read-only design audit found an implementation omission before formal effects were inspected. Each figure has a CSV and caption/status JSON. Main results are not synthesized from missing or offline data.\n\n"
            "Missing complete phases produce INCOMPLETE status and empty numerical figure sources. The30-unit raw review uses exactly the pre-main registered analysis/stats/automatic_spotcheck_plan.json; copy it unchanged when reconstructing a new directory. Selection bytes/formal-plan hash/IDs/five-per-arm/8,8,7,7 domain coverage/source/time provenance are validated; no new sample substitutes for these30. Cache-token declarations must use explicitly present cache fields in provider_calls.usage_raw; missing fields are UNKNOWN, not task-run default0. Client SDK wait is not provider queue latency or pure RTT; both remain separately UNKNOWN. Offline tables must first be rebuilt by their own recorded analyzers. Review analysis/report_availability.json and claims_to_evidence.md for all remaining gates.\n")
        hashes=[]
        for path in sorted(self.destination.rglob("*")):
            if path.is_file() and path.name!="checksums.sha256":
                hashes.append(digest(path)+"  "+str(path.relative_to(self.destination)))
        (self.destination/"checksums.sha256").write_text("\n".join(hashes)+"\n")
        return availability


def error_type(row):
    error=row.get("error")
    if isinstance(error,dict):return str(error.get("type") or error.get("error_type") or "verification_failure")
    if isinstance(error,list) and error:return str(error[0].get("type","execution_failure"))
    if isinstance(error,str) and error:return "runtime_failure"
    return "verification_failure"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=Path("/evidence/formal_20261019"))
    parser.add_argument("--report-dir",type=Path)
    parser.add_argument("--retrieval-root",type=Path)
    args=parser.parse_args()
    report=Report(args.out,args.report_dir or args.out/"report",args.retrieval_root)
    report.overview()
    stats=report.statistics()
    summary=report.main_tables()
    report.main_figures(stats,summary)
    retrieval,stress=report.offline_figures()
    report.memory_and_references(stats,retrieval)
    subgroup,overall,matched,corpus=report.matched_effects(stats)
    spot=report.spot_check()
    status=report.finish(stats,subgroup,overall,matched,corpus,spot)
    print(json.dumps({"status":status["status"],"output":str(report.destination),
        "figure_status":{k:v["status"] for k,v in status["figures"].items()},
        "table_status":{k:v["status"] for k,v in status["tables"].items()}},ensure_ascii=False),flush=True)


if __name__=="__main__":main()
