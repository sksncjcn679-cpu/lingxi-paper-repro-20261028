"""sota1: provider billing decomposition (why nine versions plateaued at ~55% on the real bill).

A provider-billed prompt is modelled as

    billed_prompt = overhead_per_request · provider_requests + visible_scale · visible_tokens

``visible_tokens`` is what the runtime itself sent (system + prompt, counted with the local
tokenizer); ``overhead_per_request`` is whatever the gateway adds on every request (hidden
system prompt, tool schemas, safety preamble ...); ``visible_scale`` absorbs the local vs.
provider tokenizer ratio.  The model is fitted by non-negative least squares without intercept.

On the archived 2026-09-24 gpt-6-luna rerun (fable7…fable16, 8 variants each, 72 cells;
``evidence/real_llm_rerun_20260924/cells.csv``) the fit is 4,382 tokens/request + 1.311·visible,
R² = 0.9998, max relative error 0.67 %.  The fixed part is 97 % of the Structured+State prompt
bill, which is exactly why communication-side work stopped moving the provider bill and why
sota1 attacks the number of provider requests (``runtime/llm_gateway.py``).

The decomposition is also computed live for every ``external-llm`` run from that run's own
cells, and a one-request probe can measure the overhead directly (``AGENT_LLM_OVERHEAD_PROBE``).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

ARCHIVE_CSV = Path("evidence") / "real_llm_rerun_20260924" / "cells.csv"


def fit_billing(rows: list[dict[str, float]]) -> dict[str, float] | None:
    """Fit ``billed = O·requests + k·visible`` (O, k >= 0).  ``None`` if under-determined."""
    pts = [
        (float(r["requests"]), float(r["visible"]), float(r["billed"]))
        for r in rows
        if float(r.get("requests", 0)) > 0 and float(r.get("billed", 0)) > 0
    ]
    if len(pts) < 2:
        return None
    X = np.array([[a, b] for a, b, _ in pts], dtype=float)
    y = np.array([c for _, _, c in pts], dtype=float)
    if np.linalg.matrix_rank(X) < 2:
        return None
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    if coef[0] < 0:  # no hidden overhead: pure scale model
        coef = np.array([0.0, float((X[:, 1] @ y) / max(1e-9, X[:, 1] @ X[:, 1]))])
    elif coef[1] < 0:
        coef = np.array([float((X[:, 0] @ y) / max(1e-9, X[:, 0] @ X[:, 0])), 0.0])
    pred = X @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "overhead_per_request": float(coef[0]),
        "visible_scale": float(coef[1]),
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0,
        "max_rel_err": float(np.max(np.abs(pred - y) / y)),
        "cells": len(pts),
    }


def _rows_from_raw(raw: dict[str, list[dict[str, Any]]]) -> list[dict[str, float]]:
    rows = []
    for cells in raw.values():
        for r in cells:
            if r.get("crashed") or "llm_prompt_tokens" not in r:
                continue
            rows.append({
                "requests": r.get("llm_calls", 0),
                "visible": r.get("llm_visible_prompt_tokens", r.get("llm_prompt_tokens", 0)),
                "billed": r.get("llm_prompt_tokens", 0),
            })
    return rows


def billing_decomposition(raw: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Per-run decomposition: fixed share of each arm's prompt bill and net (controllable) savings."""
    fit = fit_billing(_rows_from_raw(raw))
    if fit is None:
        return {"fit": None, "note": "under-determined (need >=2 cells with different request/visible mixes)"}
    o = fit["overhead_per_request"]
    means: dict[str, dict[str, float]] = {}
    for cid, cells in raw.items():
        ok = [c for c in cells if not c.get("crashed") and "llm_prompt_tokens" in c]
        if not ok:
            continue
        means[cid] = {
            "billed": float(np.mean([c["llm_prompt_tokens"] for c in ok])),
            "requests": float(np.mean([c["llm_calls"] for c in ok])),
        }
    fixed_share = {cid: (o * m["requests"] / m["billed"]) if m["billed"] else 0.0 for cid, m in means.items()}
    net = {cid: max(0.0, m["billed"] - o * m["requests"]) for cid, m in means.items()}
    net_saving = {}
    for cid, base in (("S", "T"), ("SS", "T"), ("SS_concise", "T"), ("FULL", "T_memory_pair"),
                      ("SS_coalesce", "T"), ("FULL_coalesce", "T_memory_pair"), ("SS_group", "T"),
                      ("FULL_group", "T_memory_pair")):
        if cid in net and base in net and net[base] > 0:
            net_saving[cid] = 1.0 - net[cid] / net[base]
    return {
        "fit": fit,
        "fixed_share": fixed_share,
        "net_prompt_tokens": net,
        "net_prompt_saving": net_saving,
        "interpretation": (
            "overhead_per_request is billed on every provider request regardless of content; "
            "net prompt tokens are the runtime-controllable remainder"
        ),
    }


def probe_provider_overhead(cfg: Any) -> dict[str, Any] | None:
    """Measure the hidden per-request prompt overhead with one tiny request.

    Disabled for the plain mock provider (it has no hidden overhead by construction) and when
    ``AGENT_LLM_OVERHEAD_PROBE=0``.  Failures are reported, never raised.
    """
    provider = str(getattr(cfg.llm, "provider", "mock")).lower()
    if provider == "mock":
        return None
    if os.environ.get("AGENT_LLM_OVERHEAD_PROBE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {"status": "disabled"}
    try:
        from agent_runtime.providers.factory import build_llm

        llm = build_llm(cfg)
        resp = llm.complete("Reply with: ok", system=None, temperature=0.0, max_tokens=16)
        visible = int(resp.metadata.get("visible_prompt_tokens", resp.metadata.get("runtime_visible_prompt_tokens", 0)) or 0)
        if not visible:
            from agent_runtime.providers.tokenizer import get_tokenizer

            visible = get_tokenizer().count("\nReply with: ok")
        return {
            "status": "measured",
            "billed_prompt_tokens": int(resp.prompt_tokens),
            "visible_prompt_tokens": visible,
            "overhead_tokens": max(0, int(resp.prompt_tokens) - visible),
            "completion_tokens": int(resp.completion_tokens),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": f"failed: {type(exc).__name__}: {str(exc)[:160]}"}


def load_archive(path: str | Path = ARCHIVE_CSV) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def archive_report(path: str | Path = ARCHIVE_CSV) -> dict[str, Any]:
    cells = load_archive(path)
    fit = fit_billing([
        {"requests": c["provider_requests"], "visible": c["visible_prompt_tokens"], "billed": c["billed_prompt_tokens"]}
        for c in cells
    ])
    assert fit is not None
    o = fit["overhead_per_request"]
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for c in cells:
        by_variant.setdefault(c["variant"], []).append(c)
    variants = {}
    for var, cs in by_variant.items():
        req = [float(c["provider_requests"]) for c in cs]
        billed = [float(c["billed_prompt_tokens"]) for c in cs]
        comp = [float(c["completion_tokens"]) / float(c["provider_requests"]) for c in cs]
        variants[var] = {
            "versions": len(cs),
            "billed_prompt_mean": float(np.mean(billed)),
            "fixed_share_mean": float(np.mean([o * r / b for r, b in zip(req, billed)])),
            "completion_per_request_min": float(min(comp)),
            "completion_per_request_max": float(max(comp)),
        }
    # Out-of-sample check of the request-count term (what coalescing changes): fit only on the
    # 8-request cells, predict every cell that used a different number of requests.
    holdout = None
    train = [c for c in cells if c["provider_requests"] == "8"]
    test = [c for c in cells if c["provider_requests"] != "8"]
    hfit = fit_billing([
        {"requests": c["provider_requests"], "visible": c["visible_prompt_tokens"], "billed": c["billed_prompt_tokens"]}
        for c in train
    ]) if train and test else None
    if hfit is not None:
        errs = [
            abs(hfit["overhead_per_request"] * float(c["provider_requests"]) + hfit["visible_scale"] * float(c["visible_prompt_tokens"])
                - float(c["billed_prompt_tokens"])) / float(c["billed_prompt_tokens"])
            for c in test
        ]
        holdout = {"train_cells": len(train), "test_cells": len(test), "max_rel_err": max(errs), "mean_rel_err": float(np.mean(errs))}
    capped = [c for c in cells if c["variant"] in ("SS_concise", "FULL")]
    violations = [
        c for c in capped
        if float(c["completion_tokens"]) / float(c["provider_requests"]) > 96 + 9.6
    ]
    return {
        "suite": "billing_model",
        "archive": str(path),
        "fit": fit,
        "holdout_request_count": holdout,
        "variants": variants,
        "cap96_cells": len(capped),
        "cap96_cells_with_mean_over_cap": len(violations),
    }


def render_archive_report(p: dict[str, Any]) -> str:
    f = p["fit"]
    lines = [
        "# Provider billing decomposition (archived real-LLM rerun)",
        "",
        f"> {f['cells']} provider-billed cells (fable7…fable16 × 8 variants, gpt-6-luna, 2026-09-24).",
        "",
        f"billed_prompt = **{f['overhead_per_request']:.0f}** · requests + **{f['visible_scale']:.3f}** · runtime-visible tokens "
        f"(R² = {f['r2']:.4f}, max relative error {f['max_rel_err']:.2%})",
        "",
        "| Variant | Versions | Mean billed prompt | Fixed (per-request) share | Completion/request min–max |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for var, v in p["variants"].items():
        lines.append(
            f"| {var} | {v['versions']} | {v['billed_prompt_mean']:,.0f} | {v['fixed_share_mean']:.1%} | "
            f"{v['completion_per_request_min']:.0f}–{v['completion_per_request_max']:.0f} |"
        )
    h = p.get("holdout_request_count")
    if h:
        lines += ["", f"- Out-of-sample: fitted on the {h['train_cells']} eight-request cells only, the model predicts the "
                  f"{h['test_cells']} four-request cells with max error **{h['max_rel_err']:.2%}** (mean {h['mean_rel_err']:.2%}); "
                  "the bill is linear in the number of provider requests."]
    lines += [
        "",
        f"- Completion cap 96/request was exceeded on average in **{p['cap96_cells_with_mean_over_cap']}/{p['cap96_cells']}** "
        "capped cells (SS_concise, FULL): the gateway did not enforce `max_tokens` (motivates the completion governor).",
        "- The fixed per-request share bounds what any communication-side optimisation can save on the bill "
        "(Amdahl); reducing provider requests is the remaining lever (LLM call coalescing).",
        "",
    ]
    return "\n".join(lines)


SOTA1_REAL_DIR = Path("evidence") / "real_llm_os_sota1_20260924"


def projection_validation(real_dir: str | Path = SOTA1_REAL_DIR) -> dict[str, Any] | None:
    """Prospective check: the OS-SOTA1 bill projection (made before the real run) vs the bill
    the provider actually charged on the same 15 variants."""
    real_dir = Path(real_dir)
    ck, pj = real_dir / "checkpoint.jsonl", real_dir / "projection_before_run.json"
    if not (ck.exists() and pj.exists()):
        return None
    proj = json.loads(pj.read_text(encoding="utf-8"))["summary"]
    real: dict[str, dict[str, Any]] = {}
    header: dict[str, Any] = {}
    for line in ck.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if "header" in item:
            header = item["header"]
        elif "variant" in item:
            real[item["variant"]] = item["row"]
    rows = []
    for cid, r in real.items():
        if cid not in proj:
            continue
        rp, pp = float(r["llm_prompt_tokens"]), float(proj[cid]["llm_prompt_tokens_mean"])
        rows.append({"variant": cid, "requests": int(r["llm_calls"]), "real_prompt": rp, "projected_prompt": pp,
                     "rel_err": (pp - rp) / rp, "real_total": rp + float(r["llm_completion_tokens"])})
    fit = fit_billing([{"requests": r["llm_calls"], "visible": r.get("llm_visible_prompt_tokens", 0),
                        "billed": r["llm_prompt_tokens"]} for r in real.values()])

    def saving(a: str, b: str) -> float | None:
        ra = next((x for x in rows if x["variant"] == a), None)
        rb = next((x for x in rows if x["variant"] == b), None)
        return 1 - ra["real_total"] / rb["real_total"] if ra and rb else None

    return {
        "header": {k: v for k, v in header.items() if k != "base_url"},
        "rows": rows,
        "max_abs_rel_err": max(abs(r["rel_err"]) for r in rows) if rows else None,
        "real_fit": fit,
        "real_savings": {
            "FULL_vs_T_memory_pair": saving("FULL", "T_memory_pair"),
            "FULL_coalesce_vs_T_memory_pair": saving("FULL_coalesce", "T_memory_pair"),
            "FULL_group_vs_T_memory_pair": saving("FULL_group", "T_memory_pair"),
            "SS_group_vs_T": saving("SS_group", "T"),
            "SS_coalesce_vs_T": saving("SS_coalesce", "T"),
        },
    }


def render_projection_validation(v: dict[str, Any]) -> str:
    lines = [
        "## Prospective validation: OS-SOTA1 projection vs real provider bill",
        "",
        f"> model={v['header'].get('llm_model')}; projection generated before the real run; "
        f"max |error| on billed prompt over {len(v['rows'])} variants = **{v['max_abs_rel_err']:.1%}**",
        "",
        "| Variant | Requests | Real prompt | Projected prompt | Error |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in v["rows"]:
        lines.append(f"| {r['variant']} | {r['requests']} | {r['real_prompt']:,.0f} | {r['projected_prompt']:,.0f} | {r['rel_err']:+.1%} |")
    f = v.get("real_fit")
    if f:
        lines += ["", f"- Real-run fit (provider tokenizer-matched visible counts): prompt = **{f['overhead_per_request']:.0f}**·requests "
                  f"+ {f['visible_scale']:.3f}·visible (R² = {f['r2']:.4f}): the gateway adds a fixed hidden prompt to every request."]
    sv = v["real_savings"]
    lines.append("- Real total-token savings: " + ", ".join(f"{k} **{x:.1%}**" for k, x in sv.items() if x is not None) + ".")
    return "\n".join(lines + [""])


def run_billing_model_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _write_checksum_manifest

    p = archive_report(getattr(args, "archive", None) or ARCHIVE_CSV)
    p["projection_validation"] = projection_validation()
    out = _allocate_result_dir("results", "billing_model")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    text = render_archive_report(p)
    if p["projection_validation"]:
        text += "\n" + render_projection_validation(p["projection_validation"])
    (out / "report.md").write_text(text, encoding="utf-8")
    _write_checksum_manifest(out)
    print(text)
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = [
    "archive_report",
    "billing_decomposition",
    "fit_billing",
    "probe_provider_overhead",
    "projection_validation",
    "render_archive_report",
    "run_billing_model_cli",
]
