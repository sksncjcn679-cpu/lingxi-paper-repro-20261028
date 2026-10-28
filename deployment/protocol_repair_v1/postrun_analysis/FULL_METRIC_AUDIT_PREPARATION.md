# Full metric audit: static preparation, not an executed audit

Prepared helper: `postrun_analysis/full_metric_audit.py`.

SHA256: `e338d0408bbd235a31f93ef7f1dba75bff75d98e53d8ccef221487189df1b34c`.

This draft was written only under `postrun_analysis/`, outside the controller's
root `*.py` freeze-copy glob. No calibrated, registered, frozen or business
source was changed. No new formal result, formal raw ledger, or formal outcome
was read during preparation. No Docker command, model, network request, runtime
calibration, analysis or performance task was run. Host syntax compilation and
AST comparison only passed; they are not a runtime audit PASS.

## Prospective coverage and execution boundary

The audit covers the exact new plan's 6504 units: public 144 and formal 6360,
retaining all failures and phase/model identities separately. The fixed 22-phase
counts are public144, main2160, cross-model720, multiprocess360, each external
framework120, ablation baseline180, eight ablations180 each, two serialization
conditions180 each, and five memory conditions180 each. Historical outcomes are
not read or pooled. Aggregate terminal/attempt counts are closure bookkeeping,
not pooled estimates of model performance.

Before reading any plan or raw, the helper requires the formal lifecycle to be
`REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT`, all three stages terminal0, a
finished timestamp and no observed-live-child flag. It also takes both existing
formal/provider locks without writing their files. The separate retrieval driver
has no global receipt/lock that proves quiescence, so root must supply a new JSON
receipt from actual process observation after formal termination. No such receipt
has been fabricated by this preparation.

The quiescence receipt must be inside this study's `analysis/`, have this study
ID, status `ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING`, an actual timezone-aware
`verified_utc` after the formal finish, and observed empty
`active_real_timing_processes` / `active_retrieval_timing_processes` lists. This
is a scoped external observation, not an atomic universal lock over all software.

Example future in-container invocation, **only after those actual conditions**:

```text
/opt/lingxi/formal-venv/bin/python -B /drivers/protocol_repair_v1/postrun_analysis/full_metric_audit.py --study-out /evidence/protocol_repair_20261020_v1 --runtime-root /opt/lingxi/formal-checkout --quiescence-receipt /evidence/protocol_repair_20261020_v1/analysis/postrun_window_v1.json --version full_metric_audit_v1
```

Root must separately bind the reviewed postrun helper version/hash before use;
it is not retroactively part of the existing formal freeze. Existing outputs are
never overwritten. The helper returns1 for audit FAIL,2 for retained UNKNOWN,
and0 only for fully observed checked joins. Task failures alone do not imply an
audit failure.

## Safe reuse and determined schema differences

Nine pure public-auditor definitions are copied without AST changes: digest,
integer/finite-number/hash validators, Audit, groups, usage, spans and URI/state
checks. Both static preparation and the future helper verify this identity
against public-auditor SHA
`8f8100697b768473c65d408e49b0bec1961441aa016b620a309674a92704c0dc`.
Its public-only main is never called.

| Original public assumption | Full-matrix handling | Claim boundary |
|---|---|---|
| Only public144/main model/432 attempts | Exact6504 plan,22 phases, requested `unit.model_id`, finite19512 ceiling | Public and formal remain separately identified |
| Public execution receipt proves timing ended | Formal three-stage receipt, frozen snapshot command chain, source hashes and existing locks | Retrieval quiescence has a separate actual root observation |
| Raw files immutable in their entirety after public audit | Frozen public-prefix byte/hash validation; full append streams separately hashed after final closure | A formal append is not a public-prefix mutation |
| Every request has native HTTP preflight | Native metadata preflight versus external actual-body model-I/O projection | External body JSON is transiently decoded for exact compact byte/hash reconstruction; no prompt/header text is emitted or indexed |
| Native fact-audit field names everywhere | Separate native inventory/single-copy schema and external fixed-handoff SHA/inventory schema | Fixed prompt/framework source remains unchanged |
| `resource_samples.processes` is a mode boolean | Measured process list; terminal independently verifies process mode | Preserve the existing public v2 correction |
| Every arm has native observer/IPC files | Native observer/URI/index/AF_UNIX joins; external same-process four-role scheduler and embedded zero-native-IPC contract | External serialized role handoff bytes are not IPC or native state |
| Every memory hit is an actual earlier corpus task | Ordinary causal earlier same-chain rule versus exact `prior-<task>` seeded-condition rule | Related only for `memory_related`; no repairing stale/wrong/unrelated into relevance |
| Every index event has an enclosing memory/retrieval span | Ordinary rule stays strict; precise seed write search/add before normal memory span is separately identified | Logical seed-write span remains `NOT_OBSERVED`; independent index time is observed inside wall |
| Empty provider-call list certifies real usage completeness | No-attempt units explicitly uncalibrated, retain frozen raw0/null/flag without imputation | No claim of billed0 or real provider usage calibration from no call |

## Full planned and factual automatic validation

The helper verifies the full planned IDs and synthetic UUIDs; complete task
starts/terminals; source, snapshot, contract, case, phase and requested-model
identities; global and per-unit starts/ends; actual SDK byte/hash boundaries;
usage/cached usage/nulls; exclusive span conservation; per-URI production,
consumption, mapping, RPC framing and releases; native physical socket byte
accounting; index-only boundaries and causal memory source policy; worker/SHM
cleanup; sampled resource peaks; and the external real scheduler/version/four-role
work contract.

For factual validation it loads only the exact frozen pure-stdlib
`contract.py` and `oracle.py`, with reviewed hashes and allowed-import checks.
It independently classifies all144 current authored cases before checking
agreement with their frozen canonical acceptance pair. It then joins each of
6504 planned/observed units to its selected action and any recorded read-only
check/evidence SHA. Missing selections in failed units remain missing and are
counted separately; they are not turned into correct oracle answers. Original
success/quality is never overwritten or expanded into physically plausible
alternative acceptance. This is full automatic record/fixture validation,
not24 live Linux tools, not a human review, not an invented prospective30 draw,
and not production diagnosis accuracy.

## Unknowns and the specific claims they block

- A precise initial condition-seed index event without a measured logical write
  span produces `CONDITION_SEED_LOGICAL_WRITE_SPAN_NOT_OBSERVED`. It blocks complete
  logical memory-write timing coverage including seeding. It does not erase the
  independently measured index-only span, create an enclosing span, change the
  wall partition, or weaken ordinary-write requirements. The overall audit
  remains INCONCLUSIVE if this is the only unobserved category.
- Missing failed-attempt usage or no observed provider attempt blocks real usage
  calibration for those units and complete-pair primary usage inference where
  applicable. Values are not recovered from another cohort or filled with0.
- Missing cached-token details block cached-usage claims even when input/output
  totals are known. Explicit reported cache0 remains0.
- Missing hooks, HTTP boundaries, resource samples or cleanup observations retain
  scoped UNKNOWN findings. They cannot certify the corresponding claim.
- Fused native read/matmul/cast does not identify pure-read latency. Existing-arena
  reuse is not a measured zero mapping time. Shared close timing is one group,
  not one independently measured latency per URI. These semantic limits remain
  regardless of a mechanically conserved record join.

No code here computes an effect, CI, power test, Holm family or new safety
threshold. The only main-statistics authority remains the saved
`statistical_gates.py formal` output, conventionally
`analysis/stats/formal_primary_vN.json`. The old report's statistics function and
old human/prospective30 spotcheck must not be invoked as this batch's authority.

## Outputs and remaining validation

Future output under `analysis/full_metric_audit_vN/` consists of
`metric_audit.json`, `findings.json`, `unit_checks.jsonl`,
`audited_observations.jsonl`, and a metadata-only SQLite index. Raw sources are
streamed into a disk-backed projected index and queried per unit to avoid
holding the full6504 trace in RAM. Native model I/O stays opaque/hash-only;
request/transcript/wire/memory-payload text is not copied into the metadata index.
Input hashes/stability and output artifact hashes are retained. The index still
needs ordinary disk capacity and is not a substitute source ledger.

The2GiB container constraint is addressed structurally: SQLite page cache is
explicitly16MiB, temporary sorting is file-backed and mmap is disabled. Global
uniqueness/count joins return only IDs/counts from SQL. No full detailed terminal,
attempt, message, span, resource or IPC table is loaded globally. Detailed rows
are fetched for one run only, then released on the next run. Unit checks and
audited observations are streamed to their output files rather than accumulated
as wide in-memory lists. The final report is written last and binds their hashes;
partial output after an exception cannot stand in for a completed audit receipt.
This is a source-level memory design check, not a measured peak-RSS guarantee.

See `AUDITED_OBSERVATIONS_INTERFACE.md` for the report consumer contract. Before
actual use, root should review/bind this draft and run appropriately isolated
schema fixtures in a separate non-timing window. No such dynamic test, actual
complete-matrix audit or result PASS has occurred during preparation.
