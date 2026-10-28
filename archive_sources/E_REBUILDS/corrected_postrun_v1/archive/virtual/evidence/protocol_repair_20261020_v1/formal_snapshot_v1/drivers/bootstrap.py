"""Explicit future runtime bootstrap; candidate generation never invokes this."""
from __future__ import annotations
import hashlib
import json
import sys
from contract import LABEL_ACTIONS, VERSION


def install_before_workers_and_external_imports() -> dict:
    """Install data, not a replacement parser/diagnose function.

    Call before creating any fork worker and before importing an external adapter
    that captures Allowed-pairs text. No business source file is changed. A future
    runner must record the returned effective-contract identity in every unit.
    """
    stale = [name for name in sys.modules if name.endswith("formal_external_adapters")]
    if stale:
        raise RuntimeError("external prompt module was imported before contract bootstrap")
    from agent_runtime.agents import model_decision
    prior = dict(model_decision.LABEL_ACTIONS)
    model_decision.LABEL_ACTIONS.clear()
    model_decision.LABEL_ACTIONS.update(LABEL_ACTIONS)
    effective = json.dumps(LABEL_ACTIONS, sort_keys=True, separators=(",", ":")).encode()
    return {"version": VERSION, "effective_contract_sha256": hashlib.sha256(effective).hexdigest(),
            "prior_label_actions": prior, "installed_label_actions": dict(model_decision.LABEL_ACTIONS),
            "installation": "same mutable dictionary, before AgentHost fork and external prompt import",
            "business_source_modified": False}
