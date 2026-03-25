"""Tests for Engine._run_develop_cycle gate logic (replaced old _on_test retry loop)."""
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
    DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput,
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


def _setup_issue_at_approved(store):
    """Create an issue and transition it to APPROVED status."""
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)


async def test_develop_gate_fail_then_pass(bus, store, task_mgr, config):
    """Gate fails first round, passes second round → DONE."""
    gate_call_count = 0

    async def gate_side_effect(issue_id, worktree_path, proposed_tests):
        nonlocal gate_call_count
        gate_call_count += 1
        if gate_call_count == 1:
            return False, "build failed", "error output"
        return True, "gate passed", "ok"

    agent = AsyncMock()
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    engine._gate_check = AsyncMock(side_effect=gate_side_effect)
    engine._get_code_diff = AsyncMock(return_value="")

    _setup_issue_at_approved(store)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert gate_call_count == 2  # gate failed once, then passed


async def test_develop_gate_always_fail_blocked(bus, store, task_mgr, config):
    """Gate always fails → exhausts max_rounds → BLOCKED."""
    agent = AsyncMock()
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    engine._gate_check = AsyncMock(return_value=(False, "tests failed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    _setup_issue_at_approved(store)

    events = []
    async def _on_fail(m): events.append(m)
    bus.subscribe(MessageType.EVT_TASK_FAILED, _on_fail)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert any("review not passed" in e.payload.get("reason", "") for e in events)


async def test_develop_review_critical_retries(bus, store, task_mgr, config):
    """Review has CRITICAL comment → retry develop → eventually DONE."""
    review_call_count = 0

    async def review_side_effect(request):
        nonlocal review_call_count
        review_call_count += 1
        if review_call_count == 1:
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.CRITICAL, message="critical bug")],
                reviewer="mock")
        return ReviewOutput(comments=[], reviewer="mock")

    agent = AsyncMock()
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.review = AsyncMock(side_effect=review_side_effect)
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    _setup_issue_at_approved(store)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert review_call_count == 2


async def test_develop_review_conditional_pass(bus, store, task_mgr, config):
    """Review has HIGH=1 (conditional pass) → DONE immediately."""
    agent = AsyncMock()
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.HIGH, message="minor high issue")],
        reviewer="mock",
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    _setup_issue_at_approved(store)

    completed_events = []
    bus.subscribe(MessageType.EVT_TASK_COMPLETED, lambda m: completed_events.append(m))

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert len(completed_events) == 1


async def test_review_decision_logic():
    """Test _review_decision with various severity counts."""
    from shadowcoder.core.engine import Engine as E
    from shadowcoder.agents.types import ReviewOutput, ReviewComment, Severity

    engine_mock = MagicMock(spec=E)
    # config is set in __init__, not a class attr — must attach manually
    config_mock = MagicMock()
    config_mock.get_pass_threshold.return_value = "no_critical"
    engine_mock.config = config_mock

    # No comments → pass
    review_pass = ReviewOutput(comments=[], reviewer="mock")
    assert E._review_decision(engine_mock, review_pass) == "pass"

    # HIGH=1 → conditional_pass
    review_cond = ReviewOutput(comments=[
        ReviewComment(severity=Severity.HIGH, message="h1")
    ], reviewer="mock")
    assert E._review_decision(engine_mock, review_cond) == "conditional_pass"

    # HIGH=2 → conditional_pass
    review_cond2 = ReviewOutput(comments=[
        ReviewComment(severity=Severity.HIGH, message="h1"),
        ReviewComment(severity=Severity.HIGH, message="h2"),
    ], reviewer="mock")
    assert E._review_decision(engine_mock, review_cond2) == "conditional_pass"

    # HIGH=3 → retry (lenient mode: 3+ HIGH = retry)
    review_retry = ReviewOutput(comments=[
        ReviewComment(severity=Severity.HIGH, message="h1"),
        ReviewComment(severity=Severity.HIGH, message="h2"),
        ReviewComment(severity=Severity.HIGH, message="h3"),
    ], reviewer="mock")
    assert E._review_decision(engine_mock, review_retry) == "retry"

    # _review_decision does not depend on threshold — always same logic
    # (threshold only affects how conditional_pass is handled downstream)
    config_mock.get_pass_threshold.return_value = "no_high_or_critical"
    assert E._review_decision(engine_mock, review_cond) == "conditional_pass"
    assert E._review_decision(engine_mock, review_retry) == "retry"

    # CRITICAL=1 → retry (even if no HIGH)
    review_critical = ReviewOutput(comments=[
        ReviewComment(severity=Severity.CRITICAL, message="c1")
    ], reviewer="mock")
    assert E._review_decision(engine_mock, review_critical) == "retry"
