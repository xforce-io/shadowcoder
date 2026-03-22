import pytest
from unittest.mock import AsyncMock, patch
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.base import AgentRequest
from shadowcoder.core.models import Issue, IssueStatus, ReviewResult
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


async def test_execute_returns_response(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="Design document content here")
    resp = await agent.execute(sample_request)
    assert resp.success is True
    assert isinstance(resp.content, str)
    assert len(resp.content) > 0


async def test_review_returns_result(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude = AsyncMock(return_value='{"passed": true, "comments": []}')
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewResult)
    assert result.passed is True
    assert result.reviewer == "claude-code"


async def test_review_with_issues(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude = AsyncMock(return_value='{"passed": false, "comments": [{"severity": "high", "message": "Missing error handling", "location": "parser.py:45"}]}')
    result = await agent.review(sample_request)
    assert not result.passed
    assert len(result.comments) == 1
    assert result.comments[0].message == "Missing error handling"
    assert result.comments[0].location == "parser.py:45"


async def test_review_unparseable_json(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude = AsyncMock(return_value="This is not JSON at all")
    result = await agent.review(sample_request)
    assert not result.passed  # defaults to not passed on parse error


async def test_test_pass(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude = AsyncMock(return_value="All tests passed\nRESULT: PASS")
    resp = await agent.execute(sample_request)
    assert resp.success is True


async def test_test_fail_with_recommendation(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude = AsyncMock(
        return_value="3 tests failed\nRESULT: FAIL recommendation=develop")
    resp = await agent.execute(sample_request)
    assert resp.success is False
    assert resp.metadata["recommendation"] == "develop"


async def test_test_fail_design_recommendation(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude = AsyncMock(
        return_value="Missing feature\nRESULT: FAIL recommendation=design")
    resp = await agent.execute(sample_request)
    assert resp.success is False
    assert resp.metadata["recommendation"] == "design"
