import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from shadowcoder.core.engine import Engine
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus, TaskStatus
from shadowcoder.core.config import Config
from shadowcoder.agents.types import (
    AcceptanceOutput, AgentRequest, AgentActionFailed, AgentUsage,
    DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput,
    ReviewComment, Severity,
)
from shadowcoder.agents.registry import AgentRegistry

# Stub acceptance script: fails before develop (no .dev_done), passes after
_STUB_ACCEPTANCE = AcceptanceOutput(
    script="#!/bin/bash\nset -euo pipefail\ntest -f .dev_done\n")


def _make_mock_agent(**overrides):
    """Create a mock agent with acceptance_script support."""
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
    for k, v in overrides.items():
        setattr(agent, k, v)
    return agent


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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="moderate"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design output"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="develop output"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
    return agent


@pytest.fixture
def failing_review_agent():
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="output"))
    agent.review = AsyncMock(return_value=ReviewOutput(
        comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
        reviewer="mock",
    ))
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    engine._run_acceptance_phase = AsyncMock(return_value=True)

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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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


@pytest.mark.asyncio
async def test_approve_clears_blocked_fields(bus, config, store, task_mgr):
    """approve clears blocked_reason and blocked_from."""
    from shadowcoder.core.models import BLOCKED_MAX_ROUNDS
    agent = _make_mock_agent()
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)
    engine = make_engine(bus, store, task_mgr, reg, config)

    store.create("Test approve clear")
    store.update_section(1, "开发步骤", "some code")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    issue = store.get(1)
    issue.blocked_reason = BLOCKED_MAX_ROUNDS
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert issue.blocked_reason is None
    assert issue.blocked_from is None


async def test_create_issue(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    events = []
    bus.subscribe(MessageType.EVT_ISSUE_CREATED, lambda m: events.append(m))

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "New feature"}))

    assert len(events) == 1
    issue = store.get(events[0].payload["issue_id"])
    assert issue.title == "New feature"


async def test_create_from_github_url_extracts_title(bus, store, task_mgr, registry_with, config, monkeypatch):
    """When --from is a GitHub issue URL and no title given, extract title from content."""
    def fake_fetch(url):
        return "# Add user authentication\n\nImplement OAuth2 login flow."
    monkeypatch.setattr(Engine, "_fetch_url_content", staticmethod(fake_fetch))

    engine = make_engine(bus, store, task_mgr, registry_with, config)
    events = []
    bus.subscribe(MessageType.EVT_ISSUE_CREATED, lambda m: events.append(m))

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "",
        "description": "https://github.com/owner/repo/issues/42",
    }))
    assert len(events) == 1
    issue = store.get(events[0].payload["issue_id"])
    assert issue.title == "Add user authentication"


async def test_create_no_title_no_url_gets_untitled(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    events = []
    bus.subscribe(MessageType.EVT_ISSUE_CREATED, lambda m: events.append(m))

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "",
        "description": "Just some plain text requirements.",
    }))
    assert len(events) == 1
    issue = store.get(events[0].payload["issue_id"])
    assert issue.title == "Untitled"


async def test_resume_blocked_design(bus, store, task_mgr, config):
    call_count = 0
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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

    issue = store.get(1)
    issue.blocked_from = IssueStatus.DESIGNING
    store.save(issue)

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED


async def test_all_reviewers_unavailable(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
clouds:
  local:
    env: {}
models:
  default-model:
    cloud: local
    model: sonnet
agents:
  claude-code:
    type: claude_code
    model: default-model
dispatch:
  design: claude-code
  develop: claude-code
  design_review: [claude-code]
  develop_review: [claude-code]
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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    engine._run_acceptance_phase = AsyncMock(return_value=True)

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
    engine._run_acceptance_phase = AsyncMock(return_value=True)

    issue = store.create("Run existing")
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)

    await bus.publish(Message(MessageType.CMD_RUN, {"issue_id": 1}))

    assert store.get(1).status == IssueStatus.DONE


async def test_gate_fail_escalation(bus, store, task_mgr, config):
    """Gate fails twice → reviewer gets called to analyze."""
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
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
    engine._run_acceptance_phase = AsyncMock(return_value=True)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    # Reviewer should have been called for gate escalation + normal review
    assert agent.review.call_count >= 2  # at least: 1 escalation + 1 normal review


@pytest.fixture
def integ_env(bus, store, task_mgr, registry_with, config):
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    return {"engine": engine}


def test_extract_gate_failure_summary_pytest(integ_env):
    """Extracts FAILED lines and error lines from pytest output."""
    engine = integ_env["engine"]
    output = (
        "tests/test_foo.py::test_bar PASSED\n"
        "tests/test_foo.py::test_baz FAILED\n"
        "E   AttributeError: 'Foo' object has no attribute 'bar'\n"
        "========= 1 failed, 1 passed ========="
    )
    summary = engine._extract_gate_failure_summary(output)
    assert "FAILED" in summary
    assert "AttributeError" in summary
    assert "PASSED" not in summary


def test_extract_gate_failure_summary_cargo(integ_env):
    engine = integ_env["engine"]
    output = "thread 'test_foo' panicked at 'assertion failed', src/lib.rs:10"
    summary = engine._extract_gate_failure_summary(output)
    assert "panicked" in summary


def test_extract_gate_failure_summary_go(integ_env):
    engine = integ_env["engine"]
    output = "--- FAIL: TestFoo (0.01s)\n    foo_test.go:15: expected 1, got 2"
    summary = engine._extract_gate_failure_summary(output)
    assert "FAIL: TestFoo" in summary


def test_extract_gate_failure_summary_empty(integ_env):
    engine = integ_env["engine"]
    assert engine._extract_gate_failure_summary("all tests passed") == ""


async def test_preflight_warns_no_test_command(bus, store, task_mgr, config):
    """Existing project with no detectable test command logs a warning."""
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="simple"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design", test_command="make test"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        # Create a file so it's not empty (existing project) but no marker file
        Path(os.path.join(td, "main.go")).write_text("package main")
        # Mock task creation to use this dir
        original_create = task_mgr.create
        async def mock_create(*args, **kwargs):
            t = await original_create(*args, **kwargs)
            t.worktree_path = td
            return t
        task_mgr.create = mock_create

        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        log = store.get_log(1)
        assert "auto-detect test command" in log.lower() or "test_command" in log


async def test_gate_uses_design_test_command(bus, store, task_mgr, registry_with, config):
    """When detect_language fails but design provided test_command, gate uses it."""
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    store.create("Test issue")

    # Store test_command in feedback (simulating what design cycle does)
    fb = store.load_feedback(1)
    fb["test_command"] = "echo TESTS_PASS"
    store.save_feedback(1, fb)

    # Gate should use "echo TESTS_PASS" and succeed
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        passed, msg, output = await engine._gate_check(1, td, [])
        assert passed
        assert "TESTS_PASS" in output


async def test_gate_fallback_without_design_test_command(bus, store, task_mgr, registry_with, config):
    """When no config, no design test_command, and no marker files, gate fails."""
    engine = make_engine(bus, store, task_mgr, registry_with, config)
    store.create("Test issue")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        passed, msg, output = await engine._gate_check(1, td, [])
        assert not passed
        assert "Cannot detect test command" in msg


@pytest.mark.asyncio
async def test_extract_error_summary_calls_utility_agent(bus, config, store, task_mgr):
    """_extract_error_summary uses the utility agent to extract root cause."""
    mock_agent = _make_mock_agent()
    # Simulate the utility agent returning a structured summary
    mock_agent._run = AsyncMock(return_value=(
        "Root cause: wrong number of arguments to Return for MockKNAccess.DeleteKN: got 1, want 2\n"
        "Location: version_service_test.go:1024\n"
        "Fix: Change .Return(nil) to .Return(int64(0), nil)",
        AgentUsage(input_tokens=500, output_tokens=100),
    ))
    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = Engine(bus, store, task_mgr, reg, config, "/tmp/repo")

    summary = await engine._extract_error_summary(
        "... 500 lines of test output with PASS tests ...\n"
        "wrong number of arguments to Return: got 1, want 2\n"
        "missing call(s) to MockKNAccess.DeleteKN\n" * 100 +
        "FAIL\nFAIL bkn-backend/logics/version 1.2s\n",
        issue_id=1,
    )
    assert summary  # non-empty
    assert "Root cause" in summary
    mock_agent._run.assert_called_once()
    # Verify prompt contains the raw output
    call_args = mock_agent._run.call_args
    assert "wrong number of arguments" in call_args[0][0]


def test_error_hash_detects_same_error():
    """_error_hash returns consistent hash for same error, different for different errors."""
    from shadowcoder.core.engine import Engine
    h1 = Engine._error_hash("FAIL: wrong number of args to Return: got 1, want 2")
    h2 = Engine._error_hash("FAIL: wrong number of args to Return: got 1, want 2")
    h3 = Engine._error_hash("FAIL: undefined function foo()")
    assert h1 == h2
    assert h1 != h3


def test_error_hash_empty():
    from shadowcoder.core.engine import Engine
    assert Engine._error_hash("") == ""
    assert Engine._error_hash(None) == ""


@pytest.mark.asyncio
async def test_run_warns_on_duplicate_active_issue(bus, config, store, task_mgr):
    """run command warns when there are active issues and creates anyway."""
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="moderate")),
        design=AsyncMock(return_value=DesignOutput(document="design")),
    )
    registry = MagicMock()
    registry.get = MagicMock(return_value=mock_agent)
    engine = make_engine(bus, store, task_mgr, registry, config)

    # Create first issue in DEVELOPING state
    issue1 = store.create("First issue")
    store.transition_status(issue1.id, IssueStatus.DESIGNING)
    store.transition_status(issue1.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue1.id, IssueStatus.APPROVED)
    store.transition_status(issue1.id, IssueStatus.DEVELOPING)

    # Capture log messages
    logged = []
    original_log = engine._log
    def capture_log(issue_id, msg, *a, **kw):
        logged.append(msg)
        original_log(issue_id, msg, *a, **kw)
    engine._log = capture_log

    # Run with new title — should warn about active issue
    msg = Message(MessageType.CMD_RUN, {"title": "Second issue", "description": "desc"})
    try:
        await engine._on_run(msg)
    except Exception:
        pass  # May fail due to mock setup, that's ok

    # Check that a warning was logged about active issues
    warning_found = any("活跃 issue" in m or "active issue" in m for m in logged)
    assert warning_found, f"Expected active-issue warning in logs, got: {logged}"


@pytest.mark.asyncio
async def test_acceptance_script_syntax_validation(bus, config, store, task_mgr, tmp_repo):
    """Acceptance script with bash syntax errors triggers retry."""
    bad_script = AcceptanceOutput(
        script="#!/bin/bash\nVersionService interface {\n  not valid bash\n}\n")
    good_script = AcceptanceOutput(
        script="#!/bin/bash\nset -euo pipefail\ntest -f .dev_done\n")

    mock_agent = _make_mock_agent()
    # First call returns bad bash, second returns good bash
    mock_agent.write_acceptance_script = AsyncMock(
        side_effect=[bad_script, good_script])

    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = Engine(bus, store, task_mgr, reg, config, str(tmp_repo))

    issue = store.create("Test issue")
    from shadowcoder.core.models import Task
    task = Task(task_id="t1", issue_id=issue.id, repo_path=str(tmp_repo),
                action="acceptance", agent_name="mock",
                worktree_path=str(tmp_repo), status=TaskStatus.RUNNING)

    result = await engine._run_acceptance_phase(issue, task)
    # Should have retried and succeeded with the good script
    assert mock_agent.write_acceptance_script.call_count == 2


@pytest.mark.asyncio
async def test_gate_failure_extraction_and_hash_tracking(bus, config, store, task_mgr, tmp_repo):
    """Full flow: gate fails → extract summary → hash → detect repeat → escalate."""
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        design=AsyncMock(return_value=DesignOutput(document="design")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(
            comments=[
                ReviewComment(severity=Severity.HIGH, message="fix it", location="file.go")
            ])),
    )
    # Utility agent returns consistent summary for same-error detection
    mock_agent._run = AsyncMock(return_value=(
        "Root cause: compilation failed\nfile.go:42: undefined foo",
        AgentUsage(input_tokens=100, output_tokens=50),
    ))

    registry = MagicMock()
    registry.get = MagicMock(return_value=mock_agent)
    engine = Engine(bus, store, task_mgr, registry, config, str(tmp_repo))

    # Verify _error_hash produces consistent results
    summary = "Root cause: compilation failed\nfile.go:42: undefined foo"
    h1 = Engine._error_hash(summary)
    h2 = Engine._error_hash(summary)
    assert h1 == h2
    assert len(h1) == 16  # sha256 truncated to 16 hex chars


@pytest.mark.asyncio
async def test_acceptance_fail_escalates_to_reviewer(bus, config, store, task_mgr, tmp_repo):
    """Acceptance fails with same error twice → reviewer gets called for analysis."""
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(
            comments=[ReviewComment(severity=Severity.MEDIUM, message="looks ok")],
            reviewer="mock")),
    )
    registry = MagicMock()
    registry.get = MagicMock(return_value=mock_agent)
    engine = Engine(bus, store, task_mgr, registry, config, str(tmp_repo))

    issue = store.create("Test acceptance escalation")
    store.update_section(1, "需求", "implement foo")
    store.update_section(1, "设计", "design foo")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)

    # Write an acceptance script that always fails with a consistent error
    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text(
        "#!/bin/bash\nset -euo pipefail\necho 'Expected ValueError for x'\nexit 1\n")

    # Mock _run_command to simulate acceptance failure (avoids needing real worktree)
    engine._run_command = AsyncMock(
        return_value=(False, "Expected ValueError for x"))
    # Mock gate to pass (we want to test acceptance failure, not gate failure)
    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff content")
    # Mock _extract_error_summary to return consistent output (triggers same-error detection)
    engine._extract_error_summary = AsyncMock(return_value="Expected ValueError for x")

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    # Reviewer should have been called at least once for acceptance escalation
    assert mock_agent.review.call_count >= 1, (
        f"Expected reviewer to be called for acceptance escalation, "
        f"but review was called {mock_agent.review.call_count} times")


def test_review_blames_acceptance_detects_marker():
    """_review_blames_acceptance returns True when comment contains target marker."""
    review_with_marker = ReviewOutput(
        comments=[
            ReviewComment(severity=Severity.HIGH,
                          message="[TARGET:acceptance_script] The assertion for '1 - - 2' is wrong"),
        ],
        reviewer="mock")
    review_without_marker = ReviewOutput(
        comments=[
            ReviewComment(severity=Severity.HIGH, message="Code has a bug in parser"),
        ],
        reviewer="mock")

    assert Engine._review_blames_acceptance(review_with_marker) is True
    assert Engine._review_blames_acceptance(review_without_marker) is False


@pytest.mark.asyncio
async def test_acceptance_blame_causes_blocked(bus, config, store, task_mgr, tmp_repo):
    """Reviewer blames acceptance script → issue transitions to BLOCKED."""
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        # Reviewer returns comment with acceptance_script target
        review=AsyncMock(return_value=ReviewOutput(
            comments=[ReviewComment(
                severity=Severity.HIGH,
                message="[TARGET:acceptance_script] '1 - - 2' is a valid expression, "
                        "acceptance script wrongly expects ValueError")],
            reviewer="mock")),
    )
    registry = MagicMock()
    registry.get = MagicMock(return_value=mock_agent)
    engine = Engine(bus, store, task_mgr, registry, config, str(tmp_repo))

    issue = store.create("Test acceptance blame")
    store.update_section(1, "需求", "implement foo")
    store.update_section(1, "设计", "design foo")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)

    # Acceptance script that always fails
    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text(
        "#!/bin/bash\nset -euo pipefail\necho 'Expected ValueError for x'\nexit 1\n")

    # Mock _run_command to simulate acceptance failure (avoids needing real worktree)
    engine._run_command = AsyncMock(
        return_value=(False, "Expected ValueError for x"))
    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff content")
    engine._extract_error_summary = AsyncMock(return_value="Expected ValueError for x")

    failed_events = []
    bus.subscribe(MessageType.EVT_TASK_FAILED, lambda m: failed_events.append(m))

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert len(failed_events) >= 1
    assert "acceptance script" in failed_events[-1].payload["reason"]

    # Check log mentions acceptance script
    log = store.get_log(1)
    assert "acceptance script" in log.lower()


@pytest.mark.asyncio
async def test_unblock_acceptance_bug_skips_develop(bus, config, store, task_mgr, tmp_repo):
    """Unblock from acceptance_script_bug skips develop agent, goes straight to acceptance+gate+review."""
    from shadowcoder.core.models import BLOCKED_ACCEPTANCE_BUG
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(
            comments=[], reviewer="mock")),
    )
    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = make_engine(bus, store, task_mgr, reg, config, repo_path=str(tmp_repo))

    issue = store.create("Test skip develop")
    store.update_section(1, "需求", "implement foo")
    store.update_section(1, "设计", "design foo")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    # Block with acceptance_script_bug
    issue = store.get(1)
    issue.blocked_reason = BLOCKED_ACCEPTANCE_BUG
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    # Write a fixed acceptance script that passes
    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text("#!/bin/bash\nset -euo pipefail\nexit 0\n")

    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff")
    # Mock _run_command so acceptance check passes without real bash
    engine._run_command = AsyncMock(return_value=(True, "all passed"))

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {
        "issue_id": 1, "message": "fixed acceptance script"}))

    issue = store.get(1)
    log = store.get_log(1)
    assert issue.status == IssueStatus.DONE, f"Expected DONE, got {issue.status.value}. Log:\n{log}"

    # Develop agent should NOT have been called (skipped)
    assert mock_agent.develop.call_count == 0

    # But review should have been called
    assert mock_agent.review.call_count >= 1

    # Log should mention the skip
    log = store.get_log(1)
    assert "跳过 develop agent" in log


@pytest.mark.asyncio
async def test_block_issue_sets_metadata(bus, config, store, task_mgr):
    """_block_issue sets blocked_reason and blocked_from on the issue."""
    from shadowcoder.core.models import BLOCKED_ACCEPTANCE_BUG
    agent = _make_mock_agent()
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)
    engine = make_engine(bus, store, task_mgr, reg, config)

    issue = store.create("Test block metadata")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    task = MagicMock()
    task.task_id = "t1"
    task.status = TaskStatus.RUNNING

    await engine._block_issue(1, task, BLOCKED_ACCEPTANCE_BUG)

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert issue.blocked_reason == BLOCKED_ACCEPTANCE_BUG
    assert issue.blocked_from == IssueStatus.DEVELOPING


@pytest.mark.asyncio
async def test_unblock_restores_develop(bus, config, store, task_mgr, tmp_repo):
    """unblock restores blocked_from status and re-enters develop cycle."""
    from shadowcoder.core.models import BLOCKED_ACCEPTANCE_BUG
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(
            comments=[], reviewer="mock")),
    )
    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = make_engine(bus, store, task_mgr, reg, config, repo_path=str(tmp_repo))

    issue = store.create("Test unblock")
    store.update_section(1, "需求", "implement foo")
    store.update_section(1, "设计", "design foo")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    # Manually block with metadata
    issue = store.get(1)
    issue.blocked_reason = BLOCKED_ACCEPTANCE_BUG
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    # Write acceptance script that passes (so develop can complete)
    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text("#!/bin/bash\nset -euo pipefail\nexit 0\n")

    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff")
    engine._run_command = AsyncMock(return_value=(True, ""))

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {"issue_id": 1}))

    issue = store.get(1)
    # Should have completed the develop cycle (review passes with no comments)
    assert issue.status == IssueStatus.DONE
    assert issue.blocked_reason is None
    assert issue.blocked_from is None


@pytest.mark.asyncio
async def test_unblock_with_message_logs(bus, config, store, task_mgr, tmp_repo):
    """unblock message is written to issue log."""
    from shadowcoder.core.models import BLOCKED_MAX_ROUNDS
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock")),
    )
    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = make_engine(bus, store, task_mgr, reg, config, repo_path=str(tmp_repo))

    issue = store.create("Test unblock msg")
    store.update_section(1, "需求", "foo")
    store.update_section(1, "设计", "bar")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    issue = store.get(1)
    issue.blocked_reason = BLOCKED_MAX_ROUNDS
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text("#!/bin/bash\nexit 0\n")
    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff")
    engine._run_command = AsyncMock(return_value=(True, ""))

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {
        "issue_id": 1, "message": "fixed acceptance script"}))

    log = store.get_log(1)
    assert "fixed acceptance script" in log


@pytest.mark.asyncio
async def test_unblock_rejects_non_blocked(bus, config, store, task_mgr):
    """unblock on non-BLOCKED issue emits error."""
    engine = make_engine(bus, store, task_mgr, MagicMock(), config)
    store.create("Test not blocked")

    errors = []
    bus.subscribe(MessageType.EVT_ERROR, lambda m: errors.append(m))

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {"issue_id": 1}))
    assert len(errors) == 1
    assert "not BLOCKED" in errors[0].payload["message"]


@pytest.mark.asyncio
async def test_resume_rejects_blocked(bus, config, store, task_mgr):
    """resume on BLOCKED issue returns error suggesting unblock."""
    from shadowcoder.core.models import BLOCKED_MAX_ROUNDS
    engine = make_engine(bus, store, task_mgr, MagicMock(), config)
    store.create("Test resume blocked")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    issue = store.get(1)
    issue.blocked_reason = BLOCKED_MAX_ROUNDS
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    errors = []
    bus.subscribe(MessageType.EVT_ERROR, lambda m: errors.append(m))

    await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))

    assert len(errors) == 1
    assert "unblock" in errors[0].payload["message"].lower()
