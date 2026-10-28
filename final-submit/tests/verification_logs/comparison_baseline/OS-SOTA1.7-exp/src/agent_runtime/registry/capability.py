"""能力描述与 Agent 名片（用于注册、握手与能力发现）。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from agent_runtime import PROTOCOL_VERSION


class Capability(BaseModel):
    """单项机器可读能力契约。

    赛题要求能力描述不能只是一段名字；这里把描述、输入和输出 schema 一并
    放进握手名片，使注册表/外部适配器能够查询 Agent 的真实契约。
    """

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)


class AgentCard(BaseModel):
    """Agent 名片：注册、握手与能力发现的依据。"""

    agent_name: str = Field(min_length=1)
    protocol_version: str = PROTOCOL_VERSION
    capabilities: list[str] = Field(default_factory=list)
    capability_specs: list[Capability] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def _sync_capability_names(self) -> "AgentCard":
        names = [spec.name for spec in self.capability_specs]
        if len(names) != len(set(names)):
            raise ValueError("capability_specs names must be unique")
        if self.capability_specs:
            declared = set(self.capabilities)
            missing = set(names) - declared
            if missing:
                raise ValueError(f"capability_specs not declared in capabilities: {sorted(missing)}")
        return self

    def describe(self, capability: str) -> Capability | None:
        for spec in self.capability_specs:
            if spec.name == capability:
                return spec
        return None


class HandshakeResult(BaseModel):
    """握手结果。"""

    accepted: bool
    agent_name: str
    server_protocol: str = PROTOCOL_VERSION
    reason: str = ""


__all__ = ["Capability", "AgentCard", "HandshakeResult"]
