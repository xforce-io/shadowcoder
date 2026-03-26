"""Tests for BaseAgent refactor correctness.

AC1-AC4: BaseAgent abstract/concrete contract.
Acceptance test: test_base_agent_abstract_run_only
"""
from __future__ import annotations

import asyncio
import pytest

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import AgentUsage


class MinimalSubclass(BaseAgent):
    """Minimal subclass implementing only the three abstract methods."""

    def _get_model(self) -> str:
        return "test-model"

    def _get_permission_mode(self) -> str:
        return "auto"

    async def _run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        system_prompt: str | None = None,
        session_id: str | None = None,
        resume_id: str | None = None,
    ) -> tuple[str, AgentUsage]:
        return ("test output", AgentUsage(input_tokens=1, output_tokens=1))


def test_base_agent_abstract_run_only():
    """Acceptance test: BaseAgent cannot be instantiated (has abstract _run,
    _get_model, _get_permission_mode), but a subclass implementing only those
    three abstract methods CAN be instantiated and inherits all four action
    methods from BaseAgent.
    """
    # BaseAgent itself cannot be instantiated
    with pytest.raises(TypeError):
        BaseAgent({})

    # MinimalSubclass can be instantiated with just the three abstract methods
    agent = MinimalSubclass({"type": "test"})
    assert agent is not None

    # All four action methods are inherited (concrete) from BaseAgent
    assert callable(agent.preflight)
    assert callable(agent.design)
    assert callable(agent.develop)
    assert callable(agent.review)

    # _run is the subclass's own implementation
    text, usage = asyncio.run(agent._run("test prompt"))
    assert text == "test output"
    assert usage.input_tokens == 1


def test_base_agent_has_no_abstract_action_methods():
    """preflight/design/develop/review must be concrete (not abstract) on BaseAgent."""
    import inspect
    abstracts = getattr(BaseAgent, "__abstractmethods__", set())
    for method in ("preflight", "design", "develop", "review"):
        assert method not in abstracts, (
            f"{method} should be concrete in BaseAgent, not abstract"
        )


def test_base_agent_abstract_methods_are_run_get_model_get_permission_mode():
    """Only _run, _get_model, and _get_permission_mode are abstract on BaseAgent."""
    abstracts = getattr(BaseAgent, "__abstractmethods__", set())
    assert "_run" in abstracts
    assert "_get_model" in abstracts
    assert "_get_permission_mode" in abstracts


def test_claude_code_agent_has_no_own_build_context():
    """ClaudeCodeAgent must not define its own _build_context (moved to BaseAgent)."""
    from shadowcoder.agents.claude_code import ClaudeCodeAgent
    # _build_context should be inherited from BaseAgent, not defined on ClaudeCodeAgent
    assert "_build_context" not in ClaudeCodeAgent.__dict__


def test_claude_code_agent_has_no_own_extract_comments():
    """ClaudeCodeAgent must not define its own _extract_comments_from_text."""
    from shadowcoder.agents.claude_code import ClaudeCodeAgent
    assert "_extract_comments_from_text" not in ClaudeCodeAgent.__dict__
