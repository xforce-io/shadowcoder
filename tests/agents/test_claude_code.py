import pytest
from unittest.mock import AsyncMock, patch
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.types import AgentRequest, AgentUsage, DesignOutput, DevelopOutput, ReviewOutput, TestOutput
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
        return_value=('{"passed": true, "comments": []}', _make_usage()))
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    assert result.passed is True
    assert result.reviewer == "claude-code"
    assert result.usage is not None


async def test_review_with_issues(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(return_value=(
        '{"passed": false, "comments": [{"severity": "high", "message": "Missing error handling", "location": "parser.py:45"}]}',
        _make_usage(),
    ))
    result = await agent.review(sample_request)
    assert not result.passed
    assert len(result.comments) == 1
    assert result.comments[0].message == "Missing error handling"
    assert result.comments[0].location == "parser.py:45"


async def test_review_unparseable_json(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("This is not JSON at all", _make_usage()))
    result = await agent.review(sample_request)
    assert not result.passed  # defaults to not passed on parse error


async def test_test_pass(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("All tests passed\nRESULT: PASS", _make_usage()))
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is True
    assert result.usage is not None


async def test_test_fail_with_recommendation(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("3 tests failed\nRESULT: FAIL recommendation=develop", _make_usage()))
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is False
    assert result.recommendation == "develop"


async def test_test_fail_design_recommendation(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Missing feature\nRESULT: FAIL recommendation=design", _make_usage()))
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is False
    assert result.recommendation == "design"
