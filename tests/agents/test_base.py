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


class TestExtractBashScript:
    """Tests for BaseAgent._extract_bash_script — handling model output formats."""

    def test_plain_script(self):
        raw = "#!/bin/bash\nset -euo pipefail\necho hello\n"
        assert BaseAgent._extract_bash_script(raw) == raw.strip()

    def test_fenced_block_only(self):
        raw = "```bash\n#!/bin/bash\necho hello\n```"
        result = BaseAgent._extract_bash_script(raw)
        assert result == "#!/bin/bash\necho hello"

    def test_commentary_before_fenced_block(self):
        """The exact failure mode seen with Sonnet — natural language before the code block."""
        raw = (
            "The script is ready. Here it is:\n\n"
            "```bash\n"
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "echo test\n"
            "```\n\n"
            "This tests the feature.\n"
        )
        result = BaseAgent._extract_bash_script(raw)
        assert result.startswith("#!/bin/bash")
        assert "The script is ready" not in result
        assert "This tests the feature" not in result
        assert "echo test" in result

    def test_commentary_with_nested_fences(self):
        """Model outputs commentary + real script in separate fenced blocks."""
        raw = (
            "I just need to output it:\n\n"
            "```bash\n"
            "#!/bin/bash\nset -euo pipefail\necho short\n"
            "```\n\n"
            "But here is the full script:\n\n"
            "```bash\n"
            "#!/bin/bash\nset -euo pipefail\n"
            "TMPDIR=$(mktemp -d)\n"
            "trap 'rm -rf $TMPDIR' EXIT\n"
            "echo long test\n"
            "```\n"
        )
        result = BaseAgent._extract_bash_script(raw)
        # Should pick the longest block
        assert "TMPDIR=$(mktemp -d)" in result
        assert "I just need" not in result

    def test_bare_fence_no_lang(self):
        raw = "```\n#!/bin/bash\necho ok\n```"
        result = BaseAgent._extract_bash_script(raw)
        assert result == "#!/bin/bash\necho ok"

    def test_no_shebang_gets_one(self):
        raw = "```bash\necho hello\n```"
        result = BaseAgent._extract_bash_script(raw)
        assert result.startswith("#!/bin/bash\nset -euo pipefail\n")
        assert "echo hello" in result

    def test_no_fence_no_shebang(self):
        raw = "echo hello world"
        result = BaseAgent._extract_bash_script(raw)
        assert result.startswith("#!/bin/bash")
        assert "echo hello world" in result


class TestFindWrittenScript:
    """Tests for BaseAgent._find_written_script — detecting agent-written files."""

    def test_finds_acceptance_test_sh(self, tmp_path):
        script = "#!/bin/bash\nset -euo pipefail\necho test\n"
        (tmp_path / "acceptance_test.sh").write_text(script)
        result = BaseAgent._find_written_script(str(tmp_path))
        assert result == script.strip()
        # File should be cleaned up
        assert not (tmp_path / "acceptance_test.sh").exists()

    def test_finds_acceptance_sh(self, tmp_path):
        script = "#!/bin/bash\necho ok\n"
        (tmp_path / "acceptance.sh").write_text(script)
        result = BaseAgent._find_written_script(str(tmp_path))
        assert result == script.strip()

    def test_ignores_non_script_file(self, tmp_path):
        (tmp_path / "acceptance_test.sh").write_text("just some text\n")
        result = BaseAgent._find_written_script(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_file(self, tmp_path):
        result = BaseAgent._find_written_script(str(tmp_path))
        assert result is None

    def test_returns_none_when_cwd_is_none(self):
        result = BaseAgent._find_written_script(None)
        assert result is None

    def test_priority_order(self, tmp_path):
        """acceptance_test.sh is checked before acceptance.sh."""
        (tmp_path / "acceptance_test.sh").write_text("#!/bin/bash\necho first\n")
        (tmp_path / "acceptance.sh").write_text("#!/bin/bash\necho second\n")
        result = BaseAgent._find_written_script(str(tmp_path))
        assert "echo first" in result


def test_review_context_includes_acceptance_script():
    """When acceptance_script and gate_failure_output are in context,
    _build_review_context includes them in the output string."""
    from datetime import datetime
    from shadowcoder.core.models import Issue, IssueStatus
    from shadowcoder.agents.types import AgentRequest

    issue = Issue(
        id=1,
        title="test",
        status=IssueStatus.DEVELOPING,
        priority="normal",
        created=datetime.now(),
        updated=datetime.now(),
        sections={"需求": "implement foo"},
    )

    request = AgentRequest(action="review", issue=issue, context={
        "code_diff": "diff --git a/foo.py",
        "acceptance_script": "#!/bin/bash\nassert something\n",
        "gate_failure_output": "Expected ValueError for '1 - - 2'",
    })

    agent = MinimalSubclass(config={"type": "test"})
    ctx = agent._build_review_context(request)
    assert "#!/bin/bash" in ctx
    assert "assert something" in ctx
    assert "Expected ValueError" in ctx
