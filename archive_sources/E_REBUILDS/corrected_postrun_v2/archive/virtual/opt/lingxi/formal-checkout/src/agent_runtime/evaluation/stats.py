"""统计聚合：均值 / 标准差 / P50 / P95。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def summarize(values: list[float]) -> dict[str, float]:
    """对一组数值给出统计摘要。"""
    if not values:
        return {"mean": 0.0, "std": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=0)),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "n": int(arr.size),
    }


def summarize_runs(runs: list[dict[str, Any]], keys: list[str]) -> dict[str, dict[str, float]]:
    """对多次运行的指标字典按 key 聚合统计。"""
    out: dict[str, dict[str, float]] = {}
    for key in keys:
        out[key] = summarize([float(r.get(key, 0) or 0) for r in runs])
    return out


def paired_effect_size(control: list[float], treatment: list[float]) -> dict[str, float]:
    """Return paired mean difference and standardized effect size."""
    if len(control) != len(treatment) or not control:
        return {"n": 0, "mean_difference": 0.0, "std_difference": 0.0, "cohens_dz": 0.0}
    diff = np.asarray(treatment, dtype=float) - np.asarray(control, dtype=float)
    mean = float(diff.mean())
    std = float(diff.std(ddof=1)) if diff.size > 1 else 0.0
    return {"n": int(diff.size), "mean_difference": mean, "std_difference": std, "cohens_dz": mean / std if std else 0.0}


def paired_ci95(control: list[float], treatment: list[float]) -> dict[str, float]:
    """Student-t 95% CI for paired treatment-control differences."""
    effect = paired_effect_size(control, treatment)
    n = int(effect["n"])
    if n < 2:
        return {"low": effect["mean_difference"], "high": effect["mean_difference"]}
    critical = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}.get(n - 1, 1.96)
    half = critical * effect["std_difference"] / (n ** 0.5)
    return {"low": effect["mean_difference"] - half, "high": effect["mean_difference"] + half}


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def paired_analysis(control: list[float], treatment: list[float]) -> dict[str, Any]:
    """Summarize a paired comparison without adding a SciPy dependency.

    The interval uses the existing Student-t critical values. The p-value and power
    are explicitly normal approximations and are reported as supporting diagnostics.
    """
    effect = paired_effect_size(control, treatment)
    ci = paired_ci95(control, treatment)
    n = int(effect["n"])
    d = abs(float(effect["cohens_dz"]))
    noncentrality = d * math.sqrt(n) if n else 0.0
    p_value = min(1.0, 2.0 * (1.0 - _normal_cdf(noncentrality))) if n else 1.0
    zcrit = 1.959963984540054
    power = (
        _normal_cdf(-zcrit - noncentrality)
        + 1.0 - _normal_cdf(zcrit - noncentrality)
        if n
        else 0.0
    )
    return {
        **effect,
        "ci95": ci,
        "p_value_normal_approx": p_value,
        "power_normal_approx": power,
        "inference_note": "CI uses Student-t critical values; p-value/power use normal approximation",
    }


def holm_bonferroni(p_values: dict[str, float]) -> dict[str, float]:
    """Return Holm step-down adjusted p-values for named comparisons."""
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        candidate = min(1.0, max(0.0, float(value)) * (total - rank))
        running = max(running, candidate)
        adjusted[name] = running
    return adjusted


__all__ = [
    "summarize", "summarize_runs", "paired_effect_size", "paired_ci95",
    "paired_analysis", "holm_bonferroni",
]
