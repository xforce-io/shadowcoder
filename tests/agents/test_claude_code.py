import pytest
from unittest.mock import AsyncMock, patch
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.types import AgentRequest, AgentUsage, DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput
from shadowcoder.core.models import Issue, IssueStatus
from datetime import datetime


@pytest.fixture
def agent():
    return ClaudeCodeAgent({"type": "claude_code"})


@pytest.fixture
def sample_request():
    issue = Issue(
        id=1, title="Test", status=IssueStatus.DESIGNING,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )
    return AgentRequest(action="design", issue=issue, context={"worktree_path": "/tmp"})


def _make_usage(input_tokens=100, output_tokens=50, cost_usd=0.001):
    return AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                      duration_ms=500, cost_usd=cost_usd)


async def test_preflight(agent, sample_request):
    agent._run_claude_with_usage = AsyncMock(return_value=(
        '{"feasibility": "high", "estimated_complexity": "complex", "risks": ["r1"]}',
        AgentUsage()))
    result = await agent.preflight(sample_request)
    assert result.feasibility == "high"
    assert len(result.risks) == 1


async def test_design_returns_output(agent, sample_request):
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Design document content here", _make_usage()))
    result = await agent.design(sample_request)
    assert isinstance(result, DesignOutput)
    assert len(result.document) > 0
    assert result.usage is not None
    assert result.usage.input_tokens == 100


async def test_develop_returns_output(agent, sample_request):
    sample_request.action = "develop"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Implementation summary here", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    result = await agent.develop(sample_request)
    assert isinstance(result, DevelopOutput)
    assert len(result.summary) > 0
    assert result.usage is not None
    assert result.usage.output_tokens == 50


async def test_review_returns_result(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    assert result.comments == []
    assert result.reviewer == "claude-code"
    assert result.usage is not None


async def test_review_no_score_or_passed(agent, sample_request):
    """ReviewOutput should not have score or passed fields."""
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert not hasattr(result, "score")
    assert not hasattr(result, "passed")


async def test_review_with_issues(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(return_value=(
        '{"comments": [{"severity": "high", "message": "Missing error handling", "location": "parser.py:45"}], "resolved_item_ids": [], "proposed_tests": []}',
        _make_usage(),
    ))
    result = await agent.review(sample_request)
    assert len(result.comments) == 1
    assert result.comments[0].message == "Missing error handling"
    assert result.comments[0].location == "parser.py:45"


async def test_review_unparseable_json(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("This is not JSON at all", _make_usage()))
    result = await agent.review(sample_request)
    # defaults to HIGH severity comment on parse error
    assert len(result.comments) == 1
    from shadowcoder.agents.types import Severity
    assert result.comments[0].severity == Severity.HIGH


async def test_review_with_code_diff_uses_diff_context(agent, sample_request):
    """When code_diff is in context, _build_review_context is used."""
    sample_request.action = "review"
    sample_request.context["code_diff"] = "diff --git ..."
    agent._run_claude_with_usage = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    # Verify the prompt was built with review context (diff-aware)
    call_args = agent._run_claude_with_usage.call_args
    prompt = call_args[0][0]
    assert "diff" in prompt.lower() or "Git Diff" in prompt


# --- Role instruction tests ---


def test_default_role_instructions(agent):
    """Default role instructions are used when config has no roles."""
    from shadowcoder.agents.claude_code import DEFAULT_ROLE_INSTRUCTIONS
    for role in ("designer", "design_reviewer", "developer", "code_reviewer"):
        assert agent._get_role_instruction(role) == DEFAULT_ROLE_INSTRUCTIONS[role]


def test_custom_role_instruction_overrides_default():
    """Config roles override default instructions."""
    custom = ClaudeCodeAgent({
        "type": "claude_code",
        "roles": {
            "designer": {"instruction": "Custom designer instruction"},
        },
    })
    assert custom._get_role_instruction("designer") == "Custom designer instruction"
    # Other roles still use defaults
    from shadowcoder.agents.claude_code import DEFAULT_ROLE_INSTRUCTIONS
    assert custom._get_role_instruction("developer") == DEFAULT_ROLE_INSTRUCTIONS["developer"]


def test_unknown_role_returns_empty(agent):
    """Unknown role returns empty string."""
    assert agent._get_role_instruction("unknown_role") == ""


async def test_design_prompt_includes_role_instruction(agent, sample_request):
    """Design system prompt includes designer role instruction."""
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Design doc", _make_usage()))
    await agent.design(sample_request)
    call_args = agent._run_claude_with_usage.call_args
    system_prompt = call_args[1].get("system_prompt") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("system_prompt", "")
    from shadowcoder.agents.claude_code import DEFAULT_ROLE_INSTRUCTIONS
    assert DEFAULT_ROLE_INSTRUCTIONS["designer"] in system_prompt


async def test_develop_prompt_includes_role_instruction(agent, sample_request):
    """Develop system prompt includes developer role instruction."""
    sample_request.action = "develop"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_args = agent._run_claude_with_usage.call_args
    system_prompt = call_args[1].get("system_prompt") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("system_prompt", "")
    from shadowcoder.agents.claude_code import DEFAULT_ROLE_INSTRUCTIONS
    assert DEFAULT_ROLE_INSTRUCTIONS["developer"] in system_prompt


async def test_review_design_prompt_includes_role_instruction(agent, sample_request):
    """Design review system prompt includes design_reviewer role instruction."""
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    await agent.review(sample_request)
    call_args = agent._run_claude_with_usage.call_args
    system_prompt = call_args[1].get("system_prompt") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("system_prompt", "")
    from shadowcoder.agents.claude_code import DEFAULT_ROLE_INSTRUCTIONS
    assert DEFAULT_ROLE_INSTRUCTIONS["design_reviewer"] in system_prompt


async def test_review_code_prompt_includes_role_instruction(agent, sample_request):
    """Code review system prompt includes code_reviewer role instruction."""
    sample_request.action = "review"
    sample_request.context["code_diff"] = "diff --git ..."
    agent._run_claude_with_usage = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    await agent.review(sample_request)
    call_args = agent._run_claude_with_usage.call_args
    system_prompt = call_args[1].get("system_prompt") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("system_prompt", "")
    from shadowcoder.agents.claude_code import DEFAULT_ROLE_INSTRUCTIONS
    assert DEFAULT_ROLE_INSTRUCTIONS["code_reviewer"] in system_prompt


def test_agent_usage_has_phase_and_round():
    usage = AgentUsage(input_tokens=100, output_tokens=50, duration_ms=500,
                       cost_usd=0.01, phase="develop", round_num=2)
    assert usage.phase == "develop"
    assert usage.round_num == 2


def test_agent_usage_defaults():
    usage = AgentUsage()
    assert usage.phase == ""
    assert usage.round_num == 0


async def test_develop_passes_session_id(agent, sample_request):
    """develop() forwards session_id to _run_claude_with_usage."""
    sample_request.action = "develop"
    sample_request.context["session_id"] = "test-uuid-1234"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run_claude_with_usage.call_args[1]
    assert call_kwargs.get("session_id") == "test-uuid-1234"


async def test_develop_passes_resume_id(agent, sample_request):
    """develop() forwards resume_id to _run_claude_with_usage."""
    sample_request.action = "develop"
    sample_request.context["resume_id"] = "test-uuid-5678"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run_claude_with_usage.call_args[1]
    assert call_kwargs.get("resume_id") == "test-uuid-5678"


async def test_develop_no_session_by_default(agent, sample_request):
    """develop() without session context passes no session params."""
    sample_request.action = "develop"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run_claude_with_usage.call_args[1]
    assert call_kwargs.get("session_id") is None
    assert call_kwargs.get("resume_id") is None
