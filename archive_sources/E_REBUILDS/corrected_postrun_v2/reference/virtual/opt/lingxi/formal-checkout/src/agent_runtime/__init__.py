"""Agent Runtime — 面向多智能体协作的低开销通信、状态传递与共享记忆机制。

应用创新赛道参赛项目核心包。强调多智能体协作的「系统层机制」：
低开销结构化通信、非文本状态传递、跨任务共享记忆复用。
"""

from __future__ import annotations

__version__ = "1.8.6a1"
# Release codename used for deliverable names and reports.
RELEASE_NAME = "OS-SOTA1.8.6-exp"
PROTOCOL_VERSION = "1.3"
# Session-level extension negotiated in the connection hello (message schema stays 1.3).
SESSION_EXTENSION = "1.4-cphc"

__all__ = ["__version__", "PROTOCOL_VERSION", "RELEASE_NAME"]
