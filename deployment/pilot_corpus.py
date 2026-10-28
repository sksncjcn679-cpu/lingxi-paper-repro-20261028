"""Twenty-four newly authored synthetic pilot candidates, not the formal paper corpus."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

# domain, diagnosis, current observations, independent expected read-only evidence.
CASES = [
 ("service", "port_conflict", "After a rolling deployment, gateway cannot listen on 8443: EADDRINUSE. The disk warning in yesterday's journal predates this rollout.", "ss: an orphaned gateway-v1 process still listens on 0.0.0.0:8443"),
 ("service", "permission_denied", "The port conflict was cleared. The gateway now binds, but its unprivileged cache worker gets EACCES opening /srv/gateway/cache/index. No audit AVC exists.", "stat: /srv/gateway/cache is root:root 0700; the cache worker runs as gateway uid 991"),
 ("service", "selinux_denied", "Cache ownership was repaired. A new gateway listener on 9443 still fails; audit reports AVC denied { name_bind }. The port is free and Unix ownership is correct.", "audit: gateway_t denied name_bind to unreserved_port_t on tcp/9443"),
 ("service", "oom_killed", "The listener is healthy after relabeling. During the next traffic burst the worker disappears: kernel Out of memory: Killed process gateway-worker. An old DNS failure is no longer present.", "memory.events: gateway service oom_kill increased from 0 to 1 at the incident timestamp"),
 ("service", "disk_full", "Worker memory limits were fixed. Request logging now fails with ENOSPC, while the service remains reachable and memory.events is unchanged.", "df: /srv/gateway/log filesystem has 0 available blocks and is 100% used"),
 ("service", "dns_failure", "Logging space was restored. Gateway upstream fetches now return EAI_AGAIN for payments.internal, although routing by IP succeeds. Earlier SELinux denials are resolved.", "resolver: payments.internal returns SERVFAIL from the configured local nameserver"),
 ("migration", "compatible", "First migration checkpoint: target openEuler x86_64 CPython 3.11. A wheel declares cp311-cp311-manylinux x86_64 and every listed native library resolves.", "ABI probe: cp311 tag, x86_64 ELF class, and DT_NEEDED dependencies all match checkpoint A"),
 ("migration", "rebuild_required", "The Python migration advances to 3.12. The extension selected from the prior build is tagged cp311 and import fails with an interpreter ABI mismatch. Architecture is still x86_64.", "ABI probe: extension cp311 ABI differs from target cp312; build checkpoint B needs recompilation"),
 ("migration", "rebuild_required", "The interpreter was corrected to cp312, but the next plugin ELF is AArch64 on an x86_64 target. Disk space and Unix permissions are healthy.", "ELF probe: plugin e_machine=AArch64 while the checkpoint C host is x86_64"),
 ("migration", "rebuild_required", "The architecture build was corrected. The next module requires libssl.so.1.1, while the target has only libssl.so.3; the required library cannot be resolved.", "loader probe: DT_NEEDED libssl.so.1.1 is missing at checkpoint D despite libssl.so.3 being present"),
 ("migration", "compatible", "The SSL-dependent package was rebuilt. At checkpoint E, target and module are cp312 x86_64; libssl.so.3 and all other DT_NEEDED entries resolve and the read-only import probe succeeds.", "ABI probe: checkpoint E import succeeds with matching cp312 ABI and resolved libssl.so.3"),
 ("migration", "rebuild_required", "A later C++ plugin needs symbol GLIBC_2.38, but this target exports only up to GLIBC_2.34. Its cp312 tag and x86_64 architecture match, and a previous import success concerns another plugin.", "symbol probe: checkpoint F requires GLIBC_2.38 absent from the target GLIBC_2.34 symbol set"),
 ("resources", "disk_full", "Batch output creates new files and fails with ENOSPC. df -h shows 40% blocks used, but inode use is exhausted. There is no OOM-kill event.", "df -i: batch filesystem inode IFree=0, IUse%=100 despite free data blocks"),
 ("resources", "oom_killed", "After inode cleanup, the batch reader is killed. Kernel says Out of memory: Killed process batch-reader; its cgroup limit is 512 MiB and oom_kill has incremented. Free disk space remains high.", "memory probe: batch-reader cgroup memory.max=536870912 and oom_kill changed from 2 to 3"),
 ("resources", "disk_full", "The reader limit was raised. The next stage cannot write /tmp/batch with ENOSPC. Host volume F still has capacity, but the container's writable tmp filesystem is full.", "df: /tmp/batch tmpfs used 256 MiB of 256 MiB with 0 bytes available"),
 ("resources", "oom_killed", "Temporary files were removed. The join stage disappears after a host memory-pressure event. The kernel explicitly reports Out of memory: Killed process join-stage; the old tmpfs error is resolved.", "memory probe: kernel OOM victim pid 4821 is join-stage and MemAvailable fell below the configured incident threshold"),
 ("resources", "disk_full", "The join stage was rescheduled. Checkpoint writer receives ENOSPC because its project storage quota is exhausted, despite 18 GiB globally available. No new kernel kill exists.", "quota probe: project checkpoint quota used=hard_limit=4 GiB; writes have no project allocation budget"),
 ("resources", "oom_killed", "Quota was expanded. The final compressor exits unexpectedly; kernel Out of memory: Killed process compressor is current. EADDRINUSE belongs to a retired listener several hours earlier.", "memory probe: compressor cgroup oom_kill changed from 0 to 2 during final compression"),
 ("network_permissions", "dns_failure", "The agent can reach its router IP, but artifact.repo.internal lookup returns EAI_AGAIN and the configured DNS server replies SERVFAIL. Local Unix permissions are valid.", "resolver probe: artifact.repo.internal SERVFAIL; direct-IP route to the repository remains reachable"),
 ("network_permissions", "permission_denied", "DNS was repaired. The service now gets EACCES reading /etc/agent/client.key; no SELinux AVC is recorded and the containing directory is traversable.", "stat: client.key is root:root 0600 while the service runs as agent uid 992"),
 ("network_permissions", "selinux_denied", "Key ownership was repaired. A TLS connection still fails with audit AVC denied { name_connect }; DNS resolves, the route works, and Unix file permissions allow key access.", "audit probe: agent_t denied name_connect to remote_service_port_t on tcp/7443"),
 ("network_permissions", "dns_failure", "Network labeling was fixed. A newly introduced hostname mirror-alt.internal returns EAI_AGAIN. The previous repository name resolves; the new name's DNS reply is NXDOMAIN.", "resolver probe: mirror-alt.internal is NXDOMAIN while artifact.repo.internal resolves successfully"),
 ("network_permissions", "permission_denied", "The mirror record was added. A local relay now fails opening /run/agent/control.sock with EACCES. There is no AVC and DNS is healthy.", "stat: /run/agent is root:root 0700 and blocks traversal by relay uid 993"),
 ("network_permissions", "selinux_denied", "Socket traversal was repaired. Policy still blocks reading a labeled credential despite mode 0644 and valid ownership. A current audit AVC denied { read } names agent-secret_t.", "audit probe: relay_t denied read of agent-secret_t; Unix mode and traversal checks both pass"),
]


def corpus():
    from agent_runtime.agents.model_decision import LABEL_ACTIONS, PROCEDURE
    rows, previous = [], {}
    healthy = {
        "inspect_listener": "ss: configured port has no conflicting listener",
        "inspect_permissions": "stat: service ownership and Unix permissions are valid",
        "inspect_resolver": "resolver: configured hostname resolves successfully",
        "inspect_disk": "storage: free blocks, inodes, and project quota are available",
        "inspect_memory": "memory: no current OOM victim and limits have headroom",
        "inspect_audit": "audit: no current AVC denial for this service",
        "keep_binary": "ABI: interpreter, architecture, native dependencies and symbols match",
        "rebuild_binary": "ABI: no incompatible build target detected",
    }
    for index, (domain, label, symptom, probe) in enumerate(CASES):
        step = sum(r["domain"] == domain for r in rows)
        task_id = f"pilot-{domain}-{step+1:02d}"
        checks = dict(healthy)
        checks[LABEL_ACTIONS[label]] = probe
        observations = {"incident_id": task_id, "symptom": symptom, "checks": checks,
                        "journal_context": "\n".join(f"checkpoint {step+1}, heartbeat {j}: read-only status sample; worker has loaded its configuration" for j in range(6)),
                        "previous_checkpoint": previous.get(domain),
                        "chain_note": "Earlier checkpoints may supply the diagnostic procedure; this incident needs a new diagnosis and evidence."}
        row = {"task_id": task_id, "chain_id": "pilot-chain-" + domain, "domain": domain,
               "difficulty": ["basic", "basic", "ambiguous", "ambiguous", "interference", "interference"][step],
               "parent_ids": [previous[domain]] if domain in previous else [], "observations": observations,
               "procedure": PROCEDURE,
               "expected": {"diagnosis": label, "action": LABEL_ACTIONS[label], "probe": probe},
               "origin": "new synthetic pilot candidate authored for this deployment; no external private material",
               "review_status": "HUMAN_REVIEW_PENDING", "split": "pilot_candidate_only"}
        row["content_sha256"] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        rows.append(row)
        previous[domain] = task_id
    return rows


def make_task(row):
    from agent_runtime.runtime.task import Task, VerdictResult
    expected = row["expected"]

    def verify(outputs):
        actual = outputs.get("answer")
        ok = actual == expected
        return VerdictResult(success=ok, quality=float(ok), detail={"exact_contract_match": ok})

    return Task(task_id=row["task_id"], parent_task_id=(row["parent_ids"] or [None])[0],
                topic=row["chain_id"], description="Diagnose the current incident and select its read-only evidence check.",
                payload={"question": "llm_diagnose", "operands": ["observations", "procedure"],
                         "defect": "incident", "facts": {"observations": row["observations"], "procedure": row["procedure"]}},
                expected={"answer": expected}, verifier=verify, tags=[row["domain"], "pilot"])


if __name__ == "__main__":
    rows = corpus()
    out = Path("/evidence/corpus/tasks.jsonl")
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    assert len(rows) == len({r["task_id"] for r in rows}) == len({r["content_sha256"] for r in rows}) == 24
    print(f"Saved {len(rows)} synthetic pilot candidates")
