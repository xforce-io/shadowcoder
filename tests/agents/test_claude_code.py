import pytest
from unittest.mock import AsyncMock, patch
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.types import AgentRequest, DesignOutput, DevelopOutput, ReviewOutput, TestOutput
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


async def test_design_returns_output(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="Design document content here")
    result = await agent.design(sample_request)
    assert isinstance(result, DesignOutput)
    assert len(result.document) > 0


async def test_develop_returns_output(agent, sample_request):
    sample_request.action = "develop"
    agent._run_claude = AsyncMock(return_value="Implementation summary here")
    agent._get_files_changed = AsyncMock(return_value=[])
    result = await agent.develop(sample_request)
    assert isinstance(result, DevelopOutput)
    assert len(result.summary) > 0


async def test_review_returns_result(agent, sample_request):
    sample_request.action = "review"
    agent._run_claude = AsyncMock(return_value='{"passed": true, "comments": []}')
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
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
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is True


async def test_test_fail_with_recommendation(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude = AsyncMock(
        return_value="3 tests failed\nRESULT: FAIL recommendation=develop")
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is False
    assert result.recommendation == "develop"


async def test_test_fail_design_recommendation(agent, sample_request):
    sample_request.action = "test"
    agent._run_claude = AsyncMock(
        return_value="Missing feature\nRESULT: FAIL recommendation=design")
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is False
    assert result.recommendation == "design"
