# Prepared report-consumer interface

Producer: new `postrun_analysis/full_metric_audit.py`; no actual formal result
was read and this producer has not run. The following is the implemented static
output contract, subject to root review/version binding and dynamic validation.

Output: `analysis/full_metric_audit_vN/audited_observations.jsonl`.

Each line has `schema_version: audited-observations-projection-v1` and one
observed terminal run ID. Original terminal values are copied only when their
field was actually present. Explicit nulls remain null, zero remains a recorded
zero, and absent fields remain absent and are named in `absent_terminal_fields`.
No timing, usage, memory, worker, topology or failed outcome is reconstructed to
fill a missing field. This is a raw observation projection, not a statistics
result or an independent PASS authority.

Implemented field groups:

- Identity: study/phase/task/chain/domain/difficulty/arm/repeat/model/model_id,
  intervention, seed, unit_id/run_id, processes, topology, actual_topology when
  recorded, source/task/effective-contract hashes and model_evidence.
- Outcomes: original status/success/quality and UTC start/end.
- Timing: wall_sec, monotonic wall boundaries, provider_wait_sec,
  exclusive_spans_sec, span_partition_error_sec, original nested metrics and
  runtime_cpu_sec. Figure4 can consume the recorded exclusive partition only;
  it must not add nested inclusive spans or infer unmeasured seed-write time.
- Memory: memory_count_before, memory_count_after_condition_seed,
  memory_count_after, memory_lineage, memory_adopted_id,
  memory_feedback_attribution and execution_provenance. Table5 can consume these
  recorded values, retaining absent/null distinctions and condition identity.
- Real usage: original provider_attempts, provider_usage_complete,
  provider_input_tokens/output_tokens/total_tokens/cached_input_tokens,
  provider_cached_input_tokens_complete when recorded and
  reported_token_lower_bound. Completeness flags alone do not turn no observed
  attempts into usage calibration; read the independent unit findings.
- State/IPC/resources: state_applicable/size_class, state_exchange_enabled,
  state_consumed/dimension, public state override, physical IPC accounting,
  local/logical state bytes, worker PID/CPU observations, remaining workers/SHM,
  sampled RSS/FD peaks, FD counts, and external scope/version/role-order fields
  when actually recorded.
- Audit/source links: raw_file, 1-based raw_line, raw_line_sha256 (hash of the
  complete original raw line **including its newline**), per-unit audit_failures
  and unobserved_items, and explicit source_semantics.
- Errors: error_type copies only an explicit terminal error dictionary's `type`;
  otherwise it is null rather than guessed from text. provider_error_types is
  copied from the recorded ERROR attempts, retaining an unknown type as null.

`unit_checks.jsonl` separately contains derived field/URI/index/factual join
checks and selected-action/check-observation coverage. It is not a source of
replacement timing or usage values.

The consumer must first bind the actual completed `metric_audit.json` and verify
`output_sha256['audited_observations.jsonl']`, analyzer/input identities and
global/per-unit findings. A report with retained UNKNOWN may support only the
specific observed claims described in `claim_scope`; it cannot be presented as
all115indicators passing. A global FAIL is not bypassed by a line whose local
failure count happens to be0. Public144, main, cross-model and external phases
remain separately selected for each report claim.

Primary estimates/CI/Holm must be loaded only from the saved, sole-authority
`statistical_gates.py formal` output. This projection intentionally supplies no
second estimates, CI, Holm adjustment, imputed medians, or old-study outcomes.
