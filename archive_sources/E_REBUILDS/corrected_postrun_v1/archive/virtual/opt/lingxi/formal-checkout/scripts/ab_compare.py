#!/usr/bin/env python3
"""Same-host, interleaved, paired A/B comparison between two source trees (fable7).

Previous release notes compared a 16.4 ms and a 19.3 ms "mean task latency" taken on different
days and VM states.  That cannot separate a version effect from host noise.  This script runs the
SAME contest workload from each source tree in fresh subprocesses, alternating A/B/A/B..., and
reports medians plus a paired 95% CI of the per-round differences.

    python scripts/ab_compare.py --baseline ../fable5/source --rounds 8 --out ab.json

Only mechanisms that both trees implement are exercised (the public contest workload API).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

_PROBE = r'''
import json, statistics, sys, time
from agent_runtime.evaluation.runner import ExperimentConfig, run_once
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
mode = sys.argv[1]
cfgs = {
 "T": ExperimentConfig("T","x","text",False,False,True,text_style="lean",control_transport="uds"),
 "SS": ExperimentConfig("SS","x","structured",True,False,True,structured_wire_codec="session_msgpack",
                        control_transport="uds",state_shared_memory=True,state_precision="adaptive"),
}
cfg = cfgs[mode]
run_once(cfg, _self_contained_tasks, 42)  # warm router + fork-server
lat = []
row = None
for _ in range(5):
    row = run_once(cfg, _self_contained_tasks, 42)
    lat.append(row["latency_sec"] / 4 * 1000)
from agent_runtime.sandbox.executor import CodeActExecutor
from agent_runtime.config import SandboxConfig
ex = CodeActExecutor(SandboxConfig(warm_start=True))
code = "import json\nprint(json.dumps({'value': 'port_conflict'}))\n"
for _ in range(5): ex.run(code)
sb = []
for _ in range(40):
    t = time.perf_counter(); ex.run(code); sb.append((time.perf_counter() - t) * 1000)
control = (row["ipc_framed_bytes"] + row["ipc_ack_bytes"]) if row["ipc_frames"] else row["wire_bytes"]
print(json.dumps({
  "latency_ms": statistics.median(lat),
  "tokens": row["text_tokens"],
  "full_bytes": control + row["state_bytes"] + row["artifact_bytes"],
  "socket_calls": row["ipc_socket_calls"],
  "sandbox_p50_ms": statistics.median(sb),
  "success": row["task_success"],
}))
'''

_T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


def _run(src: Path, mode: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src / "src")
    out = subprocess.run([sys.executable, "-c", _PROBE, mode], env=env, cwd=src, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"{src}: {out.stderr[-2000:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def _paired(a: list[float], b: list[float]) -> dict:
    d = [y - x for x, y in zip(a, b)]
    mean = statistics.fmean(d)
    if len(d) < 2:
        return {"mean_diff": mean, "low": mean, "high": mean}
    half = _T.get(len(d) - 1, 1.96) * statistics.stdev(d) / math.sqrt(len(d))
    return {"mean_diff": mean, "low": mean - half, "high": mean + half}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="baseline source tree (contains src/agent_runtime)")
    ap.add_argument("--candidate", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--mode", choices=["SS", "T"], default="SS")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    a_src, b_src = Path(args.baseline).resolve(), Path(args.candidate).resolve()
    rows = {"A": [], "B": []}
    for i in range(args.rounds):
        order = (("A", a_src), ("B", b_src)) if i % 2 == 0 else (("B", b_src), ("A", a_src))
        for name, src in order:
            rows[name].append(_run(src, args.mode))
            print(f"round {i} {name}: {rows[name][-1]}", file=sys.stderr, flush=True)
    keys = ("latency_ms", "tokens", "full_bytes", "socket_calls", "sandbox_p50_ms")
    report = {"baseline": str(a_src), "candidate": str(b_src), "rounds": args.rounds, "mode": args.mode,
              "cpu_count": os.cpu_count(), "metrics": {}}
    for k in keys:
        av = [r[k] for r in rows["A"]]
        bv = [r[k] for r in rows["B"]]
        report["metrics"][k] = {
            "baseline_median": statistics.median(av),
            "candidate_median": statistics.median(bv),
            "relative_change": statistics.median(bv) / statistics.median(av) - 1 if statistics.median(av) else None,
            "paired_diff_ci95": _paired(av, bv),
        }
    report["all_success"] = all(r["success"] == 1.0 for v in rows.values() for r in v)
    report["raw"] = rows
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print("| metric | baseline | candidate | change | paired Δ 95% CI |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for k, m in report["metrics"].items():
        ci = m["paired_diff_ci95"]
        change = f"{m['relative_change']:+.1%}" if m["relative_change"] is not None else "n/a"
        print(f"| {k} | {m['baseline_median']:.3f} | {m['candidate_median']:.3f} | {change} | "
              f"[{ci['low']:+.3f}, {ci['high']:+.3f}] |")
    print(f"all_success={report['all_success']} cpu_count={report['cpu_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
