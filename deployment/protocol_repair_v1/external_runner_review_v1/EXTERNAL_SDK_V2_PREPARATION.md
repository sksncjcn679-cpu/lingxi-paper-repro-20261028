# External SDK v2: exact attribution of a still-blocked local probe

The actual root-executed import diagnostic was read in full. Its report SHA256
is `5d400ac296d61ca5096adec1c68742b592ef5d19637903134fabcfb0264f340d`.
It retained exactly one AF_INET6/SOCK_STREAM/protocol-0 construction attempt in
phase `import:autogen_agentchat.agents`. The non-importlib caller chain is:

1. `autogen_core/tools/_base.py`, module line 19.
2. `jsonref.py`, module line 11.
3. `requests/__init__.py`, module line 45.
4. `urllib3/__init__.py`, module line 15.
5. `urllib3/_base_connection.py`, module line 5.
6. `urllib3/util/__init__.py`, module line 4.
7. `urllib3/util/connection.py`, module line 137.
8. `urllib3/util/connection.py`, `_has_ipv6`, line 126.
9. Standard-library `socket.py`, `__init__`, line 232.

The actual urllib3 module SHA256 is
`2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74`;
the actual `_has_ipv6` function-source SHA256 is
`d8ab130d2b780c9623eebffb295089e34150fa55636d9398c1b0f9340a2b09cf`.
The probe calls `socket.socket(AF_INET6)` before trying to bind `::1`; the audit
raised before that socket constructor completed. The diagnostic records no DNS
or connect attempt. This is now actual container evidence, not the earlier host
source hypothesis.

## New helper and exact scope

New file: `deployment/protocol_repair_v1/calibrate_external_v2.py`.

SHA256: `44e084f02f40ad12f327d61cd1a3b80c0a3a383594fd08f62df2e71932bc3f09`.

The complete source diff is `calibrate_external_v2.diff` in this review folder.
The new helper copies the v1 matrix and recorder checks unchanged, and changes
only the destination/version, denial audit and supporting source/provenance
checks. It imports the same AutoGen component in the exact diagnostic phase
before any unit timing so caller attribution is deterministic.

All INET, DNS, connect and credential-read attempts still raise. Attribution to
`expected_blocked_local_ipv6_probe` requires the exact phase and constructor
fields, the entire observed non-importlib stack including line numbers, the
actual module hash, the actual function-source hash, and the live function code
identity. An attribution failure is unexpected and remains blocked. Every event
retains its complete redacted stack and raw denial category.

For PASS the original raw counter must remain
`internet_socket_attempts: 1`, the expected-probe classification must equal one,
and every unexpected INET/DNS/connect/credential category must equal zero. No
counter is cleared, no network operation is permitted, and no zero-socket claim
is made. The output report retains raw counts, classification counts, explicit
unexpected zeros, exact reference hashes and event records.

## Preserved executed preimage

- Original v1 helper remains
  `ac9b39dcd4cdbadbf4c314bb59c066675277fe2cbd47cbdeb3fec4ef9ade8b11`.
- Original v1 FAIL report remains
  `5cf1e7a2bf27692515213703f6d234395e8d016f688a820b9028dc316bc1eb5f`.
- The external runner remains
  `45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60`.

The helper checks all three preimage hashes and the diagnostic report before and
after its work. Existing v1 raw and FAIL artifacts are never rewritten.

## Root-dispatched command

```text
/opt/lingxi/formal-venv/bin/python /drivers/protocol_repair_v1/calibrate_external_v2.py --destination /evidence/protocol_repair_20261020_v1/calibration/external_sdk_v2
```

The planned matrix remains 14 SDK fixture units plus two dry units and 18 mock
HTTP dispatches. Root owns execution. The subagent performed only host static
syntax/source/hash checks, which passed; it did not run Docker, a calibration,
a model, a network request or a performance task. Actual v2 PASS is pending root
execution and inspection.
