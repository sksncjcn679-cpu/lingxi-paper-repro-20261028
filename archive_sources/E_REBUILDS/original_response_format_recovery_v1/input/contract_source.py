"""Prospective candidate output contract. No runtime import or provider call."""
from __future__ import annotations

VERSION = "protocol-repair-candidate-contract-v1"
DOMAINS = ("service", "migration", "resources", "network_permissions")
# One fixed study-wide choice set, identical in every arm/framework. These are
# meaningful mechanism/check pairs, not identifiers appended to former answers.
MECHANISMS = {
    "service": (
        ("svc_listener_ownership_conflict", "inspect_socket_ownership", "listener ownership collision"),
        ("svc_unix_access_denied", "inspect_unix_access_path", "Unix discretionary access denial"),
        ("svc_mac_policy_denied", "inspect_selinux_decision", "current SELinux policy denial"),
        ("svc_storage_allocation_exhausted", "inspect_storage_allocation", "storage allocation exhaustion"),
        ("svc_resolver_failure", "inspect_service_name_resolution", "service name resolution failure"),
        ("svc_memory_oom_termination", "inspect_service_memory_events", "OOM termination of the service"),
    ),
    "migration": (
        ("mig_python_binary_interface_mismatch", "inspect_python_binary_tags", "Python extension ABI mismatch"),
        ("mig_elf_target_mismatch", "inspect_elf_target", "ELF machine or class mismatch"),
        ("mig_needed_soname_unavailable", "inspect_needed_objects", "required SONAME not installed"),
        ("mig_symbol_version_unavailable", "inspect_symbol_versions", "required versioned symbol not exported"),
        ("mig_program_interpreter_unavailable", "inspect_program_interpreter", "declared PT_INTERP not present"),
        ("mig_loader_search_path_excludes_provider", "inspect_loader_search_paths", "installed provider outside loader search path"),
    ),
    "resources": (
        ("res_block_capacity_exhausted", "inspect_block_accounting", "filesystem data-block exhaustion"),
        ("res_inode_capacity_exhausted", "inspect_inode_accounting", "filesystem inode exhaustion"),
        ("res_project_quota_exhausted", "inspect_project_quota", "project quota exhaustion"),
        ("res_memcg_limit_oom", "inspect_cgroup_memory_limit", "cgroup memory limit OOM"),
        ("res_cpu_bandwidth_throttling", "inspect_cpu_bandwidth", "CPU bandwidth quota throttling"),
        ("res_io_rate_throttling", "inspect_io_controller", "I/O controller rate throttling"),
    ),
    "network_permissions": (
        ("net_resolution_failure", "inspect_dns_transaction", "DNS transaction failure"),
        ("net_route_selection_mismatch", "inspect_routing_decision", "route selection mismatch"),
        ("net_packet_filter_denial", "inspect_filter_trace", "packet filter denial"),
        ("net_tls_peer_verification_failure", "inspect_tls_peer_chain", "TLS peer verification failure"),
        ("net_unix_credential_access_denied", "inspect_credential_access", "Unix credential access denial"),
        ("net_selinux_network_policy_denied", "inspect_network_avc", "SELinux denial on network-facing service"),
    ),
}
LABEL_ACTIONS = {label: action for group in MECHANISMS.values() for label, action, _ in group}
ACTION_LABELS = {action: label for label, action in LABEL_ACTIONS.items()}
assert len(LABEL_ACTIONS) == len(ACTION_LABELS) == 24

PROCEDURES = {
    "service": "Join service PID, namespace and current time across socket, path access, audit, allocation, resolver and memory records. Check current positive evidence and the supplied healthy controls. Archived warnings do not establish a current cause.",
    "migration": "Follow the artifact dependency chain: compare interpreter ABI and ELF target, enumerate installed DT_NEEDED names, compare required symbol versions, inspect PT_INTERP and then effective search directories. A provider existing on disk does not prove loader reachability.",
    "resources": "Align the workload cgroup and measurement interval. Separate free blocks, free inodes and project quota; distinguish cgroup OOM from CPU bandwidth throttling and I/O controller enforcement. Use deltas and limit values rather than isolated utilization percentages.",
    "network_permissions": "Align the endpoint and current flow. Resolve the name, compute the longest-prefix route, inspect the exact packet-filter trace, verify peer identity/trust and distinguish Unix credential checks from matching SELinux audit records. Exclude unrelated archived traffic.",
}
CAPABILITIES = {
    "service": ("service_identity_join", "current_vs_archived_evidence", "cross_subsystem_negative_controls"),
    "migration": ("artifact_target_join", "ordered_loader_constraints", "installed_vs_reachable_provider"),
    "resources": ("interval_and_cgroup_join", "capacity_vs_controller_constraints", "counter_delta_interpretation"),
    "network_permissions": ("flow_identity_join", "layered_connectivity_checks", "dac_vs_mac_separation"),
}
UNAVAILABLE_CAPABILITY = {
    "service": "remote_packet_capture_replay_procedure",
    "migration": "signed_debug_symbol_lineage_procedure",
    "resources": "device_firmware_signature_verification_procedure",
    "network_permissions": "remote_transparency_receipt_verification_procedure",
}


def document() -> dict:
    return {"version": VERSION, "status": "CANDIDATE_NOT_FROZEN_NOT_EXECUTED",
            "json_keys_exact": ["diagnosis", "action"], "label_actions": LABEL_ACTIONS,
            "mechanisms": {domain: [{"diagnosis": label, "action": action, "meaning": meaning}
                                      for label, action, meaning in group]
                           for domain, group in MECHANISMS.items()},
            "action_semantics": "read-only deterministic checks of authored fixture records; no live-host mutation",
            "effective_behavior_change": "Versioned experiment output-contract configuration replaces the former eight-pair vocabulary. Business source bytes are not edited; unchanged source SHA alone does not imply an unchanged effective contract.",
            "candidate_only": True, "paid_calls_authorized_by_this_file": False}
