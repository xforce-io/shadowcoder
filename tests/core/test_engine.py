import pytest
from unittest.mock import AsyncMock, MagicMock
from shadowcoder.core.engine import Engine
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus, TaskStatus
from shadowcoder.core.config import Config
from shadowcoder.agents.types import (
    AgentRequest, AgentActionFailed, AgentUsage,
    DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput,
    ReviewComment, Severity,
)
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
    wt.ensure = AsyncMock(return_value="/tmp/wt")
    wt.exists = AsyncMock(return_value=True)
    wt.cleanup = AsyncMock()
    return wt


@pytest.fixture
def task_mgr(mock_worktree):
    return TaskManager(mock_worktree)


@pytest.fixture
def passing_agent():
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design output"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="develop output"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    return agent


@pytest.fixture
def failing_review_agent():
    agent = AsyncMock()
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="output"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
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
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
        reviewer="mock",
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert agent.design.call_count == config.get_max_review_rounds()


async def test_design_agent_failure(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(side_effect=AgentActionFailed("design failed", partial_output="err"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.FAILED


async def test_design_agent_exception(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(side_effect=RuntimeError("crash"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.FAILED


async def test_develop_happy_path(bus, store, task_mgr, registry_with, config):
    # Gate check: _gate_check will call _detect_test_command which may fail if no
    # project files are found. Mock _gate_check to always pass.
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    issue = store.create("Test issue")
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

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
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
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
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))

    async def design_side_effect(request):
        return DesignOutput(document="output")

    async def review_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= config.get_max_review_rounds():
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
                reviewer="mock")
        return ReviewOutput(comments=[], reviewer="mock")

    agent.design = AsyncMock(side_effect=design_side_effect)
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
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
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


async def test_cleanup_done_issue(bus, store, task_mgr, mock_worktree, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    status_events = []
    bus.subscribe(MessageType.EVT_STATUS_CHANGED, lambda m: status_events.append(m))

    issue = store.create("Test")
    # Transition to DONE (new path without TESTING)
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)
    store.transition_status(issue.id, IssueStatus.DEVELOPING)
    store.transition_status(issue.id, IssueStatus.DEV_REVIEW)
    store.transition_status(issue.id, IssueStatus.DONE)

    await bus.publish(Message(MessageType.CMD_CLEANUP, {"issue_id": 1}))

    mock_worktree.cleanup.assert_called_once_with("/tmp/repo", 1, delete_branch=False)
    cleaned_up_events = [e for e in status_events if e.payload.get("status") == "cleaned_up"]
    assert len(cleaned_up_events) == 1


async def test_cleanup_non_done_issue(bus, store, task_mgr, mock_worktree, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    error_events = []
    bus.subscribe(MessageType.EVT_ERROR, lambda m: error_events.append(m))

    store.create("Test")
    # Issue is in CREATED status, not DONE or CANCELLED

    await bus.publish(Message(MessageType.CMD_CLEANUP, {"issue_id": 1}))

    mock_worktree.cleanup.assert_not_called()
    assert len(error_events) == 1
    assert "not DONE or CANCELLED" in error_events[0].payload["message"]


async def test_budget_exceeded(bus, store, task_mgr, config, tmp_path):
    """Agent returns usage that exceeds the budget; issue should become BLOCKED."""
    config_path = tmp_path / "config_budget.yaml"
    config_path.write_text("""\
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
reviewers:
  design: [claude-code]
  develop: [claude-code]
review_policy:
  max_review_rounds: 3
  max_budget_usd: 0.001
logging:
  dir: /tmp/shadowcoder-test/logs
  level: INFO
issue_store:
  dir: .shadowcoder/issues
worktree:
  base_dir: .shadowcoder/worktrees
""")
    from shadowcoder.core.config import Config as Cfg
    budget_config = Cfg(str(config_path))

    expensive_usage = AgentUsage(input_tokens=1000, output_tokens=500,
                                 duration_ms=2000, cost_usd=1.00)
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design", usage=expensive_usage))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    failed_events = []
    bus.subscribe(MessageType.EVT_TASK_FAILED, lambda m: failed_events.append(m))

    engine = make_engine(bus, store, task_mgr, reg, budget_config)
    store.create("Budget test issue")

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert len(failed_events) == 1
    assert "budget exceeded" in failed_events[0].payload["reason"]


async def test_conditional_pass(bus, store, task_mgr, config):
    """Review returns HIGH=1 (conditional pass) — issue should still proceed to APPROVED."""
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.HIGH, message="minor high issue")],
        reviewer="mock",
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Conditional pass issue")

    completed_events = []
    bus.subscribe(MessageType.EVT_TASK_COMPLETED, lambda m: completed_events.append(m))

    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    # HIGH=1 (<=2), no CRITICAL → conditional_pass → APPROVED
    assert issue.status == IssueStatus.APPROVED
    assert len(completed_events) == 1


async def test_design_runs_preflight(bus, store, task_mgr, registry_with, config):
    """First design should run preflight before starting design loop."""
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    store.create("Test")
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    issue = store.get(1)
    assert issue.status == IssueStatus.APPROVED
    # Preflight should be in the log
    log = store.get_log(1)
    assert "Preflight" in log


async def test_design_low_feasibility_blocks(bus, store, task_mgr, config):
    """Low feasibility preflight should block the issue."""
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(
        feasibility="low", estimated_complexity="very_complex",
        risks=["Haskell not suitable for concurrent MVCC"]))
    agent.design = AsyncMock()  # should not be called
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test")
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    agent.design.assert_not_called()


async def test_run_full_lifecycle(bus, store, task_mgr, registry_with, config):
    """CMD_RUN: create → design → develop → done in one command."""
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    completed_events = []
    bus.subscribe(MessageType.EVT_TASK_COMPLETED, lambda m: completed_events.append(m))

    await bus.publish(Message(MessageType.CMD_RUN, {
        "title": "Run test issue",
    }))

    issues = store.list_all()
    assert len(issues) == 1
    assert issues[0].status == IssueStatus.DONE
    # design + develop completed
    assert len(completed_events) >= 2


async def test_run_existing_issue(bus, store, task_mgr, registry_with, config):
    """CMD_RUN on existing APPROVED issue: only develop runs."""
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    issue = store.create("Run existing")
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)

    await bus.publish(Message(MessageType.CMD_RUN, {"issue_id": 1}))

    assert store.get(1).status == IssueStatus.DONE


async def test_gate_fail_escalation(bus, store, task_mgr, config):
    """Gate fails twice → reviewer gets called to analyze."""
    agent = AsyncMock()
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.MEDIUM, message="suggestion")],
        reviewer="mock"))
    agent.preflight = AsyncMock(return_value=PreflightOutput(
        feasibility="high", estimated_complexity="moderate"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    issue = store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)

    # Mock gate to fail twice then pass
    call_count = 0
    async def mock_gate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return False, "tests failed", "error output here"
        return True, "gate passed", "all tests pass"
    engine._gate_check = mock_gate
    engine._get_code_diff = AsyncMock(return_value="diff content")

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    # Reviewer should have been called for gate escalation + normal review
    assert agent.review.call_count >= 2  # at least: 1 escalation + 1 normal review
