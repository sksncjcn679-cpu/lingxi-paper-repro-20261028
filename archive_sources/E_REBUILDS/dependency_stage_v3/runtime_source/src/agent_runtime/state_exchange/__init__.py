"""非文本状态交换（embedding / compact state vector / plan DAG）。"""

from __future__ import annotations

from agent_runtime.state_exchange.exchange import StateExchange, StateRef
from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor, SharedVectorBuffer

__all__ = ["StateExchange", "StateRef", "SharedVectorBuffer", "SharedArrayDescriptor"]
