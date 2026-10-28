#!/usr/bin/env python3
"""OS-SOTA1.6: run the agent-runtime CLI from *this checkout*, never from a stale install.

Root cause found in the OS-SOTA1.5 Windows rerun: ``run_all_suites.sh`` exported
``PYTHONPATH="$ROOT/src:..."``.  On Windows the separator is ``;`` so the entry was ignored and
Python silently imported an older editable install (agent-runtime-opus4.8, source SHA
7eed80fb...), producing 17 bogus failures (unknown sub-commands, missing providers).  This
launcher inserts ``<repo>/src`` explicitly and refuses to run if the imported package does not
come from this checkout.

Usage: python3 scripts/rt.py <cli args...>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
os.environ["AGENT_PROJECT_ROOT"] = str(ROOT)
# child processes (agent hosts, routers, sandboxes) must see the same tree, on every OS
os.environ["PYTHONPATH"] = str(SRC) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")


def preflight() -> None:
    import agent_runtime

    loaded = Path(agent_runtime.__file__).resolve()
    if SRC.resolve() not in loaded.parents:
        sys.stderr.write(
            f"[rt] REFUSING TO RUN: agent_runtime imported from {loaded}\n"
            f"[rt] expected a module under {SRC}. Uninstall the stale package "
            "(pip uninstall agent-runtime) or fix PYTHONPATH.\n"
        )
        raise SystemExit(97)


if __name__ == "__main__":
    preflight()
    from agent_runtime.cli import main

    raise SystemExit(main(sys.argv[1:]))
