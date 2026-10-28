# Access diagnostic preparation after root's external SDK v1 run

The root agent reports that external SDK v1 retained all 16 expected unit
outcomes, 14 SDK units and 18 mock HTTP dispatches, but its final denial-counter
check failed with `internet_socket_attempts: 1`. That failure and all original
source/raw evidence are retained. This note does not reclassify the event.

Source inspection confirms the v1 helper does not exercise its denial guard with
a self-test and does not reset the counter. Its counter therefore represents a
runtime `socket.__new__` audit event. The original audit only saved a count, so
the actual socket family and caller cannot be established from that count.

The host's installed urllib3 source contains an import-time `_has_ipv6('::1')`
probe which constructs an AF_INET6 socket, attempts a local bind, and catches
exceptions. This is a hypothesis for the observed event, not evidence that the
container's event came from that helper. The new diagnostic is needed to retain
the actual caller and actual container-module identity.

New diagnostic source:

`deployment/protocol_repair_v1/diagnose_external_access_v1.py`

SHA256:

`0e69be96a155608e8b595efe3eeef9c4f591fff09b76306dc204dbc88add9c55`

The diagnostic imports the same runner, SDK and actual framework components,
constructs/closes one MockTransport client without requesting a completion, and
runs no task units. It rejects INET sockets, DNS resolution, connect operations,
and credential-file reads. Every rejected event is retained with import phase,
numeric socket-constructor fields, and a stack containing file suffix/function/
line only. No source line, local, endpoint, environment value or request header
is captured. If urllib3 is actually imported, the report also records the actual
module SHA256 and the public `_has_ipv6` function source/hash for attribution.

Prepared command for root dispatch:

```text
/opt/lingxi/formal-venv/bin/python /drivers/protocol_repair_v1/diagnose_external_access_v1.py --destination /evidence/protocol_repair_20261020_v1/calibration/external_access_diagnostic_v1
```

The output is `access_diagnostic.json` in that new directory. Existing output is
never overwritten. The helper asserts the executed v1 calibration source and
runner hashes before and after its imports. Static syntax/source-preservation
checks passed on the host. The subagent has not executed Docker or this
diagnostic. No v2 calibration or network-policy relaxation is prepared until the
actual diagnostic stack is reviewed.
