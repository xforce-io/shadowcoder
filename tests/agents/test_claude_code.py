import pytest
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
    resp = await agent.execute(sample_request)
    assert resp.success is True
    assert isinstance(resp.content, str)
    assert len(resp.content) > 0


async def test_review_returns_result(agent, sample_request):
    sample_request.action = "review"
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewResult)
    assert isinstance(result.passed, bool)
    assert result.reviewer == "claude-code"
