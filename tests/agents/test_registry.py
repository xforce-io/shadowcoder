import pytest
from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.config import Config


class FakeAgent(BaseAgent):
    async def preflight(self, request):
        return PreflightOutput(feasibility="high", estimated_complexity="moderate")

    async def design(self, request):
        return DesignOutput(document="ok")

    async def develop(self, request):
        return DevelopOutput(summary="ok")

    async def review(self, request):
        return ReviewOutput(comments=[], reviewer="fake")


def test_register_and_get(tmp_config):
    AgentRegistry.register("claude_code", FakeAgent)
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    agent = registry.get("claude-code")
    assert isinstance(agent, FakeAgent)


def test_get_caches(tmp_config):
    AgentRegistry.register("claude_code", FakeAgent)
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    a1 = registry.get("claude-code")
    a2 = registry.get("claude-code")
    assert a1 is a2


def test_get_unknown_agent(tmp_config):
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    with pytest.raises(KeyError):
        registry.get("nonexistent")
