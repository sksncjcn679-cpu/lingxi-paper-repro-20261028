#!/bin/bash
set -eu
OUT=/evidence/formal_20261019
/opt/lingxi/venv/bin/python -m venv --system-site-packages /opt/lingxi/formal-venv
/opt/lingxi/formal-venv/bin/python -m pip install autogen-agentchat langgraph scipy matplotlib pandas pyarrow > "$OUT/logs/preflight/dependencies.stdout.log" 2> "$OUT/logs/preflight/dependencies.stderr.log"
/opt/lingxi/formal-venv/bin/python -m pip freeze --all > "$OUT/manifests/dependencies.txt"
/opt/lingxi/formal-venv/bin/python -m pip check > "$OUT/logs/preflight/pip-check.log" 2>&1
