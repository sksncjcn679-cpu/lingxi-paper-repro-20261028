from agent_runtime.config import load_config
from agent_runtime.registry.registry import AgentRegistry
from agent_runtime.runtime.builder import build_default_agents


def test_default_agents_publish_machine_readable_capability_contracts():
    agents = build_default_agents(load_config(load_env=False))
    registry = AgentRegistry()
    for agent in agents.values():
        assert registry.handshake(agent.card()).accepted

    for card in registry.all_cards():
        assert card.capability_specs
        assert {s.name for s in card.capability_specs} == set(card.capabilities)
        for spec in card.capability_specs:
            assert spec.description.strip()
            assert spec.input_schema
            assert spec.output_schema
            assert registry.describe(spec.name)
