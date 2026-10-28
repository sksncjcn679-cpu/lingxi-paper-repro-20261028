"""OS-SOTA1.5: automatic OS-SOTA2 release-gate evaluation from result directories.

Reads the newest matching ``results/*-<suite>/summary.json`` (or explicit paths) and decides
each gate as PASS / FAIL / PENDING (evidence missing).  Nothing here runs experiments.

Gates (docs/OS_SOTA1_1_DEV.md §5, docs/TEST_METHOD_OS_SOTA1_6.md):

G1 openEuler target host: scorecard 20/20 non-environment AND openEuler verified.
G2 real LLM bill: provider-billed ``external_llm`` with repeat >= 3; FULL_coalesce saving vs
   T_memory_pair: mean >= 85 % and bootstrap 95 % CI lower bound >= 80 %.
G3 semantic validity: judge enabled; batched accuracy >= direct - 5 pp and structured
   >= text - 5 pp (Wilson intervals reported).
G4 beyond-bill evidence: a provider-billed ``long_session`` run with 100 % success, no
   unanswered logical request and SS+G cheaper than T+G.
G5 Windows run: no unexpected non-zero exits in a ``run_all_suites`` log (the two Linux-only
   verification gates are expected to fail on Windows).
"""

from __future__ import annotations

import json
import math
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_WINDOWS_NONZERO = ("competition-check", "verify_results.py --require-linux")


def _latest(root: Path, suite: str, predicate=None) -> tuple[Path, dict[str, Any]] | None:
    for d in sorted(root.glob(f"*-{suite}"), reverse=True):
        f = d / "summary.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if predicate is None or predicate(data):
            return d, data
    return None


def _judge_on(data: dict[str, Any]) -> bool:
    return any(v.get("judge_evaluated") for v in (data.get("summary") or {}).values() if isinstance(v, dict))


def _sha(data: dict[str, Any]) -> str | None:
    return ((data.get("env") or {}).get("source_tree_sha256")) or None


def _is_real(data: dict[str, Any]) -> bool:
    prov = str((data.get("provider") or {}).get("llm_provider", "")).lower()
    return bool(prov) and not prov.startswith("mock")


def _bootstrap_ci(values: list[float], iters: int = 5000, seed: int = 7) -> tuple[float, float]:
    if len(values) < 2:
        return (values[0], values[0]) if values else (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(iters))
    return means[int(0.025 * iters)], means[int(0.975 * iters) - 1]


def _wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def _total(row: dict[str, Any]) -> float:
    return float(row.get("llm_prompt_tokens", 0)) + float(row.get("llm_completion_tokens", 0))


def gate_openeuler(root: Path) -> dict[str, Any]:
    got = _latest(root, "competition_scorecard")
    if not got:
        return {"status": "PENDING", "detail": "no competition_scorecard result"}
    d, data = got
    s = data.get("summary", {})
    ok = bool(s.get("all_non_environment_mandatory_passed")) and bool(s.get("openeuler_target_verified"))
    status = "PASS" if ok else ("PENDING" if s.get("all_non_environment_mandatory_passed") else "FAIL")
    return {"status": status, "source": d.name, "sha": _sha(data),
            "detail": f"non-env {s.get('mandatory_non_environment_passed')}/{s.get('mandatory_non_environment_total')}; "
                      f"openEuler verified={s.get('openeuler_target_verified')}"}


def _bill_pairs(data: dict[str, Any], arm: str, base: str) -> list[float]:
    raw = data.get("raw", {})
    a, b = raw.get(arm, []), raw.get(base, [])
    return [1 - _total(x) / _total(y) for x, y in zip(a, b)
            if not x.get("crashed") and not y.get("crashed") and _total(y) > 0]


def gate_real_bill(root: Path) -> dict[str, Any]:
    """G2 uses a *judge-off* run: the semantic judge is a measurement instrument whose requests
    perturb the bill asymmetrically (OS-SOTA1.5 real data: +2 requests on 10 for T_memory_pair
    but +1 request on 1 for FULL_coalesce, because the memory fast-path task has no other LLM
    item to share a request with).  Judge-on runs are reported, never used to decide G2."""
    clean = _latest(root, "external_llm", lambda x: _is_real(x) and not _judge_on(x))
    perturbed = _latest(root, "external_llm", lambda x: _is_real(x) and _judge_on(x))
    if not clean:
        detail = "no provider-billed judge-off external_llm result"
        if perturbed:
            sv = _bill_pairs(perturbed[1], "FULL_coalesce", "T_memory_pair")
            if sv:
                detail += (f"; judge-on run (instrument-perturbed, not decisive): FULL_coalesce {sum(sv) / len(sv):.1%}"
                           f", n={len(sv)} ({perturbed[0].name})")
        return {"status": "PENDING", "detail": detail}
    d, data = clean
    savings = _bill_pairs(data, "FULL_coalesce", "T_memory_pair")
    if not savings:
        return {"status": "PENDING", "source": d.name, "sha": _sha(data), "detail": "FULL_coalesce/T_memory_pair cells missing"}
    mean = sum(savings) / len(savings)
    lo, hi = _bootstrap_ci(savings)
    status = "PENDING" if len(savings) < 3 else ("PASS" if mean >= 0.85 and lo >= 0.80 else "FAIL")
    extra = ""
    grp = _bill_pairs(data, "FULL_group", "T_memory_pair")
    if grp:
        extra = f"; FULL_group {sum(grp) / len(grp):.1%}"
    return {"status": status, "source": d.name, "sha": _sha(data), "repeats": len(savings), "mean_saving": mean,
            "ci95": [lo, hi],
            "detail": f"judge-off FULL_coalesce vs T_memory_pair: mean {mean:.1%}, 95% CI [{lo:.1%}, {hi:.1%}], n={len(savings)}{extra}"}


_DIRECT = ["T", "T_memory_pair", "S", "SS", "SS_memory_off", "SS_memory_on", "SS_concise", "FULL", "T_related", "FULL_related"]
_TEXT = ["T", "T_memory_pair", "T_coalesce", "T_memory_pair_coalesce", "T_group", "T_related", "T_related_group", "T_group_sv"]


def gate_semantic(root: Path) -> dict[str, Any]:
    got = _latest(root, "external_llm", lambda x: _is_real(x) and any(
        (v.get("judge_evaluated") for v in x.get("summary", {}).values())))
    if not got:
        return {"status": "PENDING", "detail": "no provider-billed external_llm run with --judge"}
    d, data = got
    raw = data.get("raw", {})

    def acc(ids):
        k = n = 0.0
        for cid in ids:
            for r in raw.get(cid, []):
                n += float(r.get("llm_judgements", 0) or 0)
                k += float(r.get("llm_judgements_correct", 0) or 0)
        return k, n

    all_ids = list(raw)
    direct = acc([c for c in all_ids if c in _DIRECT])
    batched = acc([c for c in all_ids if c not in _DIRECT])
    text = acc([c for c in all_ids if c in _TEXT])
    struct = acc([c for c in all_ids if c not in _TEXT])
    rate = lambda kn: kn[0] / kn[1] if kn[1] else float("nan")  # noqa: E731
    ok = (direct[1] > 0 and batched[1] > 0 and text[1] > 0 and struct[1] > 0
          and rate(batched) >= rate(direct) - 0.05 and rate(struct) >= rate(text) - 0.05)
    fmt = lambda name, kn: f"{name} {rate(kn):.1%} (n={int(kn[1])}, Wilson {_wilson(*kn)[0]:.0%}–{_wilson(*kn)[1]:.0%})"  # noqa: E731
    return {"status": "PASS" if ok else "FAIL", "source": d.name, "sha": _sha(data),
            "detail": "; ".join([fmt("direct", direct), fmt("batched", batched), fmt("text", text), fmt("structured", struct)])}


def gate_long_session(root: Path) -> dict[str, Any]:
    got = _latest(root, "long_session", _is_real)
    if not got:
        return {"status": "PENDING", "detail": "no provider-billed long_session result"}
    d, data = got
    s = data.get("summary", {})
    ok_success = all(x.get("success_rate") == 1.0 for x in s.values())
    ok_answers = bool(data.get("checks", {}).get("no_unanswered_logical_requests"))
    cheaper = ("SS+G" in s and "T+G" in s and s["SS+G"]["billed_total_tokens_mean"] < s["T+G"]["billed_total_tokens_mean"])
    ok = ok_success and ok_answers and cheaper
    detail = f"success={ok_success}; all answered={ok_answers}; SS+G<T+G={cheaper}"
    if "FULL+G" in s:
        detail += f"; FULL+G saving vs T={s['FULL+G'].get('billed_saving_vs_T', 0):.1%}"
    if "SS+G" in s:
        detail += f"; SS+G repairs={s['SS+G'].get('llm_coalesce_repairs_mean', 0):.0f}"
    return {"status": "PASS" if ok else "FAIL", "source": d.name, "sha": _sha(data), "detail": detail}


def gate_windows(log: Path | None) -> dict[str, Any]:
    if log is None or not log.exists():
        return {"status": "PENDING", "detail": "no Windows run_all_suites log given (--windows-log)"}
    text = log.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    heads, current = [], None
    for line in text.splitlines():
        m = re.match(r"^===== (EXIT:(\d+)|.+?) =====$", line)
        if not m:
            continue
        if m.group(2) is not None:
            if current is not None:
                heads.append((current, m.group(2)))
            current = None
        else:
            current = m.group(1)
    bad = [cmd for cmd, rc in heads if rc != "0" and not any(e in cmd for e in EXPECTED_WINDOWS_NONZERO)]
    shas = sorted(set(re.findall(r"Source SHA-256: `([0-9a-f]{64})`", text)))
    return {"status": "PASS" if not bad else "FAIL", "sha": shas[0] if len(shas) == 1 else (shas or None),
            "detail": f"{len(heads)} commands; unexpected non-zero: {bad or 'none'}; source SHA(s) in log: "
                      f"{', '.join(x[:12] for x in shas) or 'none'}"}


def evaluate(root: str | Path = "results", windows_log: str | Path | None = None,
             expect_sha: str | None = None) -> dict[str, Any]:
    """``expect_sha``: the source tree the evidence must come from (default: this checkout).

    OS-SOTA1.6: evidence produced by a different source tree is marked INVALID.  The 1.5
    Windows run had silently imported an old install (opus4.8, SHA 7eed80fb...), which this
    rule would have caught automatically."""
    root = Path(root)
    if expect_sha is None:
        from agent_runtime.evaluation.runner import _env_info

        expect_sha = _env_info()["source_tree_sha256"]
    gates = {
        "G1_openeuler": gate_openeuler(root),
        "G2_real_bill": gate_real_bill(root),
        "G3_semantic_validity": gate_semantic(root),
        "G4_long_session": gate_long_session(root),
        "G5_windows": gate_windows(Path(windows_log) if windows_log else None),
    }
    for g in gates.values():
        sha = g.get("sha")
        shas = sha if isinstance(sha, list) else ([sha] if sha else [])
        if shas and any(x != expect_sha for x in shas):
            g["status"] = "INVALID"
            g["detail"] = f"evidence from source {', '.join(x[:12] for x in shas)} != expected {expect_sha[:12]}; " + g.get("detail", "")
    return {"suite": "sota2_gate", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "root": str(root), "expected_source_sha": expect_sha, "gates": gates,
            "release_ready": all(g["status"] == "PASS" for g in gates.values())}


def render(p: dict[str, Any]) -> str:
    lines = ["# OS-SOTA2 release gates", "", "| Gate | Status | Evidence |", "| --- | --- | --- |"]
    for k, g in p["gates"].items():
        lines.append(f"| {k} | **{g['status']}** | {g.get('detail', '')}{' (' + g['source'] + ')' if g.get('source') else ''} |")
    lines += ["", f"- Expected source SHA: `{p.get('expected_source_sha', '')}`",
              f"- Release ready: **{p['release_ready']}**", ""]
    return "\n".join(lines)


def run_sota2_gate_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _write_checksum_manifest

    p = evaluate(getattr(args, "results", "results"), getattr(args, "windows_log", None),
                 getattr(args, "expect_sha", None))
    out = _allocate_result_dir("results", "sota2_gate")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render(p), encoding="utf-8")
    _write_checksum_manifest(out)
    print(render(p))
    print(f"结果已保存到: {out}")
    return 0 if (p["release_ready"] or not getattr(args, "strict", False)) else 1


__all__ = ["evaluate", "render", "run_sota2_gate_cli"]
