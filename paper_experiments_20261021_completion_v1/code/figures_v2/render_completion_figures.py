"""Render figures 10--13 from the completed, bound saved-value projection.

No bootstrap, significance tests, raw model readers, calls or benchmarks.
This module is deliberately separate from preparation; do not run while main collects.
"""
from __future__ import annotations

import argparse
import importlib.util
import unicodedata
from pathlib import Path

from prepare_figure_data import (ARMS, PAIRS, CONDITIONS, BASE_RENDERER_SHA, FONT_SHA,
                                 require, sha, load, write_json, verify_projection)

TITLES = {10: "图10　两个独立批次的五项配对效应", 11: "图11　复现批次耗时与互斥分解",
          12: "图12　独立初始化补测的写入分布", 13: "图13　原压力数据的采后点态区间"}
NOTES = {
    10: "各批次独立2160运行；目标减基线；状态配对108任务，其余120任务，各20链×3次。\n"
        "token/IPC为相对百分比，质量为百分点；旧token未完成；质量为自动验证，不作不劣或加速承诺。",
    11: "仅新复现批次；互斥分区不重复相加；分位数不是CI。\n"
        "客户环境单调计时与客户端SDK等待；不等于纯推理、服务方排队或完整宿主用户耗时。",
    12: "独立720次初始化实测，每条件180次；模型/API调用0；不回填历史运行。\n"
        "全点＋四分位箱（须线为全范围）；对称对数轴；整体写入已含索引，三面板不可相加。",
    13: "历史独立离线压力批次；每格30完整原批，10000重抽，seed20261021。\n"
        "采后逐格百分位95%CI，非同步曲线带；原点不变；通信代理计数不是服务方token。",
}


def paired(base, plt, data, width):
    single = width < 4
    fig, axes = plt.subplots(4, 1, figsize=(width, 10.1)) if single else plt.subplots(2, 2, figsize=(width, 7.2))
    axes = list(axes.flat)
    metrics = (("provider_input_tokens", "输入token变化（%）", True),
               ("observable_ipc_bytes", "运行时IPC字节变化（%）", True),
               ("quality", "自动任务质量差（百分点）", False),
               ("wall_sec", "客户环境端到端耗时差（s）", False))
    batches = ((data["old_batch_id"], "原独立批次", "o", "#183153", -.075),
               (data["new_batch_id"], "新独立复现批次", "s", "#087f8c", .075))
    from matplotlib.lines import Line2D
    for ax, (metric, label, relative) in zip(axes, metrics):
        for batch, title, marker, color, offset in batches:
            selected = {(r["baseline"], r["target"]): r for r in data["paired"]
                        if r["batch_id"] == batch and r["metric"] == metric}
            for i, pair in enumerate(PAIRS):
                row = selected[pair]
                scale = 100 if relative or metric == "quality" else 1
                value_key = "relative_change" if relative else "paired_difference"
                low_key, high_key = (("relative_ci95_low", "relative_ci95_high") if relative else ("ci95_low", "ci95_high"))
                transform = lambda value: None if value is None else value * scale
                base.point(ax, i + offset, transform(row[value_key]), transform(row[low_key]), transform(row[high_key]),
                           color=color, marker=marker, missing_label="未完成" if row["status"] != "COMPLETE" else "相对CI未定义")
        ax.axhline(0, color=".45", lw=.6, ls="--")
        ax.set(xticks=range(5), xticklabels=[a + "→" + b for a, b in PAIRS], ylabel=label, xlim=(-.4, 4.4))
        ax.tick_params(axis="x", labelrotation=30 if single else 20)
        ax.grid(axis="y", alpha=.2)
    fig.legend([Line2D([], [], marker=b[2], color=b[3], linestyle="none") for b in batches],
               [b[1] for b in batches], loc="upper center", bbox_to_anchor=(.5, .957), ncol=2, frameon=True)
    fig._presentation_rect = (0, .105 if single else .12, 1, .93)
    return fig


def seed_distribution(base, plt, data, width):
    fig, axes = plt.subplots(3, 1, figsize=(width, 8.8 if width < 4 else 7.0))
    labels = ("相关", "无关", "过时", "错误")
    fields = (("write_inclusive_sec", "整体写入耗时（ms）"),
              ("memory_exclusive_sec", "写入互斥归属耗时（ms）"),
              ("index_exclusive_sec", "索引互斥归属耗时（ms）"))
    for ax, (field, ylabel) in zip(axes, fields):
        groups = [[r[field] * 1000 for r in data["seed"] if r["condition"] == c] for c in CONDITIONS]
        for index, group in enumerate(groups):
            # Deterministic position spread only, never a new stochastic resampling.
            positions = [index + ((i % 19) - 9) / 85 for i in range(len(group))]
            ax.scatter(positions, group, s=3, facecolors="none", edgecolors=".5", linewidths=.35)
        ax.boxplot(groups, positions=range(4), widths=.35, whis=(0, 100), showfliers=False,
                   medianprops=dict(color="#183153", linewidth=1.1),
                   boxprops=dict(color=".15"), whiskerprops=dict(color=".15"), capprops=dict(color=".15"))
        ax.set(xticks=range(4), xticklabels=labels, ylabel=ylabel)
        ax.set_yscale("symlog", linthresh=.001)
        ax.grid(axis="y", alpha=.2)
    fig._presentation_rect = (0, .12, 1, .95)
    return fig


def pressure(base, plt, data, width):
    fig, axes = plt.subplots(3, 1, figsize=(width, 8.7 if width < 4 else 7.0))
    settings = (("task_latency_p95_sec", "p95_ci95_low_sec", "p95_ci95_high_sec", "任务p95（s）"),
                ("native_observable_frame_ack_bytes_per_chain", "frame_ack_bytes_per_chain_ci95_low",
                 "frame_ack_bytes_per_chain_ci95_high", "帧与ACK字节/链"),
                ("message_proxy_tokens_per_chain", "message_proxy_tokens_per_chain_ci95_low",
                 "message_proxy_tokens_per_chain_ci95_high", "消息代理token/链"))
    concurrencies = sorted({r["concurrency"] for r in data["stress"]})
    markers = ("o", "s", "^", "D", "v")
    styles = ("-", "--", ":", "-.", (0, (3, 1, 1, 1)))
    for ax, (point_key, low, high, ylabel) in zip(axes, settings):
        for ci, concurrency in enumerate(concurrencies):
            rows = sorted((r for r in data["stress"] if r["concurrency"] == concurrency), key=lambda r: r["length"])
            color = base.GREYS[ci]
            ax.plot([r["length"] for r in rows], [r[point_key] for r in rows], linestyle=styles[ci], color=color,
                    linewidth=.8, label="并发" + str(int(concurrency)))
            for row in rows:
                base.point(ax, row["length"], row[point_key], row[low], row[high], color=color, marker=markers[ci])
        ax.set_xscale("log", base=2)
        ticks = sorted({r["length"] for r in data["stress"]})
        ax.set(xticks=ticks, xticklabels=[str(int(x)) for x in ticks], xlabel="链长L", ylabel=ylabel)
        ax.grid(axis="y", alpha=.2)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.5, .955), ncol=3 if width < 4 else 5, frameon=True)
    fig._presentation_rect = (0, .12, 1, .91)
    return fig


def wrapped_note(text, half_width_limit):
    lines = []
    for original in text.splitlines():
        line = ''; units = 0
        for char in original:
            char_units = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
            if line and units + char_units > half_width_limit:
                lines.append(line); line = ''; units = 0
            line += char; units += char_units
        if line: lines.append(line)
    return '\n'.join(lines)


def render(data_path, out):
    verify_projection(data_path)
    data = load(data_path)
    require(data["status"] == "PROJECTED_SAVED_VALUES_ONLY", "completed projection required")
    require(not out.resolve().is_relative_to(Path(data["old_delivery_root"]).resolve()), "old delivery is read-only")
    require(not out.exists(), "render destination must be fresh")
    out.mkdir(parents=True, exist_ok=False)
    base_path = Path(__file__).with_name("base_renderer_v8.py")
    require(sha(base_path) == BASE_RENDERER_SHA, "base renderer changed")
    spec = importlib.util.spec_from_file_location("approved_base_renderer_v8", base_path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    font = Path(data["bindings"]["font"]["path"])
    require(sha(font) == FONT_SHA, "approved Regular400 font differs")
    plt, family, version, font_version = base.configure(font, out)
    functions = {10: paired, 11: lambda b, p, d, w: b.timing(d["timing"], p, w, 7),
                 12: seed_distribution, 13: pressure}
    files = []
    for number, function in functions.items():
        for width_label, width in (("single", 3.3), ("double", 7.16)):
            fig = function(base, plt, data, width)
            fig.suptitle(TITLES[number], fontsize=10, y=.991)
            fig.text(.02, .016, wrapped_note(NOTES[number], 76 if width_label == "single" else 140),
                     fontsize=5.6 if width_label == "single" else 6.7, va="bottom")
            fig.tight_layout(rect=fig._presentation_rect)
            for extension in ("pdf", "svg", "png"):
                path = out / f"figure_{number:02}_zh_{width_label}.{extension}"
                fig.savefig(path, dpi=600)
                files.append(dict(path=path.name, figure=number, width=width_label, sha256=sha(path), bytes=path.stat().st_size))
            plt.close(fig)
    write_json(out/"render_receipt.json", dict(status="ACTUAL_RENDER_COMPLETED_QA_PENDING", files=files,
        data_sha256=sha(data_path), renderer_sha256=sha(__file__), base_renderer_sha256=sha(base_path),
        font_sha256=sha(font), font_family=family, font_version=font_version, matplotlib_version=version,
        dpi=600, model_calls=0, benchmark_calls=0, statistical_reanalysis=False,
        source_bindings=data["bindings"], scope=data["limits"], visual_QA="NOT_YET_PERFORMED"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(args.data, args.out)
