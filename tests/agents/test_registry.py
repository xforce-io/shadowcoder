import pytest
from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.config import Config


class FakeAgent(BaseAgent):
    """Minimal concrete agent for registry tests.

    Action methods (preflight/design/develop/review) are now concrete in
    BaseAgent, so FakeAgent only needs to implement the three abstract methods.
    """

    def _get_model(self) -> str:
        return "fake-model"

    def _get_permission_mode(self) -> str:
        return "auto"

    async def _run(self, prompt, *, cwd=None, system_prompt=None,
                   session_id=None, resume_id=None):
        from shadowcoder.agents.types import AgentUsage
        return ("fake output", AgentUsage())


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
