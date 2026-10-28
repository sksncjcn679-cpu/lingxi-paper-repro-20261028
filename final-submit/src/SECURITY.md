# Security and Trust Boundaries

This project is a competition/research prototype, not a hardened multi-tenant execution service.

## Security-relevant boundaries

- The default runtime is single-host and mock-first.
- The subprocess sandbox applies timeout/resource controls and can optionally use Podman, but it is not a substitute for a hardened hostile-code sandbox.
- Code-fix verification fails closed when no sandbox is configured; task/generated code is never executed via an in-process `exec` fallback.
- Unix Domain Socket transport is local-only and uses framed messages plus pathname ownership checks; it does not provide remote authentication or encryption.
- Shared-memory views are exposed read-only at the NumPy API level; this is not an OS-enforced write-protection boundary.
- Checkpoint/event-journal SHA-256 fields detect accidental corruption/tampering; they are not authenticated signatures and do not protect against an attacker who can rewrite both data and hashes.
- The persistent idempotency ledger deduplicates completed requests. It does not claim distributed exactly-once semantics for concurrent first deliveries.
- A2A support is a data-model adapter only; it is not a complete A2A network/server security implementation.

## Secrets

Do not commit API keys or private credentials. Use `.env` created from `.env.example`; `.env` is ignored by Git.

## Reporting issues

For a competition submission, record security-relevant defects in `docs/RISK_REGISTER.md` and include the exact reproduction steps, affected version, environment and expected/observed behavior.
