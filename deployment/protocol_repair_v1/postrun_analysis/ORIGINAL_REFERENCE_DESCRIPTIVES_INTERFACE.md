# Original-method descriptive producer — prepared source only

Producer: `original_reference_descriptives.py`. This preparation wrote only new
source/documentation under `postrun_analysis/`. No new formal outcome/raw/result
was read. The helper, imports, estimators, tests, plots, Docker and provider paths
were not executed. There is no generated numerical output or actual PASS.

The future destination is a fresh
`analysis/postrun/original_reference_descriptives_vN/`. Existing successful or
failed versions are never overwritten. A post-creation exception retains partial
files plus `failure.json`; only a fully serialized producer writes `receipt.json`
last. The caller must inspect an actual completed receipt before consuming data.

## Original method and execution boundary

The two copied pure definitions are `quality_interval` and `cluster_mean` from
the exact old `formal_report.py` source SHA256
`fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3`.
The original source is parsed for AST/source identity only. It is never imported
or executed; the old `Report`, old statistics, spot30 and old outcomes are never
loaded. At future execution the helper compares the copied definitions against
the original source and these existing reviewed AST hashes:

- `quality_interval`:
  `8764cb93f579df98f993e8e341e6c570f2515f6f6cee797c61ef1d4c268458f9`
- `cluster_mean`:
  `fe72a21fddecc1fb20fc38c62794605e81e2594066eac64141798b7d0816d13f`

`method_ast_sha256` contains exactly those two keys. The copied original
`finite` dependency is AST-compared separately and reported under
`original_method.dependency_ast_sha256`. The original bootstrap constant remains
10000 and `cluster_mean` is called with its original default seed **20261019**.
NumPy/SciPy versions and the actual loaded NumPy module/beta definition source
hashes are recorded separately; those file hashes are not a complete wheel lock.

The full-audit projection is UUID-sorted. Before using the original methods, the
producer restores original `task_runs` record order using the independently bound
`raw_line` metadata. This preserves the old method's chain insertion order under
its fixed random seed. The original source AST/default/constant and sorting
boundary are explicit; this is not a reimplementation using a newly chosen seed.

Only an actually closed three-stage formal lifecycle and a caller-provided actual
quiet-window receipt permit future execution. Both existing formal/provider locks
are acquired by read-only file opens. No lock file is created or modified. The
quiet receipt has the same scope as the full-audit receipt: actual observed empty
real/retrieval timing process lists after formal finish, not a universal atomic
lock over arbitrary software.

Example future invocation, **only after root observes actual timing closure**:

```text
/opt/lingxi/formal-venv/bin/python -B /drivers/protocol_repair_v1/postrun_analysis/original_reference_descriptives.py --study-out /evidence/protocol_repair_20261020_v1 --quiescence-receipt analysis/postrun_window_v1.json --version original_reference_descriptives_v1
```

Actual producer execution requires Linux and the original `/evidence/` virtual
root: the locks use `fcntl`, and `freeze.files` binds absolute original paths
including `str(plan_path)`. The Windows host workspace is a static preparation
location; passing its `F:/...` study path directly is not a supported execution
or a path-relocated reproduction. The report consumer may read host-copied
outputs after their actual Linux execution and identity validation.

Default authority inputs:

- `manifests/formal-freeze.json`, pinned to
  `2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e`.
- The exact fullfreeze-bound `manifests/formal_plan.json` and saved entry source
  `formal_snapshot_v1/drivers/statistical_gates.py`, SHA
  `d87bb161630560f4fe7c62e061a167e36ba5d6c93a9c7a5dbd2e4d12c5240251`.
- `analysis/full_metric_audit_v1/metric_audit.json`, its hash-bound
  `audited_observations.jsonl` and `unit_checks.jsonl`. The reviewed producer
  analyzer identity is presently
  `c6a09166d2eede459b907681a02950c3ff8cc683635554b257336b130ef18158`.
  This is a currently approved source draft, not evidence of its execution.
  A later source revision needs its preimage retained and an explicit reviewed
  version/binding update.
- `analysis/stats/formal_primary_v1.json` and
  `analysis/postrun/secondary_heterogeneity_v1/receipt.json`, as identity/header
  authorities only. Their real-ledger hashes must match the full audit.

Global full-audit FAIL, a missing6504 projection, foreign/duplicate identities,
changed hashes or per-unit audit FAIL prevent description output. A retained
full-audit UNKNOWN remains UNKNOWN with original `claim_scope`/`limits` in the
receipt. No raw source is read or rehashed by this producer. Each safe projection
terminal preserves its all-planned failed outcome and original null/absent values.

## Saved output contract

`receipt.json.status = COMPLETE_SAVED_ORIGINAL_METHOD_DESCRIPTIVES` means these
two limited descriptions were saved. It does **not** certify semantic external
equivalence, all indicators, real-provider usage calibration, quality safety or
the primary confirmatory family.

Receipt identity fields are:

```text
study_id
formal_freeze_sha256
raw_input_sha256
source_primary_sha256
source_secondary_receipt_sha256
source_metric_audit_sha256
source_projection_sha256
source_unit_checks_sha256
formal_plan_sha256
method_ast_sha256       # exactly quality_interval and cluster_mean
files                  # filename -> actual output SHA256, excludes receipt
```

`absolute_quality.json` has these common identity fields plus `rows` with exactly
30 entries: main `(overall/all + four domain levels) × six arms`. Each row has
`phase`, `dimension`, `level`, `arm`, `metric=quality_success`, `value`,
`ci95_low`, `ci95_high`, `planned_units`, `terminal_units`, `unique_tasks`,
`chain_count`, `failed_terminal_units`, `status`, and `method`. Overall arm groups
retain360 units/120 tasks/20 chains; domain arm groups retain90/30/5. These are
the old conservative **absolute-rate** exact chain-event intervals, including
all-pass uncertainty. They are not paired quality safety bounds and cannot
replace the saved five paired quality objects or support noninferiority wording.

`matched_external_summary.json` includes the common identity fields and:

```text
status = SAVED_ORIGINAL_METHOD_DESCRIPTIVES_AWAIT_SEPARATE_EQUIVALENCE
task_ids                # exact60 frozen preselected IDs, not a new sample
task_ids_sha256
repeat_ids = [0, 1]
native_reference_phase = main
native_reference_arm = T0
comparison_scope = SYSTEM_LEVEL_REFERENCE
rows                    # exactly9 system × metric descriptions
```

Rows are `native_T0`, `autogen`, `langgraph` × `quality_success`,
`provider_input_tokens`, `wall_sec`. Each group retains all120 planned/observed
units,60 unique tasks and20 chains. Each row contains `value`, `ci95_low/high`,
`planned_units`, `observed_units`, `terminal_units`, `unique_tasks`, `chains`,
`chain_count`, `failed_terminal_units`, `status`, `method`, and observed
no-attempt/independent usage-calibration counts. `quality_interval` supplies
quality; the unchanged `cluster_mean` supplies the two cost/time descriptions.
No p-value, paired effect, Holm adjustment or new primary family is produced.

The producer derives the cohort only from the exact frozen external plans and
checks equality of both frameworks' task/repeat sets with60 IDs × `[0,1]`, then
joins the matching main **T0** units. It does not substitute another native arm,
public unit, repeat or task. It does not require the separate external-equivalence
audit first, avoiding a gate/summary dependency cycle. Every row is marked
`comparability_status=NOT_ASSESSED_BY_DESCRIPTIVE_PRODUCER`. The report consumer
must join the actual independently saved equivalence audit's240 matched pairs
and endpoint comparability before rendering figure9. An absent or noncomparable
equivalence endpoint is not promoted to PASS by this summary.

`matched_observation_index.json` binds360 selected safe-projection IDs and raw-line
hash metadata to their independently audited UNKNOWN/usage counts. It contains no
request, transcript, query or raw memory payload. All three output files are
included in `receipt.files`.

## Null and no-attempt semantics

There is no success-only metric selection. If any selected terminal has a genuine
null/absent/nonfinite cost/time, the original `cluster_mean` returns a null triple
for that entire planned group. No mean/CI is imputed, and no call count supplies a
replacement metric. Failed tasks with measured finite costs remain in the group.

A native zero-attempt terminal can retain the frozen driver's raw token/cache0
and its raw completeness flag. The helper does not use that flag to certify real
usage: it reads the independently audited `unit_checks.real_provider_usage_complete`
instead. A recorded task-cost0 can remain an observed no-call task cost while
provider usage calibration stays false/UNKNOWN. If such0 values enter an original
task-cost description, the endpoint status explicitly disclaims provider-usage
certification. No billed0 claim or missing provider report is fabricated.

Preparation has not run syntax compilation, AST checks, dependency imports,
fixtures or numeric validation. The future source/AST checks above are implemented
gates, not evidence that they have executed. Root must arrange actual isolated
validation and execution after the real/retrieval timing windows close.
