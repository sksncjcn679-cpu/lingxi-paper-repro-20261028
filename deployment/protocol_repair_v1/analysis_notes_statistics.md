# New-study statistical gate handoff

Material Passport: implementation preparation; original protocol sections 4.1/6 and already registered new-study protocol are authoritative. Static inspection only on 2026-10-20; no new/old model, Docker, network, simulation, numeric analysis or performance task was run by this worker. No old OUT was read. One overly broad test search returned old `final-submit/tests/results` snippets; they were discarded and did not supply any choice, value, grid, endpoint or method in this implementation.

Only implementation file: `deployment/protocol_repair_v1/statistical_gates.py`. This note is the other new owned file. Existing helpers, public registration, corpus and raw files were not changed.

## Commands after real timing ends

```text
cd /drivers
python -m unittest -v formal_statistics_test
python /drivers/protocol_repair_v1/statistical_gates.py prospective
```

The first command loads the existing synthetic test module through unittest. Its `__main__` block must NOT be run: that block writes into the historical OUT. The module contains seven test methods (31 assertion-call sites, including assertions repeated inside loops). It independently checks Holm known answers, exact sign-flip enumeration, repeat/non-independent-case counts, nondegenerate all-pass safety bounds, unknown-cost/missing-quality behavior, duplicates, unequal-chain conservatism and early-failure warnings. No new synonymous statistical test suite was added. A reported total of “36 statistical invariants” was not independently established by static source inspection; do not relabel seven methods as36 passed tests.

Do not run `formal_statistics_protocol_aligned_v3_test.py` unchanged: its setup reads the old OUT. If wrapper regressions are needed, reuse its existing assertions with a synthetic 120-task/20-chain plan and108 pre-treatment applicability labels generated in memory, rebinding v3 ID hashes to those synthetic IDs. Never redirect it to historical task runs. Important wrapper checks are the exact mixed `[120,108,120,120,120]` population, sole ten-value Holm adjustment including missing p as planned tests, unchanged seed offsets, and the lack of adjusted p-values in descriptive-only branches.

New entry-specific boundary checks remain to execute, with no provider and outside real timing:

- Validate the budget report using the new public calls, particularly that a failed attempt without provider usage gives `total=null`, preserves a partial observed subtotal and keeps all currency prices/costs UNKNOWN. Validate cache-unknown input separately; never infer cached=0.
- Pass an altered study ID, task content hash, effective-contract hash or run ID to the raw identity guard using in-memory fixtures; each must be rejected. Test an unmatched start independently.
- Validate the new prospective input hashes against registration and assert no formal start/outcome exists. The prospective command itself enforces this.
- Once formal analysis is available, ensure exactly one ten-test confirmatory family; the14 matched phase effects and every other phase have no `p_holm`/Holm-family field. Incomplete phases must retain planned counts and withhold effects.

## Prospective artifacts

The command creates a previously nonexistent directory:

```text
/evidence/protocol_repair_20261020_v1/analysis/stats/preformal_gate_v1/
  public_task_runs.jsonl
  public_provider_calls.jsonl
  public_provider_attempt_starts.jsonl
  public_task_unit_starts.jsonl
  prospective_power.json
  preformal_budget_estimate.json
  statistical_contract.json
  gate.json
  sources/statistical_gates.py
  sources/formal_statistics.py
  sources/formal_statistics_protocol_aligned_v3.py
```

Files are exclusive-create, preserving every raw value and failure. Public evidence snapshots are compared again during formal analysis. The prospective command only reads the new public144 raw units, their actual attempts/usage and new registered plan/corpus. It never imports a real driver or reads credentials/environment variables. Task-unit starts contain only plan-unit common fields: their source linkage comes from run_id, registered plan/content identity and public source registration. Actual source_sha256/model_evidence is required only in task terminal and provider attempt start/end tables, where execute() really emits it. No raw fields are invented.

The power sensitivity uses the already established failure/ICC/0,-2,-5pp paired Dirichlet-multinomial grid, now fed only by the new24 public tasks. It simulates 10,000 replicates for the fixed20-chain design, does not treat repeats as cases and does not offer an alternate test or automatic40-chain branch. One public chain/domain cannot identify ICC. The task-binomial upper value is explicitly a task-independence heuristic for sensitivity, with NO arbitrary-chain-dependence coverage. A separate Bonferroni simultaneous-domain chain-any-failure envelope (one public chain/domain, very wide even with no failures) is included as another conservative sensitivity endpoint. Neither replaces the frozen quality safety method. The conservative simultaneous-domain chain-any-loss exact bound cannot exclude5pp even at all-pass20 chains, so the registered decision stays120 tasks plus WITHHELD_PROSPECTIVELY. Both all120 and unequal-chain108 best-case bounds are reported.

The budget extrapolates per-arm public actual attempts, provider token components and serial task wall time to every remaining frozen phase. Cross-model, multiprocess, intervention and external estimates are explicitly proxies. Missing failed usage and cached tokens remain null. Observed retry rates already include public retries; the3-attempt ceiling is reported separately. Request timeout sums exclude local execution/backoff and are not wall-time bounds. The input-byte/reported-token caps are technical limits and do not establish a monetary ceiling. `currency_budget_gate` stays `UNKNOWN_UNVERIFIED_PRICES_AND_UNSPECIFIED_CURRENCY_CEILING`; the script grants no provider execution authorization.

## Formal freeze and analysis

The full freeze remains an independent root-owned action, following the actual public metric gate. Preserve the registered `protocol.yaml` unchanged; record formal freeze as a separate receipt. The analysis expects `manifests/formal-freeze.json` with `source_sha256`, `files` and UTC `frozen_at_utc` (or `created_utc`). Its `files` map must bind all five INPUTS listed in the script; public registration; the statistical contract, gate, power and budget reports; the executing statistical_gates.py; and the three source snapshots. Bind the independent real metric gate and other execution helpers too as required by the full freeze. Both contract and full freeze must precede the first formal start.

After formal execution and outside real timing:

```text
python /drivers/protocol_repair_v1/statistical_gates.py formal --version formal_primary_v1
```

Output is a new `analysis/stats/formal_primary_v1.json`; a repeated run must use a new version. It contains:

- v3 main confirmation: exactly one10-test Holm family, S0→SS0 on predeclared108, the other pairs on all120, domain-stratified10,000 chain bootstrap, repeats averaged by task, exact2^20 chain sign flips, unchanged conservative safety bound and missing/failure rules. Only population ID constants are rebound to the new pre-outcome identity contract. Numerical source hashes are unchanged and checked before import.
- v3 all120 replay retained strictly as archive-only plus the descriptive full120 state and state108 branches; they never compete with the primary family.
- `formal_phase_statistics`: every planned nonpublic phase including cross-model/multiprocess/external frameworks,8 ablations,2 serializations and5 memory conditions. All are exploratory; phases with incomplete terminal units retain their denominator and withhold effects. Within-phase cross-model/multiprocess pair statistics have only raw p-values. External phases are separate system references requiring independent comparability evidence.
- `matched_phase_effects`: eight ablations and two serializations against matched `ablation_baseline`; four memory conditions against `memory_condition_none`. Matching is by frozen task/repeat/model/topology, with content/chain/domain checks. It reuses the existing report paired analysis and verified numerical kernel; quality/unknown costs/all failures remain visible. There is no added confirmatory family.

Static status: AST parsed successfully. Numeric tests, real144 schema compatibility, power simulation, budget generation and end-to-end freeze/analysis have NOT yet been executed.
