import pytest
from unittest.mock import AsyncMock, MagicMock
from shadowcoder.core.engine import Engine
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import (
    IssueStatus, TaskStatus, ReviewResult, ReviewComment, Severity,
)
from shadowcoder.core.config import Config
from shadowcoder.agents.base import AgentResponse
from shadowcoder.agents.registry import AgentRegistry


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


@pytest.fixture
def passing_agent():
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="design output", success=True))
    agent.review = AsyncMock(return_value=ReviewResult(passed=True, comments=[], reviewer="mock"))
    return agent


@pytest.fixture
def failing_review_agent():
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="output", success=True))
    agent.review = AsyncMock(return_value=ReviewResult(
        passed=False,
        comments=[ReviewComment(severity=Severity.HIGH, message="bad")],
        reviewer="mock",
    ))
    return agent


@pytest.fixture
def registry_with(passing_agent):
    reg = MagicMock()
    reg.get = MagicMock(return_value=passing_agent)
    return reg


def make_engine(bus, store, task_mgr, registry, config, repo_path="/tmp/repo"):
    return Engine(bus, store, task_mgr, registry, config, repo_path)


async def test_design_happy_path(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    store.create("Test issue")
    events = []
    bus.subscribe(MessageType.EVT_TASK_COMPLETED, lambda m: events.append(m))

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.APPROVED
    assert "design output" in issue.sections.get("设计", "")
    assert len(events) == 1


async def test_design_review_fails_then_blocked(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="output", success=True))
    agent.review = AsyncMock(return_value=ReviewResult(
        passed=False,
        comments=[ReviewComment(severity=Severity.HIGH, message="bad")],
        reviewer="mock",
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert agent.execute.call_count == config.get_max_review_rounds()


async def test_design_agent_failure(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="err", success=False))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.FAILED


async def test_design_agent_exception(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.execute = AsyncMock(side_effect=RuntimeError("crash"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.FAILED


async def test_develop_happy_path(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    issue = store.create("Test issue")
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.TESTING


async def test_test_happy_path(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    issue = store.create("Test issue")
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)
    store.transition_status(issue.id, IssueStatus.DEVELOPING)
    store.transition_status(issue.id, IssueStatus.DEV_REVIEW)
    store.transition_status(issue.id, IssueStatus.TESTING)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE


async def test_test_from_failed(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    issue = store.create("Test issue")
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.FAILED)

    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE


async def test_cancel(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_CANCEL, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.CANCELLED


async def test_approve_blocked(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="output", success=True))
    agent.review = AsyncMock(return_value=ReviewResult(
        passed=False,
        comments=[ReviewComment(severity=Severity.HIGH, message="bad")],
        reviewer="mock",
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.BLOCKED

    await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED


async def test_create_issue(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    events = []
    bus.subscribe(MessageType.EVT_ISSUE_CREATED, lambda m: events.append(m))

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "New feature"}))

    assert len(events) == 1
    issue = store.get(events[0].payload["issue_id"])
    assert issue.title == "New feature"


async def test_resume_blocked_design(bus, store, task_mgr, config):
    call_count = 0
    agent = AsyncMock()

    async def execute_side_effect(request):
        return AgentResponse(content="output", success=True)

    async def review_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= config.get_max_review_rounds():
            return ReviewResult(passed=False,
                comments=[ReviewComment(severity=Severity.HIGH, message="bad")],
                reviewer="mock")
        return ReviewResult(passed=True, comments=[], reviewer="mock")

    agent.execute = AsyncMock(side_effect=execute_side_effect)
    agent.review = AsyncMock(side_effect=review_side_effect)
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.BLOCKED

    await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED


async def test_all_reviewers_unavailable(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="output", success=True))
    agent.review = AsyncMock(side_effect=RuntimeError("reviewer crash"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.FAILED


async def test_list_issues(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    events = []
    bus.subscribe(MessageType.EVT_ISSUE_LIST, lambda m: events.append(m))

    store.create("A")
    store.create("B")
    await bus.publish(Message(MessageType.CMD_LIST, {}))

    assert len(events) == 1
    assert len(events[0].payload["issues"]) == 2


async def test_info_issue(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    events = []
    bus.subscribe(MessageType.EVT_ISSUE_INFO, lambda m: events.append(m))

    store.create("Test")
    await bus.publish(Message(MessageType.CMD_INFO, {"issue_id": 1}))

    assert len(events) == 1
    assert events[0].payload["issue"]["title"] == "Test"
