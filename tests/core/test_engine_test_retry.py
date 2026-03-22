"""Tests for Engine._on_test retry loop with recommendation routing."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from shadowcoder.core.engine import Engine
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus
from shadowcoder.core.config import Config
from shadowcoder.agents.types import (
    AgentRequest, AgentActionFailed,
    DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
    ReviewComment, Severity,
)


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def config(tmp_config):
    return Config(str(tmp_config))


@pytest.fixture
def store(tmp_repo, config):
    return IssueStore(str(tmp_repo), config)


@pytest.fixture
def mock_worktree():
    wt = AsyncMock()
    wt.create = AsyncMock(return_value="/tmp/wt")
    return wt


@pytest.fixture
def task_mgr(mock_worktree):
    return TaskManager(mock_worktree)


def make_engine(bus, store, task_mgr, registry, config, repo_path="/tmp/repo"):
    return Engine(bus, store, task_mgr, registry, config, repo_path)


def _setup_issue_at_testing(store):
    """Create an issue and transition it to TESTING status."""
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)
    store.transition_status(1, IssueStatus.DEV_REVIEW)
    store.transition_status(1, IssueStatus.TESTING)


async def test_test_retry_with_develop_recommendation(bus, store, task_mgr, config):
    """Test fails with recommendation=develop → auto develop → re-test → pass."""
    test_call_count = 0

    async def test_side_effect(request):
        nonlocal test_call_count
        test_call_count += 1
        if test_call_count == 1:
            return TestOutput(report="benchmark 5/7", success=False,
                recommendation="develop")
        return TestOutput(report="benchmark 7/7", success=True)

    agent = AsyncMock()
    agent.test = AsyncMock(side_effect=test_side_effect)
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="fixed code"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        passed=True, comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    _setup_issue_at_testing(store)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert test_call_count == 2  # test failed once, then passed


async def test_test_retry_with_design_recommendation(bus, store, task_mgr, config):
    """Test fails with recommendation=design → auto design+develop → re-test → pass."""
    test_call_count = 0

    async def test_side_effect(request):
        nonlocal test_call_count
        test_call_count += 1
        if test_call_count == 1:
            return TestOutput(report="missing feature", success=False,
                recommendation="design")
        return TestOutput(report="all pass", success=True)

    agent = AsyncMock()
    agent.test = AsyncMock(side_effect=test_side_effect)
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="output"))
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        passed=True, comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    _setup_issue_at_testing(store)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE


async def test_test_no_recommendation_stays_failed(bus, store, task_mgr, config):
    """Test fails with no recommendation → FAILED, no auto-retry."""
    agent = AsyncMock()
    agent.test = AsyncMock(return_value=TestOutput(
        report="failed", success=False, recommendation=None))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    _setup_issue_at_testing(store)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.FAILED


async def test_test_retries_exhausted_blocked(bus, store, task_mgr, config):
    """Test keeps failing with recommendation=develop → exhausts retries → BLOCKED."""
    agent = AsyncMock()
    agent.test = AsyncMock(return_value=TestOutput(
        report="still failing", success=False, recommendation="develop"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        passed=True, comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    _setup_issue_at_testing(store)

    events = []
    async def _on_fail(m): events.append(m)
    bus.subscribe(MessageType.EVT_TASK_FAILED, _on_fail)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED

    # Should have attempt counts in events
    assert any("retries" in e.payload.get("reason", "") for e in events)


async def test_test_recommendation_develop_fails_review_stops(bus, store, task_mgr, config):
    """Test fails → develop auto-triggered → develop review fails all rounds →
    develop goes BLOCKED → test loop stops (doesn't retry)."""
    agent = AsyncMock()
    agent.test = AsyncMock(return_value=TestOutput(
        report="fail", success=False, recommendation="develop"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        passed=False,
        comments=[ReviewComment(severity=Severity.HIGH, message="bad")],
        reviewer="mock",
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    _setup_issue_at_testing(store)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    # develop's review loop exhausted → issue is BLOCKED (from develop, not test)
    assert issue.status == IssueStatus.BLOCKED


async def test_test_event_includes_recommendation(bus, store, task_mgr, config):
    """EVT_TASK_FAILED payload includes the recommendation."""
    agent = AsyncMock()
    agent.test = AsyncMock(return_value=TestOutput(
        report="fail", success=False, recommendation="develop"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="output"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        passed=True, comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    _setup_issue_at_testing(store)

    events = []
    async def _h(m): events.append(m)
    bus.subscribe(MessageType.EVT_TASK_FAILED, _h)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    # First EVT_TASK_FAILED should have recommendation
    assert events[0].payload["recommendation"] == "develop"
