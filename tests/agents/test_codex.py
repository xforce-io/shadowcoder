"""Tests for CodexAgent.

Covers AC5-AC10 plus acceptance tests:
- test_codex_agents_md_restore_on_exception (acceptance test)
- test_codex_jsonl_malformed_lines (acceptance test)
"""
from __future__ import annotations

import asyncio
import json
import logging
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from shadowcoder.agents.codex import CodexAgent
from shadowcoder.agents.types import (
    AgentRequest, AgentUsage, DesignOutput, DevelopOutput,
    PreflightOutput, ReviewOutput,
)
from shadowcoder.core.models import Issue, IssueStatus


@pytest.fixture
def agent():
    return CodexAgent({"type": "codex", "model": "o3"})


@pytest.fixture
def sample_request(tmp_path):
    issue = Issue(
        id=1, title="Test", status=IssueStatus.DESIGNING,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )
    return AgentRequest(action="design", issue=issue, context={"worktree_path": str(tmp_path)})


def _make_jsonl(text="Agent response text", input_tokens=100, output_tokens=50) -> str:
    """Build a standard JSONL response from codex."""
    lines = [
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}),
    ]
    return "\n".join(lines)


def _mock_proc(jsonl: str, returncode: int = 0):
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.communicate = AsyncMock(return_value=(jsonl.encode(), b""))
    return mock_proc


# ------------------------------------------------------------------ #
#  AC5: Command construction                                          #
# ------------------------------------------------------------------ #

async def test_run_builds_exec_command(agent, tmp_path):
    """CodexAgent._run() correctly builds codex exec command with proper flags."""
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await agent._run("test prompt", cwd=str(tmp_path))

    cmd = mock_exec.call_args[0]
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    assert "--json" in cmd
    assert "-m" in cmd
    assert "o3" in cmd
    assert "--full-auto" in cmd
    assert "-C" in cmd
    assert str(tmp_path) in cmd
    assert cmd[-1] == "-"  # stdin sentinel


async def test_run_prompt_sent_via_stdin(agent, tmp_path):
    """Prompt is passed via stdin, not as a CLI argument."""
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await agent._run("my prompt text", cwd=str(tmp_path))

    # The communicate call should pass the prompt as input bytes
    communicate_call = mock_proc.communicate.call_args
    assert communicate_call[1]["input"] == b"my prompt text"


# ------------------------------------------------------------------ #
#  AC9: Permission mode mapping                                       #
# ------------------------------------------------------------------ #

async def test_run_permission_mode_auto(agent, tmp_path):
    """'auto' permission mode → --full-auto flag."""
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await agent._run("prompt", cwd=str(tmp_path))

    cmd = mock_exec.call_args[0]
    assert "--full-auto" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


async def test_run_permission_mode_bypass(tmp_path):
    """'bypass' permission mode → --dangerously-bypass-approvals-and-sandbox flag."""
    agent = CodexAgent({"type": "codex", "model": "o3", "permission_mode": "bypass"})
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await agent._run("prompt", cwd=str(tmp_path))

    cmd = mock_exec.call_args[0]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--full-auto" not in cmd


# ------------------------------------------------------------------ #
#  AC6: JSONL output parsing                                         #
# ------------------------------------------------------------------ #

async def test_run_parses_jsonl_output(agent, tmp_path):
    """CodexAgent._run() parses JSONL to extract text and usage."""
    jsonl = _make_jsonl(text="Hello world", input_tokens=200, output_tokens=75)
    mock_proc = _mock_proc(jsonl)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        text, usage = await agent._run("prompt", cwd=str(tmp_path))

    assert text == "Hello world"
    assert usage.input_tokens == 200
    assert usage.output_tokens == 75
    assert usage.cost_usd is None  # F3: Codex doesn't provide cost_usd


async def test_run_concatenates_multiple_agent_messages(agent, tmp_path):
    """Multiple agent_message events are concatenated in order."""
    jsonl = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Part 1 "}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Part 2"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}),
    ])
    mock_proc = _mock_proc(jsonl)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        text, _ = await agent._run("prompt", cwd=str(tmp_path))

    assert text == "Part 1 Part 2"


async def test_run_handles_empty_jsonl(agent, tmp_path):
    """Graceful fallback when JSONL has no agent_message events."""
    jsonl = "\n".join([
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}}),
    ])
    mock_proc = _mock_proc(jsonl)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        text, usage = await agent._run("prompt", cwd=str(tmp_path))

    # No agent_message events → fall back to raw stdout
    assert text == jsonl
    assert usage.input_tokens == 5
    assert usage.output_tokens == 3


async def test_run_accumulates_usage_from_multiple_turns(agent, tmp_path):
    """Usage tokens are accumulated from multiple turn.completed events."""
    jsonl = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "response"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}),
    ])
    mock_proc = _mock_proc(jsonl)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        _, usage = await agent._run("prompt", cwd=str(tmp_path))

    assert usage.input_tokens == 30
    assert usage.output_tokens == 15


# ------------------------------------------------------------------ #
#  AC3 (acceptance): malformed JSONL lines                           #
# ------------------------------------------------------------------ #

async def test_codex_jsonl_malformed_lines(agent, tmp_path):
    """Feed JSONL with valid, empty, and malformed lines — graceful degradation.

    Acceptance test: valid events parsed, malformed lines skipped with warning,
    result text collected from valid agent_message events.
    """
    jsonl = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Valid text"}}),
        "",                          # empty line — should be silently skipped
        "not valid json {{{",        # malformed — should be warned and skipped
        '{"truncated": true',        # truncated JSON — should be warned and skipped
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}),
    ])
    mock_proc = _mock_proc(jsonl)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        text, usage = await agent._run("prompt", cwd=str(tmp_path))

    # Valid events parsed, malformed lines skipped
    assert text == "Valid text"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5


async def test_parse_jsonl_malformed_lines_logged(agent, caplog):
    """Malformed JSONL lines emit a WARNING log."""
    raw = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
        "{{bad json}}",
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    with caplog.at_level(logging.WARNING, logger="shadowcoder.agents.codex"):
        text, _ = agent._parse_jsonl(raw, duration_ms=100)

    assert text == "ok"
    assert any("malformed" in record.message.lower() for record in caplog.records)


# ------------------------------------------------------------------ #
#  AC7: AGENTS.md injection                                          #
# ------------------------------------------------------------------ #

async def test_run_writes_agents_md(agent, tmp_path):
    """Creates AGENTS.md with system_prompt content before exec."""
    captured_content = None

    async def capture_exec(*args, **kwargs):
        agents_md = tmp_path / "AGENTS.md"
        nonlocal captured_content
        if agents_md.exists():
            captured_content = agents_md.read_text()
        mock_proc = _mock_proc(_make_jsonl())
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_exec):
        await agent._run("prompt", cwd=str(tmp_path), system_prompt="System instructions")

    assert captured_content == "System instructions"


async def test_run_cleans_agents_md_no_original(agent, tmp_path):
    """Deletes AGENTS.md after exec if none existed before."""
    assert not (tmp_path / "AGENTS.md").exists()

    mock_proc = _mock_proc(_make_jsonl())
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await agent._run("prompt", cwd=str(tmp_path), system_prompt="System instructions")

    assert not (tmp_path / "AGENTS.md").exists()


async def test_run_restores_agents_md(agent, tmp_path):
    """Restores original AGENTS.md content after exec."""
    original_content = "# Original AGENTS.md\n\nSome content."
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(original_content)

    mock_proc = _mock_proc(_make_jsonl())
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await agent._run("prompt", cwd=str(tmp_path), system_prompt="System instructions")

    assert agents_md.exists()
    assert agents_md.read_text() == original_content


async def test_run_prepends_system_prompt_to_existing_agents_md(agent, tmp_path):
    """When AGENTS.md exists, system_prompt is prepended with separator."""
    original_content = "# Existing content"
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(original_content)
    written_content = None

    async def capture_exec(*args, **kwargs):
        nonlocal written_content
        written_content = agents_md.read_text()
        return _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", side_effect=capture_exec):
        await agent._run("prompt", cwd=str(tmp_path), system_prompt="System prompt")

    assert written_content is not None
    assert "System prompt" in written_content
    assert "# Existing content" in written_content
    assert "---" in written_content  # separator present


async def test_run_no_agents_md_when_no_system_prompt(agent, tmp_path):
    """No AGENTS.md is written when system_prompt is not provided."""
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await agent._run("prompt", cwd=str(tmp_path))

    assert not (tmp_path / "AGENTS.md").exists()


# ------------------------------------------------------------------ #
#  AC2 (acceptance): AGENTS.md restore on exception                  #
# ------------------------------------------------------------------ #

async def test_codex_agents_md_restore_on_exception(tmp_path):
    """AGENTS.md is restored to its original content when the subprocess
    raises an exception (e.g. TimeoutError or OSError).

    Acceptance test: original AGENTS.md content is restored even when
    _run raises TimeoutError.
    """
    agent = CodexAgent({"type": "codex", "model": "o3"})
    original_content = "# Original"
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(original_content)

    # Mock _run_subprocess to raise TimeoutError (simulating subprocess timeout)
    with patch.object(agent, "_run_subprocess", side_effect=asyncio.TimeoutError("timed out")):
        with pytest.raises((RuntimeError, asyncio.TimeoutError)):
            await agent._run("prompt", cwd=str(tmp_path), system_prompt="Injected system prompt")

    # AGENTS.md must be restored to its original content
    assert agents_md.exists()
    assert agents_md.read_text() == original_content


async def test_codex_agents_md_cleaned_on_exception_no_original(tmp_path):
    """AGENTS.md is deleted when it didn't exist originally, even on exception."""
    agent = CodexAgent({"type": "codex", "model": "o3"})
    assert not (tmp_path / "AGENTS.md").exists()

    with patch.object(agent, "_run_subprocess", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await agent._run("prompt", cwd=str(tmp_path), system_prompt="Injected")

    # AGENTS.md should be cleaned up
    assert not (tmp_path / "AGENTS.md").exists()


# ------------------------------------------------------------------ #
#  System prompt must not be silently dropped when cwd is None       #
# ------------------------------------------------------------------ #

async def test_run_cwd_none_system_prompt_prepended(agent):
    """When cwd=None, system_prompt cannot use AGENTS.md.
    It must be prepended to the prompt so the model still receives it."""
    captured_prompt = None
    mock_proc = _mock_proc(_make_jsonl())

    async def capture_exec(*args, **kwargs):
        nonlocal captured_prompt
        # args[0] is 'codex', rest are flags, last is '-' for stdin
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_exec) as mock_exec:
        # Capture the stdin input sent to the subprocess
        original_communicate = mock_proc.communicate

        async def capture_communicate(input=None):
            nonlocal captured_prompt
            if input:
                captured_prompt = input.decode("utf-8")
            return await original_communicate(input=input)

        mock_proc.communicate = capture_communicate
        await agent._run("user prompt here", cwd=None, system_prompt="You are a JSON bot.")

    assert captured_prompt is not None
    assert "You are a JSON bot." in captured_prompt, (
        "system_prompt must reach the model even when cwd=None; "
        "got prompt: " + (captured_prompt or "<empty>")
    )


# ------------------------------------------------------------------ #
#  AC8: Inherited actions work via mocked _run()                     #
# ------------------------------------------------------------------ #

async def test_inherited_actions_preflight(agent, sample_request):
    """preflight() is inherited from BaseAgent and works via mocked _run()."""
    agent._run = AsyncMock(return_value=(
        '{"feasibility": "high", "estimated_complexity": "moderate", "risks": []}',
        AgentUsage(input_tokens=10, output_tokens=5),
    ))
    result = await agent.preflight(sample_request)
    assert isinstance(result, PreflightOutput)
    assert result.feasibility == "high"
    assert result.estimated_complexity == "moderate"


async def test_inherited_actions_design(agent, sample_request):
    """design() is inherited from BaseAgent and works via mocked _run()."""
    agent._run = AsyncMock(return_value=(
        'Design document\n```yaml\ntest_command: pytest -v\n```',
        AgentUsage(),
    ))
    result = await agent.design(sample_request)
    assert isinstance(result, DesignOutput)
    assert "Design document" in result.document
    assert result.test_command == "pytest -v"


async def test_inherited_actions_develop(agent, sample_request):
    """develop() is inherited from BaseAgent and works via mocked _run()."""
    agent._run = AsyncMock(return_value=("Implementation done", AgentUsage()))
    agent._get_files_changed = AsyncMock(return_value=["src/foo.py"])
    result = await agent.develop(sample_request)
    assert isinstance(result, DevelopOutput)
    assert result.summary == "Implementation done"
    assert result.files_changed == ["src/foo.py"]


async def test_inherited_actions_review(agent, sample_request):
    """review() is inherited from BaseAgent and works via mocked _run()."""
    agent._run = AsyncMock(return_value=(
        '{"comments": [], "resolved_item_ids": [], "proposed_tests": []}',
        AgentUsage(),
    ))
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    assert result.reviewer == "codex"  # uses config type


# ------------------------------------------------------------------ #
#  AC10: Registry integration                                         #
# ------------------------------------------------------------------ #

async def test_registry_codex(tmp_config):
    """CodexAgent can be registered and instantiated from config."""
    from shadowcoder.agents.registry import AgentRegistry
    from shadowcoder.agents.codex import CodexAgent as CA
    from shadowcoder.core.config import Config

    AgentRegistry.register("codex", CA)
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    agent = registry.get("codex")
    assert isinstance(agent, CA)


# ------------------------------------------------------------------ #
#  Retry logic                                                        #
# ------------------------------------------------------------------ #

async def test_run_retry_on_failure(agent, tmp_path):
    """3 retries on subprocess failure, then RuntimeError."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error output"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="3 attempts"):
                await agent._run("prompt", cwd=str(tmp_path))


async def test_run_succeeds_on_second_attempt(agent, tmp_path):
    """Succeeds if second attempt returns rc=0."""
    fail_proc = AsyncMock()
    fail_proc.returncode = 1
    fail_proc.communicate = AsyncMock(return_value=(b"", b"temporary error"))

    success_proc = _mock_proc(_make_jsonl(text="success"))

    call_count = 0

    async def make_proc(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return fail_proc
        return success_proc

    with patch("asyncio.create_subprocess_exec", side_effect=make_proc):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            text, _ = await agent._run("prompt", cwd=str(tmp_path))

    assert text == "success"
    assert call_count == 2


# ------------------------------------------------------------------ #
#  Session resume warning                                             #
# ------------------------------------------------------------------ #

async def test_run_session_resume_warning(agent, tmp_path, caplog):
    """resume_id logs a warning and runs a fresh session (MVP limitation)."""
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with caplog.at_level(logging.WARNING, logger="shadowcoder.agents.codex"):
            text, _ = await agent._run("prompt", cwd=str(tmp_path), resume_id="abc-123")

    assert any("resume" in r.message.lower() for r in caplog.records)
    assert text == "Agent response text"


async def test_run_session_id_warning(agent, tmp_path, caplog):
    """session_id logs a warning (not supported by codex CLI)."""
    mock_proc = _mock_proc(_make_jsonl())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with caplog.at_level(logging.WARNING, logger="shadowcoder.agents.codex"):
            await agent._run("prompt", cwd=str(tmp_path), session_id="sess-xyz")

    assert any("session" in r.message.lower() for r in caplog.records)


# ------------------------------------------------------------------ #
#  _parse_jsonl unit tests                                            #
# ------------------------------------------------------------------ #

def test_parse_jsonl_basic(agent):
    raw = _make_jsonl(text="hello", input_tokens=10, output_tokens=5)
    text, usage = agent._parse_jsonl(raw, duration_ms=100)
    assert text == "hello"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cost_usd is None


def test_parse_jsonl_ignores_non_agent_message_items(agent):
    raw = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "tool_call", "text": "ignored"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "kept"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    text, _ = agent._parse_jsonl(raw, duration_ms=0)
    assert text == "kept"


def test_parse_jsonl_empty_raw_fallback(agent):
    """Completely empty output returns empty string."""
    text, usage = agent._parse_jsonl("", duration_ms=0)
    assert text == ""
    assert usage.input_tokens == 0
