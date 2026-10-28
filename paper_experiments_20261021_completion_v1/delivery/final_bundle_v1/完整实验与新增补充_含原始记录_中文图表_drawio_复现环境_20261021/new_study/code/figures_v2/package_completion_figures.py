"""Package retained old figures and new figures after actual two-width visual QA.

This does not publish/upload, render, run statistics or modify any old delivery.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import zipfile

from prepare_figure_data import require, sha, load, write_json, verify_projection


def safe_child(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()) and path != root.resolve(), "unsafe manifest member")
    return path


def copy_bound(source, destination, expected, copied):
    require(sha(source) == expected, "source SHA differs: " + str(source))
    require(not destination.exists(), "duplicate package path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha(destination) == expected, "copied bytes differ")
    copied.append(dict(path=str(destination), sha256=expected, bytes=destination.stat().st_size))


def package(data_path, render_root, qa_single, qa_double, stage, zip_path):
    verify_projection(data_path)
    data = load(data_path)
    require(data["status"] == "PROJECTED_SAVED_VALUES_ONLY", "completed projection required")
    render_receipt = render_root / "render_receipt.json"
    render = load(render_receipt)
    require(render["status"] == "ACTUAL_RENDER_COMPLETED_QA_PENDING"
            and render["data_sha256"] == sha(data_path) and render["dpi"] == 600, "actual render mismatch")
    require(len(render["files"]) == 24, "expected four figures, two widths, three formats")
    require({(r["figure"], r["width"], Path(r["path"]).suffix) for r in render["files"]}
            == {(i, w, e) for i in range(10, 14) for w in ("single", "double") for e in (".pdf", ".svg", ".png")},
            "render coverage differs")
    qa = {}
    for width, path in (("single", qa_single), ("double", qa_double)):
        item = load(path)
        require(item["schema_version"] == "completion-figure-visual-qa-v1"
                and item["status"] in ("PASS_WITH_LIMITATIONS", "PASS_PRESENTATION_AND_SOURCE_BYTE_QA")
                and item["width"] == width and sorted(item["actual_new_views"]) == [10, 11, 12, 13]
                and item["render_receipt_sha256"] == sha(render_receipt)
                and not item["unresolved_P1_P2"], "actual visual QA does not support packaging: " + width)
        qa[width] = dict(path=str(path.resolve()), sha256=sha(path), status=item["status"])
    old_root = Path(data["old_delivery_root"]).resolve()
    old_manifest_path = Path(data["bindings"]["old_delivery_manifest"]["path"])
    require(sha(old_manifest_path) == data["bindings"]["old_delivery_manifest"]["sha256"], "old manifest changed")
    old_manifest = load(old_manifest_path)
    stage, zip_path = stage.resolve(), zip_path.resolve()
    require(not stage.exists() and not zip_path.exists(), "package destinations must be fresh")
    require(not stage.is_relative_to(old_root) and not zip_path.is_relative_to(old_root), "old delivery must remain read-only")
    require(not zip_path.is_relative_to(stage), "ZIP must be outside staging tree")
    copied = []
    stage.mkdir(parents=True, exist_ok=False)
    for record in old_manifest["files"]:
        source = safe_child(old_root, record["path"])
        destination = safe_child(stage / "original_delivery", record["path"])
        copy_bound(source, destination, record["sha256"], copied)
    copy_bound(old_manifest_path, stage/"original_delivery"/"manifest.json", sha(old_manifest_path), copied)
    for row in render["files"]:
        copy_bound(safe_child(render_root, row["path"]), stage/"completion_figures"/row["path"], row["sha256"], copied)
    for path in (render_receipt, qa_single, qa_double):
        prefix = "qa_" if path != render_receipt else ""
        name = ("single_" if path == qa_single else "double_" if path == qa_double else "") + path.name
        copy_bound(path, stage/"completion_evidence"/(prefix+name), sha(path), copied)
    for path in data_path.parent.iterdir():
        if path.is_file() and path.suffix in (".csv", ".json"):
            copy_bound(path, stage/"completion_data"/path.name, sha(path), copied)
    for path in Path(__file__).parent.iterdir():
        if path.is_file() and path.suffix in (".py", ".md"):
            copy_bound(path, stage/"completion_source"/path.name, sha(path), copied)
    # Bind small actual method/result receipts without bundling model raw or private prompts.
    for name in ("seed_result", "stress_point_identity", "stress_method"):
        binding = data["bindings"][name]
        copy_bound(Path(binding["path"]), stage/"completion_evidence"/(name+".json"), binding["sha256"], copied)
    routes = [{"figure": i, "batch_scope": "retained original; no numeric/visual modification",
               "directory": "original_delivery/figures"} for i in range(1, 10)]
    routes += [{"figure": i, "batch_scope": {10: "two independent main batches, not pooled",
                11: "new replication main only", 12: "new initialization-only measurements",
                13: "postcollection CI on original offline stress points"}[i],
                "directory": "completion_figures"} for i in range(10, 14)]
    write_json(stage/"figure_index.json", dict(routes=routes, limits=data["limits"],
        retained_original_manifest_sha256=sha(old_manifest_path), new_render_receipt_sha256=sha(render_receipt), visual_qa=qa,
        scientific_claims_not_automatically_upgraded=True, external_reference_scope="original NON_COMPARABLE retained"))
    readme = ("# 中文图表与绘图数据补充\n\n旧九图及原数据在original_delivery，逐文件SHA不变。"
              "新增图10–13及原始精度CSV在completion_figures/completion_data。\n\n"
              + "\n".join("- " + text for text in data["limits"]) + "\n\n"
              "新增图已按两个宽度实际QA；此包不证明模型结果必然有利、不提升未完成/不可比结论，亦未发布外部。\n")
    (stage/"README.md").write_text(readme, encoding="utf-8")
    members = {p.relative_to(stage).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size}
               for p in sorted(stage.rglob("*")) if p.is_file()}
    write_json(stage/"manifest.json", dict(status="ACTUAL_COMPLETION_FIGURES_AND_QA_BOUND", files=members, visual_qa=qa,
        old_assets_modified=False, source_bindings=data["bindings"]))
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage).as_posix())
    import hashlib
    decoded = []
    with zipfile.ZipFile(zip_path) as archive:
        require(archive.testzip() is None, "ZIP CRC failed")
        require(len(archive.namelist()) == len(set(archive.namelist())), "duplicate ZIP members")
        for info in archive.infolist():
            path = safe_child(stage, info.filename)
            digest = hashlib.sha256(archive.read(info)).hexdigest()
            require(digest == sha(path), "decoded ZIP member differs")
            decoded.append(dict(path=info.filename, sha256=digest, bytes=info.file_size))
    write_json(zip_path.with_suffix(".package_receipt.json"), dict(status="ACTUAL_ZIP_CREATED_AND_DECODED", zip_path=str(zip_path),
        zip_sha256=sha(zip_path), zip_bytes=zip_path.stat().st_size, members=decoded, visual_qa=qa,
        source_sha256=sha(__file__), old_assets_modified=False, model_calls=0, benchmark_calls=0, uploaded=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("data", "render-root", "qa-single", "qa-double", "stage", "zip"):
        parser.add_argument("--"+argument, type=Path, required=True)
    args = parser.parse_args()
    package(args.data, args.render_root, args.qa_single, args.qa_double, args.stage, args.zip)
