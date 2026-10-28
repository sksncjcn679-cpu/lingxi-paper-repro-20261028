"""openEuler 应用迁移兼容性连续任务。

M1 给出迁移评估策略；M2 隐去策略，验证跨任务 procedural memory 复用。
"""

from __future__ import annotations

from agent_runtime.runtime.task import Task, VerdictResult


def _verifier(expected: str):
    def verify(outputs: dict) -> VerdictResult:
        actual = outputs.get("answer")
        ok = actual == expected
        return VerdictResult(
            success=ok,
            quality=1.0 if ok else 0.0,
            detail={"answer": actual, "expected": expected},
        )
    return verify


def make_m1(*, include_strategy: bool = True) -> Task:
    facts = {
        "target_arch": "x86_64",
        "python_abi": "cp311",
        "native_dependency": "glibc-compatible-x86_64",
        "service_manager": "systemd",
        "package_manifest": [
            {"name": f"py-lib-{i:02d}", "kind": "python", "abi": "py3", "status": "portable"}
            for i in range(24)
        ] + [
            {"name": "libcrypto", "kind": "native", "arch": "x86_64", "status": "compatible"},
            {"name": "service-unit", "kind": "systemd", "status": "native"},
        ],
    }
    if include_strategy:
        facts["strategy"] = "assess_openeuler_migration_v1"
    return Task(
        topic="openeuler-migration-assessment",
        description="Assess migration compatibility of a Python/systemd service for openEuler 24.03.",
        payload={
            "question": "assess_migration",
            "operands": [
                "target_arch", "python_abi", "native_dependency", "service_manager", "package_manifest", "strategy"
            ],
            "defect": "openeuler_migration",
            "diagnosis_labels": ["compatible", "rebuild_required"],
            "facts": facts,
        },
        expected={"answer": "compatible"},
        verifier=_verifier("compatible"),
        tags=["openeuler", "migration", "compatibility"],
    )


def make_m2(parent_task_id: str | None = None, *, include_strategy: bool = False) -> Task:
    facts = {
        "target_arch": "x86_64",
        "python_abi": "cp311",
        "native_dependency": "i686-only-native-extension",
        "service_manager": "systemd",
        "package_manifest": [
            {"name": f"py-lib-{i:02d}", "kind": "python", "abi": "py3", "status": "portable"}
            for i in range(24)
        ] + [
            {"name": "legacy-fastmath", "kind": "native", "arch": "i686", "status": "rebuild"},
            {"name": "service-unit", "kind": "systemd", "status": "native"},
        ],
    }
    if include_strategy:
        facts["strategy"] = "assess_openeuler_migration_v1"
    return Task(
        topic="openeuler-migration-assessment",
        description="Assess a related native-extension workload and reuse the previous migration procedure.",
        payload={
            "question": "assess_migration",
            "operands": [
                "target_arch", "python_abi", "native_dependency", "service_manager", "package_manifest", "strategy"
            ],
            "defect": "openeuler_migration",
            "diagnosis_labels": ["compatible", "rebuild_required"],
            "facts": facts,
        },
        expected={"answer": "rebuild_required"},
        verifier=_verifier("rebuild_required"),
        tags=["openeuler", "migration", "compatibility"],
        parent_task_id=parent_task_id,
    )


def make_group(*, self_contained: bool = False) -> list[Task]:
    first = make_m1(include_strategy=True)
    second = make_m2(parent_task_id=first.task_id, include_strategy=self_contained)
    return [first, second]


__all__ = ["make_m1", "make_m2", "make_group"]
