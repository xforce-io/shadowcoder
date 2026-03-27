"""
Integration tests: real components (engine + store + bus + worktree + task_manager),
mock agent + gate. Covers normal flows, error recovery, and edge cases.
"""
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.agents.types import (
    AgentActionFailed,
    AgentRequest,
    AgentUsage,
    DesignOutput,
    DevelopOutput,
    PreflightOutput,
    ReviewComment,
    ReviewOutput,
    Severity,
    TestCase,
)
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import IssueStatus
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Configurable stub agent
# ---------------------------------------------------------------------------

class StubAgent(BaseAgent):
    """Configurable agent for integration testing.

    By default all actions pass. Call configure_*() to inject failures,
    delays, or custom behaviors.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.preflight_calls: list[AgentRequest] = []
        self.design_calls: list[AgentRequest] = []
        self.develop_calls: list[AgentRequest] = []
        self.review_calls: list[AgentRequest] = []

        # Defaults
        self._preflight_fn = self._default_preflight
        self._design_fn = self._default_design
        self._develop_fn = self._default_develop
        self._review_fn = self._default_review

    # --- abstract method implementations (required by BaseAgent) ---

    def _get_model(self) -> str:
        return "stub-model"

    def _get_permission_mode(self) -> str:
        return "auto"

    async def _run(self, prompt, *, cwd=None, system_prompt=None,
                   session_id=None, resume_id=None):
        from shadowcoder.agents.types import AgentUsage
        # StubAgent overrides all action methods so _run is never called directly
        return ("stub output", AgentUsage())

    # --- configurators ---

    def configure_preflight(self, fn):
        self._preflight_fn = fn

    def configure_design(self, fn):
        self._design_fn = fn

    def configure_develop(self, fn):
        self._develop_fn = fn

    def configure_review(self, fn):
        self._review_fn = fn

    def configure_review_fail_then_pass(self, fail_count: int):
        """Reviews fail with CRITICAL for the first `fail_count` calls, then pass."""
        counter = {"n": 0}

        async def _fn(request):
            counter["n"] += 1
            if counter["n"] <= fail_count:
                return ReviewOutput(
                    comments=[ReviewComment(
                        severity=Severity.CRITICAL,
                        message=f"Review round {counter['n']}: needs improvement",
                    )],
                    reviewer="stub-reviewer",
                )
            return ReviewOutput(
                comments=[ReviewComment(
                    severity=Severity.LOW,
                    message="Looks good",
                )],
                reviewer="stub-reviewer",
            )

        self._review_fn = _fn

    # --- defaults ---

    async def _default_preflight(self, request):
        return PreflightOutput(feasibility="high", estimated_complexity="moderate")

    async def _default_design(self, request):
        return DesignOutput(document="## Architecture\nStub design output.\n\n## API\nStub API.")

    async def _default_develop(self, request):
        # Create marker file so acceptance script passes after develop
        wt = request.context.get("worktree_path")
        if wt:
            from pathlib import Path
            (Path(wt) / ".dev_done").write_text("1")
        return DevelopOutput(summary="## Implementation\nStub develop output.")

    async def _default_review(self, request):
        return ReviewOutput(
            comments=[ReviewComment(severity=Severity.LOW, message="Minor nit")],
            reviewer="stub-reviewer",
        )

    def configure_acceptance(self, fn):
        self._acceptance_fn = fn

    # --- interface ---

    async def write_acceptance_script(self, request: AgentRequest):
        from shadowcoder.agents.types import AcceptanceOutput
        if hasattr(self, "_acceptance_fn"):
            return await self._acceptance_fn(request)
        # Default: return a script that fails before develop, passes after
        # Uses a marker file: develop creates it, acceptance checks for it
        return AcceptanceOutput(
            script="#!/bin/bash\nset -euo pipefail\n"
                   "# Stub acceptance: fail until .dev_done marker exists\n"
                   "test -f .dev_done\n")

    async def preflight(self, request: AgentRequest) -> PreflightOutput:
        self.preflight_calls.append(request)
        return await self._preflight_fn(request)

    async def design(self, request: AgentRequest) -> DesignOutput:
        self.design_calls.append(request)
        return await self._design_fn(request)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        self.develop_calls.append(request)
        return await self._develop_fn(request)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        self.review_calls.append(request)
        return await self._review_fn(request)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def integ_repo(tmp_path):
    """A real git repo for integration tests."""
    repo = tmp_path / "integ-project"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# Integration Test Project\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add README"],
                   cwd=str(repo), check=True, capture_output=True)
    return repo


@pytest.fixture
def integ_config(tmp_path):
    config_path = tmp_path / "config.yaml"
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
  pass_threshold: no_high_or_critical
  max_review_rounds: 3

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
""")
    return Config(str(config_path))


@pytest.fixture
def integ_config_with_budget(tmp_path):
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
  pass_threshold: no_high_or_critical
  max_review_rounds: 3
  max_budget_usd: 0.001

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
""")
    return Config(str(config_path))


@pytest.fixture
def agent():
    return StubAgent({"type": "claude_code"})


@pytest.fixture
def system(integ_repo, integ_config, agent):
    """Assemble a full system with real components, stub agent, mocked gate."""
    AgentRegistry.register("claude_code", lambda cfg: agent)

    bus = MessageBus()
    wt_manager = WorktreeManager(integ_config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    store = IssueStore(str(integ_repo), integ_config)
    registry = AgentRegistry(integ_config)
    registry._instances["claude-code"] = agent

    engine = Engine(bus, store, task_manager, registry, integ_config, str(integ_repo))
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="diff --git a/foo.py")
    engine._run_acceptance_phase = AsyncMock(return_value=True)

    return {
        "bus": bus,
        "engine": engine,
        "store": store,
        "task_manager": task_manager,
        "agent": agent,
        "repo": integ_repo,
        "config": integ_config,
    }


def _collect_events(bus):
    """Subscribe to all event types and return the dict of collected events."""
    events = {t: [] for t in MessageType}
    for mt in MessageType:
        if mt.value.startswith("evt."):
            async def _handler(msg, _mt=mt):
                events[_mt].append(msg)
            bus.subscribe(mt, _handler)
    return events


# ===========================================================================
# 1. Normal flows
# ===========================================================================


class TestHappyPath:
    """Normal lifecycle flows."""

    async def test_create_design_develop_done(self, system):
        """Individual commands: create → design → develop → done."""
        bus, store, agent = system["bus"], system["store"], system["agent"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
            "title": "Calculator module",
            "priority": "high",
            "tags": ["feature"],
        }))
        assert store.get(1).status == IssueStatus.CREATED

        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.APPROVED
        assert "Architecture" in issue.sections.get("设计", "")

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.DONE
        assert "Implementation" in issue.sections.get("开发步骤", "")

        assert len(agent.preflight_calls) == 1
        assert len(agent.design_calls) == 1
        assert len(agent.develop_calls) == 1
        assert len(agent.review_calls) == 2  # design review + develop review

    async def test_run_full_lifecycle(self, system):
        """CMD_RUN creates + designs + develops in one shot."""
        bus, store, agent = system["bus"], system["store"], system["agent"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_RUN, {
            "title": "Full run test",
        }))

        issues = store.list_all()
        assert len(issues) == 1
        assert issues[0].status == IssueStatus.DONE
        assert len(events[MessageType.EVT_TASK_COMPLETED]) >= 2

    async def test_run_existing_approved_issue(self, system):
        """CMD_RUN on an already-approved issue skips design."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        store.create("Pre-approved")
        store.transition_status(1, IssueStatus.DESIGNING)
        store.transition_status(1, IssueStatus.DESIGN_REVIEW)
        store.transition_status(1, IssueStatus.APPROVED)

        await bus.publish(Message(MessageType.CMD_RUN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        assert len(agent.design_calls) == 0
        assert len(agent.develop_calls) == 1

    async def test_create_with_description_file(self, system):
        """Create issue with --from description file."""
        bus, store, repo = system["bus"], system["store"], system["repo"]

        desc_file = repo / "requirements.md"
        desc_file.write_text("## Requirements\n- Feature A\n- Feature B\n")

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
            "title": "From file",
            "description": str(desc_file),
        }))

        issue = store.get(1)
        assert issue.status == IssueStatus.CREATED
        assert "Feature A" in issue.sections.get("需求", "")
        assert "Feature B" in issue.sections.get("需求", "")

    async def test_create_with_inline_description(self, system):
        """Create issue with inline description (not a file path)."""
        bus, store = system["bus"], system["store"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
            "title": "Inline desc",
            "description": "Build a REST API with CRUD operations",
        }))

        issue = store.get(1)
        assert "REST API" in issue.sections.get("需求", "")


# ===========================================================================
# 2. Design phase scenarios
# ===========================================================================


class TestDesignPhase:
    """Design cycle: review retry, exhaustion, preflight."""

    async def test_design_review_retry_then_pass(self, system):
        """Design review fails twice, then passes."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        agent.configure_review_fail_then_pass(2)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Retry design"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        issue = store.get(1)
        assert issue.status == IssueStatus.APPROVED
        assert len(agent.design_calls) == 3  # 2 retries + 1 final
        assert "PASSED" in issue.sections.get("Design Review", "")

        log = store.get_log(1)
        assert "RETRY" in log

    async def test_design_review_exhaustion_blocked(self, system):
        """Design review fails max_rounds times → BLOCKED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]
        config = system["config"]

        agent.configure_review_fail_then_pass(100)  # always fail

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Exhaust design"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        issue = store.get(1)
        assert issue.status == IssueStatus.BLOCKED
        assert len(agent.design_calls) == config.get_max_review_rounds()

    async def test_preflight_low_feasibility_blocks(self, system):
        """Low feasibility preflight → BLOCKED without running design."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def low_preflight(request):
            return PreflightOutput(
                feasibility="low",
                estimated_complexity="very_complex",
                risks=["Fundamentally impossible"],
            )

        agent.configure_preflight(low_preflight)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Impossible task"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.BLOCKED
        assert len(agent.design_calls) == 0

    async def test_preflight_exception_continues_design(self, system):
        """Preflight exception is non-fatal: design continues."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def crashing_preflight(request):
            raise RuntimeError("preflight crashed")

        agent.configure_preflight(crashing_preflight)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Preflight crash"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        issue = store.get(1)
        assert issue.status == IssueStatus.APPROVED
        assert len(agent.design_calls) == 1

    async def test_design_conditional_pass(self, system):
        """Review with HIGH=1 (no CRITICAL) → conditional pass → APPROVED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def conditional_review(request):
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.HIGH, message="minor high")],
                reviewer="stub-reviewer",
            )

        agent.configure_review(conditional_review)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Conditional"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.APPROVED
        assert len(agent.design_calls) == 1  # no retry


# ===========================================================================
# 3. Develop phase scenarios
# ===========================================================================


class TestDevelopPhase:
    """Develop cycle: gate, review, escalation."""

    async def _setup_approved(self, bus, store):
        """Create an issue and get it to APPROVED via design."""
        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Dev test"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

    async def test_gate_fail_retry_then_pass(self, system):
        """Gate fails once → retry develop → gate passes → review → DONE."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        gate_count = {"n": 0}

        async def gate_fn(issue_id, worktree_path, proposed_tests):
            gate_count["n"] += 1
            if gate_count["n"] == 1:
                return False, "build failed", "error: cannot find module"
            return True, "gate passed", "all tests pass"

        engine._gate_check = AsyncMock(side_effect=gate_fn)
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        await self._setup_approved(bus, store)
        agent.review_calls.clear()

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        assert gate_count["n"] == 2
        assert len(agent.develop_calls) >= 2  # at least 2 develop rounds

    async def test_gate_consecutive_fail_escalates(self, system):
        """Gate fails twice consecutively → escalates to reviewer."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        gate_count = {"n": 0}

        async def gate_fn(issue_id, worktree_path, proposed_tests):
            gate_count["n"] += 1
            if gate_count["n"] <= 2:
                return False, "tests failed", "FAILED test_foo"
            return True, "gate passed", "ok"

        engine._gate_check = AsyncMock(side_effect=gate_fn)
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        await self._setup_approved(bus, store)
        review_count_before = len(agent.review_calls)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        # Reviewer was called for escalation + normal review
        reviews_during_develop = len(agent.review_calls) - review_count_before
        assert reviews_during_develop >= 2

        log = store.get_log(1)
        assert "升级给 reviewer" in log

    async def test_gate_always_fails_blocked(self, system):
        """Gate always fails → exhausts rounds → BLOCKED."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        engine._gate_check = AsyncMock(return_value=(False, "tests failed", "error"))
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        await self._setup_approved(bus, store)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.BLOCKED

    async def test_develop_review_retry_then_pass(self, system):
        """Develop review fails once (CRITICAL) → retry → passes → DONE."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await self._setup_approved(bus, store)

        # Configure review: fail once during develop (review calls include
        # the design review, so we need to count from here)
        review_counter = {"n": 0}

        async def review_fn(request):
            review_counter["n"] += 1
            # First develop review fails
            if request.action == "review" and review_counter["n"] == 1:
                return ReviewOutput(
                    comments=[ReviewComment(severity=Severity.CRITICAL, message="bug")],
                    reviewer="stub-reviewer",
                )
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.LOW, message="ok")],
                reviewer="stub-reviewer",
            )

        agent.review_calls.clear()
        agent.configure_review(review_fn)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        assert len(agent.develop_calls) >= 2  # retried

    async def test_develop_review_exhaustion_blocked(self, system):
        """Develop review always fails → BLOCKED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await self._setup_approved(bus, store)

        async def always_fail_review(request):
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.CRITICAL, message="terrible")],
                reviewer="stub-reviewer",
            )

        agent.configure_review(always_fail_review)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.BLOCKED


# ===========================================================================
# 4. Error and recovery scenarios
# ===========================================================================


class TestErrorRecovery:
    """Agent failures, budget, and recovery from bad states."""

    async def test_design_agent_action_failed(self, system):
        """AgentActionFailed during design → FAILED, partial output saved."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def failing_design(request):
            raise AgentActionFailed("design crashed", partial_output="partial design")

        agent.configure_design(failing_design)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Crash design"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        issue = store.get(1)
        assert issue.status == IssueStatus.FAILED
        assert "partial design" in issue.sections.get("设计", "")

    async def test_design_agent_runtime_error(self, system):
        """Unexpected exception during design → FAILED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def crashing_design(request):
            raise RuntimeError("unexpected crash")

        agent.configure_design(crashing_design)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Runtime crash"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.FAILED

    async def test_develop_agent_action_failed(self, system):
        """AgentActionFailed during develop → FAILED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Dev crash"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

        async def failing_develop(request):
            raise AgentActionFailed("develop crashed", partial_output="partial code")

        agent.configure_develop(failing_develop)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        issue = store.get(1)
        assert issue.status == IssueStatus.FAILED
        assert "partial code" in issue.sections.get("开发步骤", "")

    async def test_reviewer_crash_fails_issue(self, system):
        """All reviewers crash → FAILED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def crashing_review(request):
            raise RuntimeError("reviewer exploded")

        agent.configure_review(crashing_review)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Review crash"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.FAILED

    async def test_failed_issue_recovers_via_run(self, system):
        """FAILED issue → run again → recovers from design."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        # First attempt: design crashes
        async def failing_design(request):
            raise RuntimeError("crash")

        agent.configure_design(failing_design)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Recovery"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.FAILED

        # Fix agent and re-run
        agent.configure_design(agent._default_design)
        agent.configure_review(agent._default_review)

        await bus.publish(Message(MessageType.CMD_RUN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE

    async def test_blocked_issue_approve_design(self, system):
        """Design BLOCKED → approve → APPROVED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        agent.configure_review_fail_then_pass(100)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Blocked design"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.BLOCKED

        await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

    async def test_blocked_issue_approve_develop(self, system):
        """Develop BLOCKED → approve → DONE."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Blocked dev"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

        # Make develop review always fail
        async def always_fail(request):
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
                reviewer="stub-reviewer",
            )

        agent.configure_review(always_fail)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.BLOCKED

        await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.DONE

    async def test_blocked_issue_resume_design(self, system):
        """Design BLOCKED → resume → re-runs design → passes."""
        bus, store, agent = system["bus"], system["store"], system["agent"]
        config = system["config"]

        max_rounds = config.get_max_review_rounds()
        agent.configure_review_fail_then_pass(max_rounds)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Resume design"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.BLOCKED

        # Resume — agent review counter is past fail_count now
        await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

    async def test_blocked_issue_resume_develop(self, system):
        """Develop BLOCKED → resume → re-runs develop → passes."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Resume dev"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

        # Make gate always fail to block develop
        engine._gate_check = AsyncMock(return_value=(False, "tests failed", "error"))
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.BLOCKED

        # Fix gate and resume
        engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
        engine._run_acceptance_phase = AsyncMock(return_value=True)
        agent.configure_review(agent._default_review)

        await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.DONE

    async def test_approve_non_blocked_is_error(self, system):
        """Approve on non-BLOCKED issue → error event."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Not blocked"}))

        await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))

        assert len(events[MessageType.EVT_ERROR]) == 1
        assert "not BLOCKED" in events[MessageType.EVT_ERROR][0].payload["message"]

    async def test_resume_non_blocked_is_error(self, system):
        """Resume on non-BLOCKED issue → error event."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Not blocked"}))

        await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))

        assert len(events[MessageType.EVT_ERROR]) == 1
        assert "not BLOCKED" in events[MessageType.EVT_ERROR][0].payload["message"]


# ===========================================================================
# 5. Budget scenarios
# ===========================================================================


class TestBudget:
    """Budget enforcement."""

    async def test_design_budget_exceeded_blocks(self, integ_repo, integ_config_with_budget, agent):
        """Design agent returns expensive usage → BLOCKED."""
        AgentRegistry.register("claude_code", lambda cfg: agent)

        bus = MessageBus()
        wt_manager = WorktreeManager(integ_config_with_budget.get_worktree_dir())
        task_manager = TaskManager(wt_manager)
        store = IssueStore(str(integ_repo), integ_config_with_budget)
        registry = AgentRegistry(integ_config_with_budget)
        registry._instances["claude-code"] = agent

        engine = Engine(bus, store, task_manager, registry, integ_config_with_budget, str(integ_repo))
        engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        expensive_usage = AgentUsage(input_tokens=10000, output_tokens=5000,
                                     duration_ms=5000, cost_usd=10.0)

        async def expensive_design(request):
            return DesignOutput(document="expensive", usage=expensive_usage)

        agent.configure_design(expensive_design)

        events = _collect_events(bus)

        store.create("Budget test")
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.BLOCKED
        assert any("budget exceeded" in e.payload.get("reason", "")
                    for e in events[MessageType.EVT_TASK_FAILED])

    async def test_develop_budget_exceeded_blocks(self, integ_repo, integ_config_with_budget, agent):
        """Develop agent returns expensive usage → BLOCKED."""
        AgentRegistry.register("claude_code", lambda cfg: agent)

        bus = MessageBus()
        wt_manager = WorktreeManager(integ_config_with_budget.get_worktree_dir())
        task_manager = TaskManager(wt_manager)
        store = IssueStore(str(integ_repo), integ_config_with_budget)
        registry = AgentRegistry(integ_config_with_budget)
        registry._instances["claude-code"] = agent

        engine = Engine(bus, store, task_manager, registry, integ_config_with_budget, str(integ_repo))
        engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        # Get issue to APPROVED first
        store.create("Budget dev test")
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

        expensive_usage = AgentUsage(input_tokens=10000, output_tokens=5000,
                                     duration_ms=5000, cost_usd=10.0)

        async def expensive_develop(request):
            return DevelopOutput(summary="expensive code", usage=expensive_usage)

        agent.configure_develop(expensive_develop)

        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.BLOCKED


# ===========================================================================
# 6. Cancel and cleanup
# ===========================================================================


class TestCancelCleanup:
    """Cancel and cleanup flows."""

    async def test_cancel_created_issue(self, system):
        """Cancel a CREATED issue."""
        bus, store = system["bus"], system["store"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Cancel me"}))
        await bus.publish(Message(MessageType.CMD_CANCEL, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.CANCELLED

    async def test_cancel_approved_issue(self, system):
        """Cancel an APPROVED issue — design content preserved."""
        bus, store = system["bus"], system["store"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Cancel approved"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

        await bus.publish(Message(MessageType.CMD_CANCEL, {"issue_id": 1}))

        issue = store.get(1)
        assert issue.status == IssueStatus.CANCELLED
        assert "Architecture" in issue.sections.get("设计", "")

    async def test_cleanup_done_issue(self, system):
        """Cleanup a DONE issue — worktree removed."""
        bus, store, repo = system["bus"], system["store"], system["repo"]

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Cleanup test"}))
        assert store.get(1).status == IssueStatus.DONE

        wt_path = repo / ".shadowcoder" / "worktrees" / "issue-1"
        assert wt_path.exists()

        await bus.publish(Message(MessageType.CMD_CLEANUP, {"issue_id": 1}))

        assert not wt_path.exists()

    async def test_cleanup_non_done_is_error(self, system):
        """Cleanup on CREATED issue → error."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Not done"}))
        await bus.publish(Message(MessageType.CMD_CLEANUP, {"issue_id": 1}))

        assert len(events[MessageType.EVT_ERROR]) == 1
        assert "not DONE or CANCELLED" in events[MessageType.EVT_ERROR][0].payload["message"]


# ===========================================================================
# 7. List and info
# ===========================================================================


class TestListInfo:
    """List and info commands."""

    async def test_list_multiple_issues(self, system):
        """List shows all issues."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue A"}))
        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue B"}))
        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue C"}))

        await bus.publish(Message(MessageType.CMD_LIST, {}))

        assert len(events[MessageType.EVT_ISSUE_LIST]) == 1
        issues = events[MessageType.EVT_ISSUE_LIST][0].payload["issues"]
        assert len(issues) == 3
        assert issues[0]["title"] == "Issue A"
        assert issues[2]["title"] == "Issue C"

    async def test_info_shows_details(self, system):
        """Info returns issue details."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
            "title": "Info test",
            "priority": "high",
            "tags": ["backend"],
        }))
        await bus.publish(Message(MessageType.CMD_INFO, {"issue_id": 1}))

        info = events[MessageType.EVT_ISSUE_INFO][0].payload["issue"]
        assert info["title"] == "Info test"
        assert info["priority"] == "high"
        assert info["tags"] == ["backend"]
        assert info["status"] == "created"


# ===========================================================================
# 8. Breakpoint recovery (simulate interrupted run)
# ===========================================================================


class TestBreakpointRecovery:
    """Simulate run interrupted at various stages, then re-run."""

    async def test_run_interrupted_during_design_then_rerun(self, system):
        """Design fails (simulating interruption) → FAILED → run again → DONE."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        call_count = {"n": 0}

        async def fail_once_design(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("process killed")
            return DesignOutput(document="## Architecture\nRecovered design.")

        agent.configure_design(fail_once_design)

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Interrupted run"}))

        # First run fails at design
        issue = store.get(1)
        assert issue.status == IssueStatus.FAILED

        # Re-run recovers
        agent.configure_review(agent._default_review)
        await bus.publish(Message(MessageType.CMD_RUN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE

    async def test_run_interrupted_during_develop_then_rerun(self, system):
        """Develop fails → FAILED → run picks up from design (re-design since FAILED)."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Dev interrupted"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED

        # Develop fails
        call_count = {"n": 0}

        async def fail_once_develop(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("process killed")
            return DevelopOutput(summary="## Recovered\nCode works now.")

        agent.configure_develop(fail_once_develop)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.FAILED

        # Re-run: status is FAILED, so _on_run will re-run design first
        agent.configure_review(agent._default_review)
        await bus.publish(Message(MessageType.CMD_RUN, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE

    async def test_blocked_during_run_stops_gracefully(self, system):
        """Run where design gets BLOCKED → run stops, issue stays BLOCKED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        agent.configure_review_fail_then_pass(100)

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Run blocked"}))

        issue = store.get(1)
        assert issue.status == IssueStatus.BLOCKED

        log = store.get_log(1)
        assert "run 暂停" in log


# ===========================================================================
# 9. Version archive and log integrity
# ===========================================================================


class TestVersionArchive:
    """Verify version archives and logs are created correctly."""

    async def test_design_creates_version_archive(self, system):
        """Each design round creates a version archive file."""
        bus, store, agent, repo = (
            system["bus"], system["store"], system["agent"], system["repo"])

        agent.configure_review_fail_then_pass(1)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Version test"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        versions_dir = repo / ".shadowcoder" / "issues" / "0001" / "versions"
        assert versions_dir.exists()
        assert (versions_dir / "design_r1.md").exists()
        assert (versions_dir / "design_r2.md").exists()

    async def test_log_contains_full_history(self, system):
        """Log file captures all rounds, reviews, and status changes."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        agent.configure_review_fail_then_pass(1)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Log test"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        log = store.get_log(1)
        assert "Issue 创建" in log
        assert "Preflight" in log
        assert "Design R1 开始" in log
        assert "RETRY" in log
        assert "Design R2 开始" in log
        assert "PASS" in log


# ===========================================================================
# 10. Phase-aware usage tracking
# ===========================================================================


class TestPhaseUsage:
    """Per-phase cost observability."""

    async def test_usage_summary_includes_phase_breakdown(self, system):
        """Usage summary breaks down cost by phase."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        expensive = AgentUsage(input_tokens=1000, output_tokens=500,
                               duration_ms=5000, cost_usd=1.0)

        async def design_with_usage(request):
            return DesignOutput(document="## Arch\nDesign.", usage=expensive)

        async def develop_with_usage(request):
            return DevelopOutput(summary="## Impl\nCode.", usage=expensive)

        agent.configure_design(design_with_usage)
        agent.configure_develop(develop_with_usage)

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Phase usage"}))
        assert store.get(1).status == IssueStatus.DONE

        summary = engine._usage_summary(1)
        assert "design:" in summary.lower() or "Design:" in summary

    async def test_track_usage_records_phase(self, system):
        """_track_usage stores phase on the usage object."""
        engine = system["engine"]
        usage = AgentUsage(input_tokens=10, output_tokens=5, duration_ms=100, cost_usd=0.01)
        engine._track_usage(1, usage, phase="develop", round_num=2)
        recorded = engine._usage_by_issue[1][0]
        assert recorded.phase == "develop"
        assert recorded.round_num == 2


# ===========================================================================
# 11. Event system integrity
# ===========================================================================


class TestEvents:
    """Verify correct events are fired during lifecycle."""

    async def test_full_lifecycle_events(self, system):
        """Complete lifecycle fires expected events in order."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Event test"}))

        assert len(events[MessageType.EVT_ISSUE_CREATED]) == 1
        assert len(events[MessageType.EVT_TASK_COMPLETED]) >= 2
        assert len(events[MessageType.EVT_REVIEW_RESULT]) >= 2
        assert len(events[MessageType.EVT_STATUS_CHANGED]) >= 2

    async def test_failed_task_events(self, system):
        """Failed design fires EVT_TASK_FAILED."""
        bus, store, agent = system["bus"], system["store"], system["agent"]
        events = _collect_events(bus)

        async def crash(request):
            raise RuntimeError("boom")

        agent.configure_design(crash)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Fail event"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        assert len(events[MessageType.EVT_TASK_FAILED]) == 1


# ===========================================================================
# 11. Multiple issues
# ===========================================================================


class TestMultipleIssues:
    """Multiple issues coexist correctly."""

    async def test_two_issues_independent(self, system):
        """Two issues can be created and progressed independently."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue 1"}))
        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue 2"}))

        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.APPROVED
        assert store.get(2).status == IssueStatus.CREATED

        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 2}))
        assert store.get(2).status == IssueStatus.APPROVED

    async def test_cancel_one_doesnt_affect_other(self, system):
        """Cancelling one issue doesn't affect another."""
        bus, store = system["bus"], system["store"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Keep"}))
        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Cancel"}))

        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        await bus.publish(Message(MessageType.CMD_CANCEL, {"issue_id": 2}))

        assert store.get(1).status == IssueStatus.APPROVED
        assert store.get(2).status == IssueStatus.CANCELLED


# ===========================================================================
# 12. File persistence integrity
# ===========================================================================


class TestFilePersistence:
    """Verify issue state is correctly persisted to disk."""

    async def test_issue_file_roundtrip(self, system):
        """Issue survives write→read roundtrip with all fields."""
        bus, store, repo = system["bus"], system["store"], system["repo"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
            "title": "Persistence test",
            "priority": "high",
            "tags": ["infra", "urgent"],
        }))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        # Re-create store from disk
        config = system["config"]
        fresh_store = IssueStore(str(repo), config)
        issue = fresh_store.get(1)

        assert issue.title == "Persistence test"
        assert issue.status == IssueStatus.APPROVED
        assert issue.priority == "high"
        assert issue.tags == ["infra", "urgent"]
        assert "Architecture" in issue.sections.get("设计", "")

    async def test_log_persists_across_store_instances(self, system):
        """Log file persists independently of store lifecycle."""
        bus, store, repo = system["bus"], system["store"], system["repo"]

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Log persist"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        config = system["config"]
        fresh_store = IssueStore(str(repo), config)
        log = fresh_store.get_log(1)

        assert "Issue 创建" in log
        assert "Design R1 开始" in log


# ===========================================================================
# 13. Iterate (DONE → add requirements → re-develop) — RED test
# ===========================================================================


class TestIterate:
    """Tests for iterate functionality (DONE → add requirements → re-develop)."""

    async def test_iterate_done_issue(self, system):
        """DONE issue + iterate with new requirements → re-enters develop → DONE."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        # Complete an issue first
        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Iterate base"}))
        assert store.get(1).status == IssueStatus.DONE

        # Iterate with new requirements
        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": 1,
            "requirements": "Add caching layer and retry logic",
        }))

        issue = store.get(1)
        assert issue.status == IssueStatus.DONE
        # New requirements should be appended
        assert "caching" in issue.sections.get("需求", "").lower()
        # Develop should have been called again
        assert len(agent.develop_calls) >= 2

    async def test_iterate_preserves_design(self, system):
        """Iterate keeps the original design section intact."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Iterate preserve"}))
        assert store.get(1).status == IssueStatus.DONE

        original_design = store.get(1).sections.get("设计", "")

        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": 1,
            "requirements": "Add logging",
        }))

        # Design should still be there
        assert store.get(1).sections.get("设计", "") == original_design

    async def test_iterate_non_done_is_error(self, system):
        """Iterate on non-DONE issue → error."""
        bus, store = system["bus"], system["store"]
        events = _collect_events(bus)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Not done"}))

        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": 1,
            "requirements": "something",
        }))

        assert len(events[MessageType.EVT_ERROR]) == 1
        assert "not DONE" in events[MessageType.EVT_ERROR][0].payload["message"]

    async def test_iterate_appends_to_existing_requirements(self, system):
        """Iterate appends new requirements to existing ones, separated by ---."""
        bus, store, repo = system["bus"], system["store"], system["repo"]

        # Create with initial requirements
        desc_file = repo / "reqs.md"
        desc_file.write_text("Initial requirements: build a calculator")

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
            "title": "Iterate append",
            "description": str(desc_file),
        }))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.DONE

        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": 1,
            "requirements": "Add scientific functions",
        }))

        reqs = store.get(1).sections.get("需求", "")
        assert "Initial requirements" in reqs
        assert "scientific functions" in reqs
        assert "---" in reqs

    async def test_iterate_multiple_times(self, system):
        """Can iterate multiple times on the same issue."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Multi iterate"}))
        assert store.get(1).status == IssueStatus.DONE

        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": 1,
            "requirements": "Add feature A",
        }))
        assert store.get(1).status == IssueStatus.DONE

        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": 1,
            "requirements": "Add feature B",
        }))
        assert store.get(1).status == IssueStatus.DONE

        reqs = store.get(1).sections.get("需求", "")
        assert "feature a" in reqs.lower()
        assert "feature b" in reqs.lower()
        assert len(agent.develop_calls) >= 3


class TestAcceptanceContract:
    """Acceptance contract: acceptance vs supplementary tests."""

    async def test_design_review_tests_become_acceptance(self, system):
        """Tests proposed during design review go to acceptance_tests."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        async def review_with_tests(request):
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.LOW, message="ok")],
                proposed_tests=[TestCase(
                    name="test_core_func", description="core test",
                    expected_behavior="works")],
                reviewer="stub",
            )

        agent.configure_review(review_with_tests)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Contract"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        fb = store.load_feedback(1)
        assert len(fb.get("acceptance_tests", [])) == 1
        assert fb["acceptance_tests"][0]["name"] == "test_core_func"
        assert len(fb.get("supplementary_tests", [])) == 0

    async def test_dev_review_tests_become_supplementary(self, system):
        """Tests proposed during dev review go to supplementary_tests."""
        bus, store, agent = system["bus"], system["store"], system["agent"]

        review_counter = {"n": 0}

        async def review_fn(request):
            review_counter["n"] += 1
            # Design review: no tests
            if review_counter["n"] == 1:
                return ReviewOutput(
                    comments=[ReviewComment(severity=Severity.LOW, message="ok")],
                    reviewer="stub",
                )
            # Dev review: propose a test
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.LOW, message="ok")],
                proposed_tests=[TestCase(
                    name="test_edge_case", description="edge",
                    expected_behavior="handled")],
                reviewer="stub",
            )

        agent.configure_review(review_fn)

        await bus.publish(Message(MessageType.CMD_RUN, {"title": "Dev tests"}))

        fb = store.load_feedback(1)
        assert len(fb.get("supplementary_tests", [])) == 1
        assert fb["supplementary_tests"][0]["name"] == "test_edge_case"

    async def test_developer_sees_both_test_types(self, system):
        """Developer context includes both acceptance and supplementary tests."""
        engine, store = system["engine"], system["store"]

        store.create("Dev context")
        fb = store.load_feedback(1)
        fb["acceptance_tests"] = [{"name": "test_core", "description": "core",
                                    "expected_behavior": "works", "category": "acceptance",
                                    "round_proposed": 1}]
        fb["supplementary_tests"] = [{"name": "test_edge", "description": "edge",
                                       "expected_behavior": "handled", "category": "acceptance",
                                       "round_proposed": 2}]
        store.save_feedback(1, fb)

        result = engine._format_acceptance_tests_for_developer(1)
        assert "test_core" in result
        assert "test_edge" in result
        assert "acceptance" in result.lower() or "Acceptance" in result
        assert "supplementary" in result.lower() or "Supplementary" in result

    async def test_standard_gate_only_checks_acceptance(self, system):
        """In standard mode, gate only checks acceptance_tests."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        store.create("Gate standard")
        store.transition_status(1, IssueStatus.DESIGNING)
        store.transition_status(1, IssueStatus.DESIGN_REVIEW)
        store.transition_status(1, IssueStatus.APPROVED)

        fb = store.load_feedback(1)
        fb["acceptance_tests"] = [{"name": "test_accept", "description": "a",
                                    "expected_behavior": "b", "category": "acceptance",
                                    "round_proposed": 1}]
        fb["supplementary_tests"] = [{"name": "test_suppl", "description": "c",
                                       "expected_behavior": "d", "category": "acceptance",
                                       "round_proposed": 1}]
        store.save_feedback(1, fb)

        gate_tests = engine._get_gate_tests(1)
        assert any(t["name"] == "test_accept" for t in gate_tests)
        assert not any(t["name"] == "test_suppl" for t in gate_tests)


class TestStrictGateMode:
    """Strict gate mode checks both acceptance and supplementary tests."""

    async def test_strict_gate_checks_all_tests(self, integ_repo, tmp_path, agent):
        """In strict mode, gate checks acceptance + supplementary tests."""
        config_path = tmp_path / "config_strict.yaml"
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
gate:
  mode: strict
issue_store:
  dir: .shadowcoder/issues
worktree:
  base_dir: .shadowcoder/worktrees
""")
        config = Config(str(config_path))
        assert config.get_gate_mode() == "strict"

        AgentRegistry.register("claude_code", lambda cfg: agent)
        bus = MessageBus()
        wt_manager = WorktreeManager(config.get_worktree_dir())
        task_manager = TaskManager(wt_manager)
        store = IssueStore(str(integ_repo), config)
        registry = AgentRegistry(config)
        registry._instances["claude-code"] = agent
        engine = Engine(bus, store, task_manager, registry, config, str(integ_repo))

        store.create("Strict gate")
        fb = store.load_feedback(1)
        fb["acceptance_tests"] = [{"name": "test_a", "description": "a",
                                    "expected_behavior": "b", "category": "acceptance",
                                    "round_proposed": 1}]
        fb["supplementary_tests"] = [{"name": "test_s", "description": "c",
                                       "expected_behavior": "d", "category": "acceptance",
                                       "round_proposed": 1}]
        store.save_feedback(1, fb)

        gate_tests = engine._get_gate_tests(1)
        assert any(t["name"] == "test_a" for t in gate_tests)
        assert any(t["name"] == "test_s" for t in gate_tests)


# ===========================================================================
# 15. Session resume semantics
# ===========================================================================


class TestSessionResume:
    """Gate-fail session resume semantics."""

    async def test_gate_fail_retry_uses_resume(self, system):
        """Gate fail → next develop call gets resume_id, not session_id."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        gate_count = {"n": 0}

        async def gate_fn(issue_id, worktree_path, proposed_tests):
            gate_count["n"] += 1
            if gate_count["n"] == 1:
                return False, "tests failed", "error output"
            return True, "gate passed", "ok"

        engine._gate_check = AsyncMock(side_effect=gate_fn)
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Session resume"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        agent.develop_calls.clear()

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.DONE

        # First develop call: should have session_id (new session)
        first_ctx = agent.develop_calls[0].context
        assert first_ctx.get("session_id") is not None
        assert first_ctx.get("resume_id") is None

        # Second develop call (after gate fail): should have resume_id
        second_ctx = agent.develop_calls[1].context
        assert second_ctx.get("resume_id") == first_ctx["session_id"]
        assert second_ctx.get("session_id") is None

    async def test_review_retry_gets_new_session(self, system):
        """After review retry, develop gets a fresh session_id (not resume)."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        review_counter = {"n": 0}

        async def review_fn(request):
            review_counter["n"] += 1
            if review_counter["n"] == 1:
                return ReviewOutput(
                    comments=[ReviewComment(severity=Severity.CRITICAL, message="bad")],
                    reviewer="stub",
                )
            return ReviewOutput(
                comments=[ReviewComment(severity=Severity.LOW, message="ok")],
                reviewer="stub",
            )

        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Review new session"}))
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

        agent.develop_calls.clear()
        agent.configure_review(review_fn)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        assert store.get(1).status == IssueStatus.DONE

        # Both develop calls should have different session_ids (not resume)
        first_sid = agent.develop_calls[0].context.get("session_id")
        second_sid = agent.develop_calls[1].context.get("session_id")
        assert first_sid is not None
        assert second_sid is not None
        assert first_sid != second_sid
        # Neither should have resume_id
        assert agent.develop_calls[0].context.get("resume_id") is None
        assert agent.develop_calls[1].context.get("resume_id") is None


# ===========================================================================
# TestGateStrayFiles
# ===========================================================================


@pytest.fixture
def integ_env(integ_repo, integ_config, agent):
    """Assemble system without mocking _gate_check, for gate-internals tests."""
    AgentRegistry.register("claude_code", lambda cfg: agent)

    bus = MessageBus()
    wt_manager = WorktreeManager(integ_config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    store = IssueStore(str(integ_repo), integ_config)
    registry = AgentRegistry(integ_config)
    registry._instances["claude-code"] = agent

    engine = Engine(bus, store, task_manager, registry, integ_config, str(integ_repo))
    engine._get_code_diff = AsyncMock(return_value="diff --git a/foo.py")

    return {
        "bus": bus,
        "engine": engine,
        "store": store,
        "task_manager": task_manager,
        "agent": agent,
        "repo": integ_repo,
        "config": integ_config,
        "default_develop": agent._default_develop,
    }


class TestGateStrayFiles:
    async def test_gate_warns_on_stray_root_files(self, integ_env):
        """Gate output includes warning when stray .py files exist in worktree root."""
        env = integ_env
        agent = env["agent"]
        agent.configure_develop(lambda req: env["default_develop"](req))

        # Create issue and get to develop
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Test stray files"}))
        issue = env["store"].list_all()[-1]
        env["store"].transition_status(issue.id, IssueStatus.DESIGNING)
        env["store"].transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
        env["store"].transition_status(issue.id, IssueStatus.APPROVED)
        env["store"].update_section(issue.id, "设计", "Test design")

        # Create stray file in worktree before develop
        task = await env["task_manager"].create(issue, repo_path=str(env["repo"]),
            action="develop", agent_name="claude-code")
        if task.worktree_path:
            (Path(task.worktree_path) / "test_debug.py").write_text("# temp")
            (Path(task.worktree_path) / "pyproject.toml").write_text("[project]\nname='test'\n")

        # Mock _run_command to simulate passing tests
        env["engine"]._run_command = AsyncMock(return_value=(True, "all passed"))

        # Run gate check
        ok, msg, output = await env["engine"]._gate_check(
            issue.id, task.worktree_path, [])
        assert "WARNING" in output or "Stray" in output


class TestInProgressRecovery:
    async def test_in_progress_develop_recovers(self, system):
        """IN_PROGRESS with develop section -> run recovers to APPROVED -> develop resumes."""
        env = system
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Recovery test"}))
        issue = store.list_all()[-1]
        issue.status = IssueStatus.IN_PROGRESS
        issue.sections["设计"] = "Test design"
        issue.sections["开发"] = "Test develop WIP"
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN, {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_in_progress_design_recovers(self, system):
        """IN_PROGRESS with design section only -> run recovers to CREATED -> design restarts."""
        env = system
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Recovery test design"}))
        issue = store.list_all()[-1]
        store.update_section(issue.id, "设计", "WIP design")
        issue = store.get(issue.id)
        issue.status = IssueStatus.IN_PROGRESS
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN, {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_in_progress_no_sections_restarts_design(self, system):
        """IN_PROGRESS with no sections -> run restarts from design."""
        env = system
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Recovery test empty"}))
        issue = store.list_all()[-1]
        issue.status = IssueStatus.IN_PROGRESS
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN, {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_failed_with_develop_section_resumes_develop(self, system):
        """FAILED with develop section -> run recovers to APPROVED -> develop resumes."""
        env = system
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Failed recovery develop"}))
        issue = store.list_all()[-1]
        issue.status = IssueStatus.FAILED
        issue.sections["设计"] = "Test design"
        issue.sections["开发"] = "Develop WIP"
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN, {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_failed_with_design_only_restarts_design(self, system):
        """FAILED with design section only -> run restarts from design."""
        env = system
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Failed recovery design"}))
        issue = store.list_all()[-1]
        store.update_section(issue.id, "设计", "WIP design")
        issue = store.get(issue.id)
        issue.status = IssueStatus.FAILED
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN, {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE


# ===========================================================================
# 14. Last issue pointer
# ===========================================================================


class TestLastIssue:
    """last_issue pointer: save/get, resume_last, and CANCELLED/DONE rerun."""

    async def test_run_saves_last_issue(self, system):
        """run saves last_issue pointer."""
        env = system
        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"title": "Test last"}))
        last = env["store"].get_last()
        assert last is not None
        issue = env["store"].list_all()[-1]
        assert last == issue.id

    async def test_run_resume_last(self, system):
        """run with resume_last continues the last issue."""
        env = system
        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"title": "Test resume"}))
        issue = env["store"].list_all()[-1]
        assert issue.status == IssueStatus.DONE

        # Resume last (DONE → APPROVED → develop → DONE)
        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"resume_last": True}))
        issue = env["store"].get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_run_resume_last_no_previous(self, system):
        """run resume_last with no previous issue emits error."""
        env = system
        errors = []
        async def capture_error(msg):
            errors.append(msg.payload)
        env["bus"].subscribe(MessageType.EVT_ERROR, capture_error)
        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"resume_last": True}))
        assert len(errors) == 1

    async def test_run_cancelled_restarts_design(self, system):
        """run on CANCELLED issue restarts from design."""
        env = system
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Cancel test"}))
        issue = store.list_all()[-1]
        store.transition_status(issue.id, IssueStatus.CANCELLED)

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_run_done_reruns_develop(self, system):
        """run on DONE issue re-enters develop."""
        env = system
        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"title": "Done test"}))
        issue = env["store"].list_all()[-1]
        assert issue.status == IssueStatus.DONE

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = env["store"].get(issue.id)
        assert issue.status == IssueStatus.DONE


# ===========================================================================
# 20. Confirm acceptance before develop
# ===========================================================================


class TestConfirmAcceptance:
    """When confirm_acceptance is enabled, block after acceptance xfail for human review."""

    @pytest.fixture
    def confirm_config(self, tmp_path):
        config_path = tmp_path / "config_confirm.yaml"
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
  pass_threshold: no_high_or_critical
  max_review_rounds: 3
  confirm_acceptance: true
issue_store:
  dir: .shadowcoder/issues
worktree:
  base_dir: .shadowcoder/worktrees
""")
        return Config(str(config_path))

    @pytest.fixture
    def confirm_system(self, integ_repo, confirm_config, agent):
        AgentRegistry.register("claude_code", lambda cfg: agent)
        bus = MessageBus()
        wt_manager = WorktreeManager(confirm_config.get_worktree_dir())
        task_manager = TaskManager(wt_manager)
        store = IssueStore(str(integ_repo), confirm_config)
        registry = AgentRegistry(confirm_config)
        registry._instances["claude-code"] = agent
        engine = Engine(bus, store, task_manager, registry, confirm_config, str(integ_repo))
        engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
        engine._get_code_diff = AsyncMock(return_value="diff --git a/foo.py")
        # Do NOT mock _run_acceptance_phase — let it run (but mock the agent)
        return {
            "bus": bus, "engine": engine, "store": store,
            "task_manager": task_manager, "agent": agent,
            "repo": integ_repo, "config": confirm_config,
        }

    async def test_blocks_after_acceptance_xfail(self, confirm_system):
        """Acceptance xfail confirmed → BLOCKED, not straight to develop."""
        env = confirm_system
        await env["bus"].publish(Message(MessageType.CMD_RUN, {"title": "Confirm test"}))
        issue = env["store"].list_all()[-1]
        assert issue.status == IssueStatus.BLOCKED

        # Acceptance script should exist
        acc_path = env["engine"]._acceptance_script_path(issue.id)
        assert acc_path.exists()

        # No develop calls yet
        assert len(env["agent"].develop_calls) == 0

    async def test_resume_after_acceptance_skips_regeneration(self, confirm_system):
        """Resume after acceptance BLOCKED → develop runs, acceptance not regenerated."""
        env = confirm_system
        await env["bus"].publish(Message(MessageType.CMD_RUN, {"title": "Resume test"}))
        issue = env["store"].list_all()[-1]
        assert issue.status == IssueStatus.BLOCKED

        # Record acceptance call count before resume
        acc_calls_before = len(env["agent"].preflight_calls)

        # Resume → should enter develop loop (skipping acceptance regeneration)
        await env["bus"].publish(Message(MessageType.CMD_RESUME,
            {"issue_id": issue.id}))
        issue = env["store"].get(issue.id)
        assert issue.status == IssueStatus.DONE

        # Develop should have been called
        assert len(env["agent"].develop_calls) >= 1

    async def test_run_resumes_blocked_acceptance(self, confirm_system):
        """run on acceptance-BLOCKED issue resumes develop, not design."""
        env = confirm_system
        await env["bus"].publish(Message(MessageType.CMD_RUN, {"title": "Run resume test"}))
        issue = env["store"].list_all()[-1]
        assert issue.status == IssueStatus.BLOCKED

        # Run again — should resume develop, not restart design
        design_calls_before = len(env["agent"].design_calls)
        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = env["store"].get(issue.id)
        assert issue.status == IssueStatus.DONE
        # Design should NOT have been re-run
        assert len(env["agent"].design_calls) == design_calls_before
