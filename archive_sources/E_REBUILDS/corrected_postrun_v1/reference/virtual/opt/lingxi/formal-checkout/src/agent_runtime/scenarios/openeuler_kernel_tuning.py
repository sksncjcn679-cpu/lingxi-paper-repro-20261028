"""Three-step openEuler kernel-tuning chain and an adversarial negative-transfer pair."""

from __future__ import annotations

from agent_runtime.runtime.task import Task, VerdictResult


def _verifier(expected: str):
    def verify(outputs: dict) -> VerdictResult:
        actual = outputs.get("answer")
        return VerdictResult(
            success=actual == expected,
            quality=1.0 if actual == expected else 0.0,
            detail={"answer": actual, "expected": expected},
        )

    return verify


def _kernel_task(
    task_id: str,
    *,
    parent_task_id: str | None,
    facts: dict,
    expected: str,
    include_strategy: bool,
) -> Task:
    facts = dict(facts)
    if include_strategy:
        facts["strategy"] = "tune_kernel_param_v1"
    return Task(
        task_id=task_id,
        topic="openeuler-kernel-tuning",
        description="Diagnose and tune a sysctl parameter from an openEuler kernel snapshot.",
        payload={
            "question": "tune_kernel_param",
            "operands": ["sysctl_snapshot", "service_observation", "strategy"],
            "defect": "kernel_parameter_tuning",
            "facts": facts,
        },
        expected={"answer": expected},
        verifier=_verifier(expected),
        parent_task_id=parent_task_id,
        tags=["openeuler", "kernel", "sysctl", "tuning"],
    )


def make_k1(*, include_strategy: bool = True) -> Task:
    return _kernel_task(
        "kernel-k1",
        parent_task_id=None,
        facts={
            "sysctl_snapshot": {"vm.swappiness": 90, "vm.dirty_ratio": 20},
            "service_observation": "memory pressure and swap activity are elevated",
        },
        expected="swappiness_tuned",
        include_strategy=include_strategy,
    )


def make_k2(parent_task_id: str | None = "kernel-k1", *, include_strategy: bool = False) -> Task:
    return _kernel_task(
        "kernel-k2",
        parent_task_id=parent_task_id,
        facts={
            "sysctl_snapshot": {"net.core.somaxconn": 64, "net.ipv4.ip_local_port_range": "32768 60999"},
            "service_observation": "listen backlog overflows during burst traffic",
        },
        expected="somaxconn_tuned",
        include_strategy=include_strategy,
    )


def make_k3(parent_task_id: str | None = "kernel-k2", *, include_strategy: bool = False) -> Task:
    return _kernel_task(
        "kernel-k3",
        parent_task_id=parent_task_id,
        facts={
            "sysctl_snapshot": {"vm.swappiness": 80, "net.core.somaxconn": 128},
            "service_observation": "both memory pressure and backlog pressure are present",
        },
        expected="kernel_profile_tuned",
        include_strategy=include_strategy,
    )


def make_kernel_group(*, self_contained: bool = False) -> list[Task]:
    return [
        make_k1(include_strategy=True),
        make_k2(include_strategy=self_contained),
        make_k3(include_strategy=self_contained),
    ]


def _adversarial_task(
    task_id: str,
    *,
    parent_task_id: str | None,
    defect: str,
    strategy: str,
    expected: str,
    include_strategy: bool,
) -> Task:
    facts = {
        "abi_observation": "ABI marker present",
        "native_dependency": "glibc symbol version mismatch" if defect == "glibc_symbol" else "cp311 extension",
    }
    if include_strategy:
        facts["strategy"] = strategy
    return Task(
        task_id=task_id,
        topic="openeuler-abi-compatibility",
        description="Assess an ABI-looking openEuler migration issue without confusing ABI classes.",
        payload={
            "question": "assess_migration",
            "operands": ["abi_observation", "native_dependency", "strategy"],
            "defect": defect,
            "facts": facts,
        },
        expected={"answer": expected},
        verifier=_verifier(expected),
        parent_task_id=parent_task_id,
        tags=["openeuler", "abi", defect],
    )


def make_x1(*, include_strategy: bool = True) -> Task:
    return _adversarial_task(
        "abi-x1", parent_task_id=None, defect="python_abi",
        strategy="assess_abi_v1", expected="abi_compatible", include_strategy=include_strategy,
    )


def make_x2(parent_task_id: str | None = "abi-x1", *, include_strategy: bool = True) -> Task:
    return _adversarial_task(
        "abi-x2", parent_task_id=parent_task_id, defect="glibc_symbol",
        strategy="assess_glibc_symbol_v1", expected="symbol_review", include_strategy=include_strategy,
    )


def make_adversarial_group(*, self_contained: bool = False) -> list[Task]:
    return [make_x1(include_strategy=True), make_x2(include_strategy=True)]


__all__ = ["make_kernel_group", "make_k1", "make_k2", "make_k3", "make_adversarial_group", "make_x1", "make_x2"]
