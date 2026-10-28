# External runner preparation review v1

Prepared on 2026-10-20. Status: **HOST SOURCE PREPARED; STATIC CHECKS ONLY**.

## Owned paths

- `deployment/protocol_repair_v1/external_runner.py` (new runner).
- `deployment/protocol_repair_v1/calibrate_external.py` (new isolated calibration harness, subsequently authorized by the root agent).
- `deployment/protocol_repair_v1/external_runner_review_v1/PREPARATION_REVIEW.md` (this independent preparation note).

No existing runner, business source, manifest, evidence ledger, plan, protocol
condition, or public-pilot artifact was edited. No Docker command, framework
execution, performance task, model request, network request, credential loading,
or `.env`/secret-file read was performed for this preparation.

The original `deployment/formal_external_runner.py` remained byte-identical:

`46ddfa3744f8e605aaa2611fc2b620719affdb361232a97b64c72dd11b9a2d31`

Prepared runner SHA256:

`45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60`

Prepared calibration-harness SHA256:

`ac9b39dcd4cdbadbf4c314bb59c066675277fe2cbd47cbdeb3fec4ef9ade8b11`

## Implementation boundaries

The runner first imports the repair `real_driver`, which installs the fixed
24-pair mutable output contract before `formal_external_adapters` captures its
Allowed-pairs prompt. It then explicitly binds `adapter.check = oracle.check`.
The existing adapter, four-role ordering, framework schedulers, fixed external
prompt, scorer, T0 baseline, model configuration, three-attempt bound, retry
policy, and frozen plan are reused. External unit validation rejects a changed
contract, other arms, processes, or interventions. Dry classification invokes
`oracle.classify_facts` on current observations and never consults `expected`.

The target evidence root is the repair driver's
`/evidence/protocol_repair_20261020_v1`. The CLI's real path takes the native shared
execution lock, invokes `native.validate_freeze()`, rejects unresolved task or
provider starts, reserves up to three attempts per unit, and rechecks the global
attempt/reported-token budget before each request. It records the actual SDK
HTTP request bytes before transport, suppresses SDK retries, excludes headers
from saved requests, and preserves all attempted responses/errors. Every unit's
common recording fields contain `effective_contract_sha256` and `model_evidence`.
Known reported totals are a lower bound; unknown usage never becomes a billed
zero or a later-study replacement.

Dry and SDK-fixture execution call the native configuration with
`offline=dry or fixture`; neither path loads the real secret. Dry output is
separate under `calibration/external_recorder_dry_v1`. The new calibration seam
is `execute(unit, row, _sdk_fixture_factory=factory)`, where `factory(cfg)` must
return an OpenAI SDK client using an in-memory transport and `max_retries=0`.
Fixture records carry `offline_sdk_transport_fixture_not_provider`, real token
fields remain `None`, and fixture usage is kept only for calibration. Missing
cached-input usage remains `None`, including when other usage is complete; an
explicitly reported cached-token zero remains zero.

## Static checks performed

The host Python parser and compiler inspected source without importing or
executing the runner. Checks passed for syntax, native-before-adapter import
order, explicit repair-oracle binding, freeze call, offline configuration
selection, no acceptance-answer access in `automatic_label`, missing-cache
preservation, no secret-file path in the new runner, and unchanged legacy bytes.
No runtime correctness, performance, overhead, SDK dispatch count, scheduler
behavior, or provider outcome is claimed by these checks.

## Prepared minimum calibration for the root agent

`calibrate_external.py` is prepared but has **not** been executed. Its default
destination is the new
`/evidence/protocol_repair_20261020_v1/calibration/external_sdk_v1`. It refuses to
overwrite a destination or target any location outside a direct child of the
repair calibration directory. Both runner output globals and the dry output
root are redirected before execution; the original study raw hashes are checked
before and after.

The planned matrix has **16 unique synthetic task units**: eight successful SDK
paths covering four public domains in both frameworks, six additional SDK
scenarios (malformed JSON, wrong action, missing usage, missing cached usage, two
503 responses then recovery, and three 503 responses exhausted), and one dry
unit per framework. It expects **18 MockTransport HTTP dispatches**, 18 complete
durable attempt starts/ends and actual-body joins, 14 SDK terminals, and two dry
terminals. Normal responses explicitly report cached zero so absence is tested
separately. Fixture decisions derive from current factual predicates; wrong
responses are retained without repair.

The harness installs a network/credential-file denial audit hook before importing
the drivers, checks every configuration request is explicitly offline, records
actual framework versions, source/dependency/corpus hashes and four-role work
counts, verifies no real usage claim or leaked offline header key, and checks
client cleanup. Requested retry backoffs are logged but sleeping is bypassed
locally; the result expressly excludes performance and overhead claims. Synthetic
UUIDs include the case, framework, scenario and calibration destination; versioned
reruns cannot collide with formal unit IDs.

After the root agent confirms that the concurrent public pilot has ended, the
prepared in-container command is:

```text
/opt/lingxi/formal-venv/bin/python /drivers/protocol_repair_v1/calibrate_external.py --destination /evidence/protocol_repair_20261020_v1/calibration/external_sdk_v1
```

The root agent owns container dispatch. No command above was run during
preparation, and no calibration outcome is claimed.

## Follow-up gates after the prepared calibration

After the concurrent public pilot is finished and execution is again permitted:

1. The prepared script checks native-before-adapter import, exact captured
   24-pair prompt identity, and two dry units. If broader label-level integration
   coverage is needed, in a fresh process with network creation denied import the repair native
   driver and this runner; assert the installed map and adapter prompt contain
   exactly the same 24 pairs and `adapter.check is oracle.check`. Use the 24
   public cases in both actual frameworks for 48 dry units. Check four-role
   order, factual fixture-check success, contract hashes, model-evidence tags,
   and zero provider attempts. Do not obtain calibration success by substituting
   `row['expected']` for the factual oracle decision.
2. The prepared script covers the requested SDK/error/usage/retry minimum. For
   broader boundary coverage, in a new isolated calibration directory set both `runner.OUT` and
   `runner.native.OUT` to that directory and supply a bounded
   `manifests/budget_limits.json`. Use unique calibration run IDs and the same
   external T0 unit fields, including `effective_contract_sha256`. For each
   framework, exercise one successful SDK request, two transient failures then
   success, three terminal timeouts, malformed JSON, a valid wrong pair, missing
   usage, missing cached usage, and explicit cached zero. Add pre-transport byte
   ceiling and pre-attempt budget stops. Assert actual SDK byte hashes/counts,
   maximum three dispatches, full durable starts/ends, correct failure retention,
   unknown usage, no real-usage claims, redaction, and span conservation. The
   factory must set `max_retries=0` explicitly and use a nonsecret offline key.
3. Validate resume rejection and lock contention without provider dispatch, then
   include this runner and its reused adapter/oracle/bootstrap dependencies in
   the prospective full freeze. The existing native freeze gate is mandatory
   before invoking the real CLI path. Preserve public-pilot and formal records
   in their designated versioned locations.

## Remaining limits and risks

- This file targets the existing Linux runtime (`fcntl`, `/proc`, `/dev/shm`);
  Windows host static validation does not exercise that environment.
- The SDK fixture factory is a dependency-injection seam, not a network sandbox.
  The calibration harness must independently deny network access and redirect
  both evidence-root globals so fixture records never enter formal raw ledgers.
- Cached usage can be unknown even with otherwise complete provider totals;
  downstream analysis must retain this distinction.
- The shared reported-token stop is based on observed lower-bound usage. Failed
  requests with missing provider usage remain unknown; no monetary ceiling or
  actual billing claim is introduced.
- The adapter's existing reviewer uses the frozen acceptance scorer. The new
  factual oracle is bound for its read-only check, while acceptance definitions
  and protocol conditions remain as already authored.
- Direct Python calls to `execute` are intended for isolated calibration. Formal
  execution must use the CLI/run path, which owns freeze, resume, unit-start,
  reservation, and shared-lock control.
