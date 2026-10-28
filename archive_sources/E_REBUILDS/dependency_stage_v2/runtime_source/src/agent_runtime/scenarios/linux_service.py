"""openEuler/Linux 服务故障诊断连续任务。

L1 给出通用诊断策略；L2 不提供策略，必须复用 L1 的 procedural memory。
输入使用 systemctl/ss 风格的结构化观测，但不依赖宿主机服务，保证比赛复现确定性。
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


def make_l1(*, include_strategy: bool = True) -> Task:
    facts = {
        "service_status": "Active: failed (Result: exit-code); bind: Address already in use",
        "port_probe": "LISTEN 0 128 0.0.0.0:8080 users:((legacyd,pid=941,fd=7))",
        "dependency_status": "redis.service active",
        "permission_status": "ok",
        "journal_excerpt": "\n".join([
            "Sep 20 10:31:04 api systemd[1]: Starting api.service",
            "Sep 20 10:31:04 api api[2120]: loading /etc/api/config.yaml",
            "Sep 20 10:31:04 api api[2120]: connecting to redis://127.0.0.1:6379",
            "Sep 20 10:31:04 api api[2120]: redis health check ok",
            "Sep 20 10:31:04 api api[2120]: binding 0.0.0.0:8080",
            "Sep 20 10:31:04 api api[2120]: OSError: [Errno 98] Address already in use",
            "Sep 20 10:31:04 api systemd[1]: api.service: Main process exited, status=1/FAILURE",
            "Sep 20 10:31:04 api systemd[1]: api.service: Failed with result 'exit-code'",
        ] * 3),
    }
    if include_strategy:
        facts["strategy"] = "diagnose_service_v1"
    return Task(
        topic="linux-service-diagnosis",
        description="Diagnose why api.service cannot start from systemctl/ss style evidence.",
        payload={
            "question": "diagnose_service",
            "operands": [
                "service_status", "port_probe", "dependency_status", "permission_status", "journal_excerpt", "strategy"
            ],
            "defect": "service_start_failure",
            "diagnosis_labels": ["port_conflict", "permission_denied"],
            "facts": facts,
        },
        expected={"answer": "port_conflict"},
        verifier=_verifier("port_conflict"),
        tags=["linux", "systemd", "diagnosis", "service_start_failure"],
    )


def make_l2(parent_task_id: str | None = None, *, include_strategy: bool = False) -> Task:
    facts = {
        "service_status": "Active: failed (Result: exit-code); open /var/lib/app/state: Permission denied",
        "port_probe": "port 8081 free",
        "dependency_status": "redis.service active",
        "permission_status": "denied:/var/lib/app/state",
        "journal_excerpt": "\n".join([
            "Sep 20 10:42:11 worker systemd[1]: Starting worker.service",
            "Sep 20 10:42:11 worker worker[2441]: loading state directory /var/lib/app/state",
            "Sep 20 10:42:11 worker worker[2441]: PermissionError: [Errno 13] Permission denied",
            "Sep 20 10:42:11 worker worker[2441]: configured listen port 8081",
            "Sep 20 10:42:11 worker worker[2441]: dependency redis health check ok",
            "Sep 20 10:42:11 worker systemd[1]: worker.service: Main process exited, status=1/FAILURE",
            "Sep 20 10:42:11 worker systemd[1]: worker.service: Failed with result 'exit-code'",
        ] * 3),
    }
    if include_strategy:
        facts["strategy"] = "diagnose_service_v1"
    return Task(
        topic="linux-service-diagnosis",
        description="Diagnose a related worker.service startup failure and reuse the prior diagnostic procedure.",
        payload={
            "question": "diagnose_service",
            "operands": [
                "service_status", "port_probe", "dependency_status", "permission_status", "journal_excerpt", "strategy"
            ],
            "defect": "service_start_failure",
            "diagnosis_labels": ["port_conflict", "permission_denied"],
            "facts": facts,
        },
        expected={"answer": "permission_denied"},
        verifier=_verifier("permission_denied"),
        tags=["linux", "systemd", "diagnosis", "service_start_failure"],
        parent_task_id=parent_task_id,
    )


def make_group(*, self_contained: bool = False) -> list[Task]:
    first = make_l1(include_strategy=True)
    second = make_l2(parent_task_id=first.task_id, include_strategy=self_contained)
    return [first, second]


__all__ = ["make_l1", "make_l2", "make_group"]
