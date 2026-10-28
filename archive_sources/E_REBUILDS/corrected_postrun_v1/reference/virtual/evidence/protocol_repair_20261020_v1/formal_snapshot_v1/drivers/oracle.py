"""Independent fixture oracle. Never imports the generator or reads expected.

All predicates operate on supplied current measurements. Multiple causes and no
supported cause are rejected, rather than repaired into the author's answer.
"""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
from contract import MECHANISMS, ACTION_LABELS


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unix_path_allowed(access):
    uid, gids = access["caller_uid"], set(access["caller_gids"])
    if uid == 0:
        raise ValueError("candidate DAC predicate deliberately excludes root capability bypass")
    for entry in access["path_entries"]:
        requested = entry["required_bits"]
        mode = int(entry["mode_octal"], 8)
        if uid == entry["owner_uid"]:
            bits = (mode >> 6) & 7
        elif str(uid) in entry.get("named_user_acl", {}):
            bits = entry["named_user_acl"][str(uid)] & entry["acl_mask"]
        elif entry["owner_gid"] in gids:
            bits = ((mode >> 3) & 7) & entry.get("acl_mask", 7)
        else:
            bits = mode & 7
        if bits & requested != requested:
            return False
    return True


def current_avc(audit, pid):
    return any(e["pid"] == pid and e["boot_id"] == audit["boot_id"]
               and e["namespace"] == audit["namespace"]
               and e["resource"] == audit["target_resource"]
               and e["operation"] == audit["operation"]
               and audit["window_start"] <= e["monotonic_sec"] <= audit["window_end"]
               and audit["enforcing"] and not e["permissive"]
               and e["result"] == "denied" for e in audit["records"])


def classify_facts(domain, observations):
    f = observations["measurements"]
    if domain == "service":
        s, a, m, d, r, o = (f[k] for k in ("socket", "unix_access", "audit", "allocation", "resolver", "memory"))
        socket_bad = (s["bind_errno"] == "EADDRINUSE" and s["desired_endpoint"] == s["owner_endpoint"]
                      and s["namespace"] == s["owner_namespace"] and s["owner_alive"]
                      and s["owner_pid"] != s["service_pid"])
        allocation_bad = d["write_errno"] in ("ENOSPC", "EDQUOT") and (
            d["available_blocks"] == 0 or d["free_inodes"] == 0 or d["quota_used_bytes"] >= d["quota_hard_bytes"])
        oom_bad = (o["exit_signal"] == 9 and o["oom_kill_after"] > o["oom_kill_before"]
                   and o["kernel_victim_pid"] == o["service_pid"] and o["victim_in_current_window"])
        flags = [socket_bad, a["open_errno"] == "EACCES" and not unix_path_allowed(a),
                 current_avc(m, s["service_pid"]), allocation_bad,
                 r["direct_ip_probe"] == "success" and (r["rcode"] != "NOERROR" or not r["answers"]), oom_bad]
    elif domain == "migration":
        p, e, n, v, i, s = (f[k] for k in ("python", "elf", "needed", "versions", "interpreter", "search"))
        flags = [p["artifact_abi"] != p["target_abi"],
                 e["artifact_machine"] != e["target_machine"] or e["artifact_class"] != e["target_class"],
                 bool(set(n["required_sonames"]) - set(n["installed_sonames"])),
                 bool(set(v["required_versions"]) - set(v["exported_versions"])),
                 i["pt_interp"] not in i["installed_paths"],
                 s["provider_soname"] == s["requested_soname"]
                 and s["provider_directory"] not in s["effective_search_directories"]]
    elif domain == "resources":
        b, i, q, m, c, o = (f[k] for k in ("blocks", "inodes", "quota", "memcg", "cpu", "io"))
        memory_bad = (m["oom_kill_after"] > m["oom_kill_before"] and m["killed_pid"] == m["workload_pid"]
                      and m["memory_peak_bytes"] >= m["effective_memory_max_bytes"] and m["same_interval"])
        cpu_bad = (c["runnable_threads"] >= c["allocated_cpus"] and c["nr_throttled_delta"] > 0
                   and c["throttled_usec_delta"] > 0 and c["quota_usec"] < c["period_usec"] * c["allocated_cpus"])
        io_bad = (o["pending_requests"] > 0 and o["device_utilization_percent"] < 70
                  and ((o["enforced_bps"] is not None and o["achieved_bps"] == o["enforced_bps"])
                       or (o["enforced_iops"] is not None and o["achieved_iops"] == o["enforced_iops"])))
        flags = [b["available_blocks"] == 0 and b["write_errno"] == "ENOSPC",
                 i["free_inodes"] == 0 and i["create_errno"] == "ENOSPC",
                 q["used_bytes"] >= q["hard_bytes"] and q["write_errno"] == "EDQUOT",
                 memory_bad, cpu_bad, io_bad]
    elif domain == "network_permissions":
        d, r, p, t, a, m = (f[k] for k in ("dns", "route", "filter", "tls", "credential", "audit"))
        dest = ipaddress.ip_address(r["destination"])
        routes = [item for item in r["entries"] if dest in ipaddress.ip_network(item["prefix"])]
        if not routes:
            route_bad = True
        else:
            chosen = max(routes, key=lambda item: (ipaddress.ip_network(item["prefix"]).prefixlen, -item["metric"]))
            route_bad = chosen["interface"] not in r["permitted_egress_interfaces"] or chosen["type"] != "unicast"
        flags = [d["direct_ip_probe"] == "success" and (d["rcode"] != "NOERROR" or not d["answers"]),
                 route_bad, p["verdict"] in ("drop", "reject") and p["trace_five_tuple"] == p["requested_five_tuple"],
                 t["verify_return_code"] != 0 and t["tcp_connected"] and t["verified_peer_endpoint"] == t["requested_endpoint"],
                 a["open_errno"] == "EACCES" and not unix_path_allowed(a), current_avc(m, observations["subject_pid"])]
    else:
        raise ValueError("unknown domain")
    matches = [item[0] for item, bad in zip(MECHANISMS[domain], flags) if bad]
    if len(matches) != 1:
        raise ValueError(f"current measurements require exactly one supported cause; found {len(matches)}")
    return {"diagnosis": matches[0], "action": dict((label, action) for label, action, _ in MECHANISMS[domain])[matches[0]]}


def check(row, action):
    if action not in ACTION_LABELS:
        raise ValueError("unsupported read-only check")
    observed = classify_facts(row["domain"], row["observations"])
    evidence = row["observations"]["measurements"]
    return {"task_id": row["task_id"], "action": action,
            "current_record_matches_check": observed["action"] == action,
            "evidence_sha256": hashlib.sha256(canonical(evidence).encode()).hexdigest(),
            "read_only": True, "scope": "authored fixture records, not live-host execution",
            "oracle_version": "compound-measurement-predicates-v1"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--action", required=True)
    args = parser.parse_args()
    row = next(json.loads(line) for line in args.tasks.read_text(encoding="utf-8").splitlines()
               if json.loads(line)["task_id"] == args.task_id)
    print(canonical(check(row, args.action)))
