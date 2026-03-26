import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.types import AgentRequest, AgentUsage, DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput, Severity
from shadowcoder.core.models import Issue, IssueStatus
from datetime import datetime

# data/roles/ in the repo — seed roles used by tests
_DATA_ROLES_DIR = str(Path(__file__).resolve().parent.parent.parent / "data" / "roles")


@pytest.fixture
def agent():
    return ClaudeCodeAgent({"type": "claude_code", "_roles_dirs": [_DATA_ROLES_DIR]})


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
    agent._run = AsyncMock(return_value=(
        '{"feasibility": "high", "estimated_complexity": "complex", "risks": ["r1"]}',
        AgentUsage()))
    result = await agent.preflight(sample_request)
    assert result.feasibility == "high"
    assert len(result.risks) == 1


async def test_design_returns_output(agent, sample_request):
    agent._run = AsyncMock(
        return_value=("Design document content here", _make_usage()))
    result = await agent.design(sample_request)
    assert isinstance(result, DesignOutput)
    assert len(result.document) > 0
    assert result.usage is not None
    assert result.usage.input_tokens == 100


async def test_develop_returns_output(agent, sample_request):
    sample_request.action = "develop"
    agent._run = AsyncMock(
        return_value=("Implementation summary here", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    result = await agent.develop(sample_request)
    assert isinstance(result, DevelopOutput)
    assert len(result.summary) > 0
    assert result.usage is not None
    assert result.usage.output_tokens == 50


async def test_review_returns_result(agent, sample_request):
    sample_request.action = "review"
    agent._run = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    assert result.comments == []
    # reviewer uses config type (F7: intentional change from "claude-code" to "claude_code")
    assert result.reviewer == "claude_code"
    assert result.usage is not None


async def test_review_no_score_or_passed(agent, sample_request):
    """ReviewOutput should not have score or passed fields."""
    sample_request.action = "review"
    agent._run = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert not hasattr(result, "score")
    assert not hasattr(result, "passed")


async def test_review_with_issues(agent, sample_request):
    sample_request.action = "review"
    agent._run = AsyncMock(return_value=(
        '{"comments": [{"severity": "high", "message": "Missing error handling", "location": "parser.py:45"}], "resolved_item_ids": [], "proposed_tests": []}',
        _make_usage(),
    ))
    result = await agent.review(sample_request)
    assert len(result.comments) == 1
    assert result.comments[0].message == "Missing error handling"
    assert result.comments[0].location == "parser.py:45"


async def test_review_unparseable_json(agent, sample_request):
    sample_request.action = "review"
    agent._run = AsyncMock(
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
    agent._run = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    # Verify the prompt was built with review context (diff-aware)
    call_args = agent._run.call_args
    prompt = call_args[0][0]
    assert "diff" in prompt.lower() or "Git Diff" in prompt


# --- System prompt loading tests ---


def test_load_system_prompt_from_data_roles(agent):
    """data/roles/ instructions.md files are loaded for all roles."""
    for role in ("designer", "design_reviewer", "developer", "code_reviewer", "preflight"):
        prompt = agent._load_system_prompt(role)
        assert len(prompt) > 0, f"No prompt found for {role}"


def test_load_system_prompt_unknown_role(agent):
    """Unknown role returns empty string."""
    assert agent._load_system_prompt("unknown_role") == ""


def test_load_system_prompt_project_override(tmp_path):
    """Project-level roles override built-in ones."""
    role_dir = tmp_path / "roles" / "designer"
    role_dir.mkdir(parents=True)
    (role_dir / "instructions.md").write_text("Custom project designer instructions")
    custom = ClaudeCodeAgent({"type": "claude_code", "_roles_dirs": [str(tmp_path / "roles")]})
    assert custom._load_system_prompt("designer") == "Custom project designer instructions"


def test_load_system_prompt_multiple_md_files(tmp_path):
    """Multiple .md files in a role dir are concatenated in sorted order."""
    role_dir = tmp_path / "roles" / "developer"
    role_dir.mkdir(parents=True)
    (role_dir / "01_persona.md").write_text("You are a developer.")
    (role_dir / "02_rules.md").write_text("Follow these rules.")
    agent = ClaudeCodeAgent({"type": "claude_code", "_roles_dirs": [str(tmp_path / "roles")]})
    prompt = agent._load_system_prompt("developer")
    assert "You are a developer." in prompt
    assert "Follow these rules." in prompt
    assert prompt.index("You are a developer.") < prompt.index("Follow these rules.")


async def test_design_prompt_loads_from_file(agent, sample_request):
    """Design system prompt is loaded from designer instructions file."""
    agent._run = AsyncMock(
        return_value=("Design doc", _make_usage()))
    await agent.design(sample_request)
    system_prompt = agent._run.call_args[1].get("system_prompt", "")
    # Check content from designer/instructions.md
    assert "资深系统架构师" in system_prompt
    assert "test_command" in system_prompt


async def test_develop_prompt_loads_from_file(agent, sample_request):
    """Develop system prompt is loaded from developer instructions file."""
    sample_request.action = "develop"
    agent._run = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    system_prompt = agent._run.call_args[1].get("system_prompt", "")
    assert "务实的高级工程师" in system_prompt


async def test_review_design_prompt_loads_from_file(agent, sample_request):
    """Design review system prompt is loaded from design_reviewer instructions file."""
    sample_request.action = "review"
    agent._run = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    await agent.review(sample_request)
    system_prompt = agent._run.call_args[1].get("system_prompt", "")
    assert "架构评审专家" in system_prompt


async def test_review_code_prompt_loads_from_file(agent, sample_request):
    """Code review system prompt is loaded from code_reviewer instructions file."""
    sample_request.action = "review"
    sample_request.context["code_diff"] = "diff --git ..."
    agent._run = AsyncMock(
        return_value=('{"comments": [], "resolved_item_ids": [], "proposed_tests": []}', _make_usage()))
    await agent.review(sample_request)
    system_prompt = agent._run.call_args[1].get("system_prompt", "")
    assert "代码评审专家" in system_prompt


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
    """develop() forwards session_id to _run."""
    sample_request.action = "develop"
    sample_request.context["session_id"] = "test-uuid-1234"
    agent._run = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run.call_args[1]
    assert call_kwargs.get("session_id") == "test-uuid-1234"


async def test_develop_passes_resume_id(agent, sample_request):
    """develop() forwards resume_id to _run."""
    sample_request.action = "develop"
    sample_request.context["resume_id"] = "test-uuid-5678"
    agent._run = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run.call_args[1]
    assert call_kwargs.get("resume_id") == "test-uuid-5678"


async def test_develop_no_session_by_default(agent, sample_request):
    """develop() without session context passes no session params."""
    sample_request.action = "develop"
    agent._run = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run.call_args[1]
    assert call_kwargs.get("session_id") is None
    assert call_kwargs.get("resume_id") is None


# --- _extract_comments_from_text tests ---


def sample_request_factory():
    """Create a sample request for standalone tests."""
    issue = Issue(
        id=1, title="Test", status=IssueStatus.DESIGNING,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )
    return AgentRequest(action="review", issue=issue, context={"worktree_path": "/tmp"})


async def test_review_extract_numbered_chinese():
    agent = ClaudeCodeAgent({"type": "claude_code"})
    text = (
        '1. **标签模式错误**：代码用了【think】但实际是<think>。严重性：high。\n'
        '2. **清理时机**：在eval前清理是合理的。严重性：medium。'
    )
    comments = agent._extract_comments_from_text(text)
    assert len(comments) == 2
    assert comments[0].severity == Severity.HIGH
    assert comments[1].severity == Severity.MEDIUM


async def test_review_extract_bracketed_severity():
    agent = ClaudeCodeAgent({"type": "claude_code"})
    text = "- [HIGH] Think tag pattern is wrong\n- [MEDIUM] Cleanup timing issue"
    comments = agent._extract_comments_from_text(text)
    assert len(comments) == 2
    assert comments[0].severity == Severity.HIGH
    assert "Think tag" in comments[0].message


async def test_review_extract_no_structure():
    agent = ClaudeCodeAgent({"type": "claude_code"})
    comments = agent._extract_comments_from_text("Some random thoughts about code quality")
    assert comments == []


async def test_review_extract_default_severity():
    agent = ClaudeCodeAgent({"type": "claude_code"})
    text = "1. Missing error handling for edge case\n2. Variable naming unclear"
    comments = agent._extract_comments_from_text(text)
    assert len(comments) == 2
    assert all(c.severity == Severity.MEDIUM for c in comments)


async def test_review_fallback_preserves_full_text():
    agent = ClaudeCodeAgent({"type": "claude_code"})
    long_text = "A" * 500
    agent._run = AsyncMock(return_value=(long_text, AgentUsage(input_tokens=100, output_tokens=50, duration_ms=500, cost_usd=0.001)))
    result = await agent.review(sample_request_factory())
    assert len(result.comments) == 1
    assert long_text in result.comments[0].message


def test_extract_test_command_from_design():
    doc = '# Design\n\nSome content.\n\n```yaml\ntest_command: "make -C bkn/bkn-backend test"\n```\n'
    assert ClaudeCodeAgent._extract_test_command(doc) == "make -C bkn/bkn-backend test"


def test_extract_test_command_no_quotes():
    doc = '# Design\n\n```yaml\ntest_command: go test ./...\n```\n'
    assert ClaudeCodeAgent._extract_test_command(doc) == "go test ./..."


def test_extract_test_command_missing():
    doc = '# Design\n\nNo metadata block.'
    assert ClaudeCodeAgent._extract_test_command(doc) is None
