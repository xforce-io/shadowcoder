"""
End-to-end test: create a real repo, run the full issue lifecycle with real
filesystem I/O, real git worktrees, and real issue markdown persistence.

Agent is a stub, but everything else is real.
"""
import asyncio
import subprocess
from pathlib import Path

import pytest

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import AgentRequest, DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput, ReviewComment, Severity
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import IssueStatus, TaskStatus
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.worktree import WorktreeManager


class E2EAgent(BaseAgent):
    """A deterministic agent for e2e testing.
    Simulates realistic behavior: produces different content per action,
    and can be configured to fail reviews on demand."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.execute_calls: list[AgentRequest] = []  # tracks design/develop calls
        self.review_calls: list[AgentRequest] = []
        self._review_fail_count = 0  # how many review calls should fail before passing
        self._review_call_counter = 0

    def set_review_fail_count(self, n: int):
        """Make the next N review calls return NOT PASSED."""
        self._review_fail_count = n
        self._review_call_counter = 0

    async def preflight(self, request: AgentRequest) -> PreflightOutput:
        return PreflightOutput(feasibility="high", estimated_complexity="moderate")

    async def design(self, request: AgentRequest) -> DesignOutput:
        self.execute_calls.append(request)
        document = """## Architecture
A simple calculator module with add, subtract, multiply, divide functions.

## API
- `calc.add(a, b) -> float`
- `calc.subtract(a, b) -> float`
- `calc.multiply(a, b) -> float`
- `calc.divide(a, b) -> float` (raises ValueError on division by zero)

## Testing Strategy
Unit tests for each function including edge cases."""
        return DesignOutput(document=document)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        self.execute_calls.append(request)
        summary = """## Implementation
Created `calc.py` with four arithmetic functions.
Created `test_calc.py` with 8 test cases.

### Files Changed
- `calc.py` (new)
- `test_calc.py` (new)"""
        return DevelopOutput(summary=summary)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        self.review_calls.append(request)
        self._review_call_counter += 1

        if self._review_call_counter <= self._review_fail_count:
            return ReviewOutput(
                comments=[
                    ReviewComment(
                        severity=Severity.CRITICAL,
                        message=f"Review round {self._review_call_counter}: needs improvement",
                    ),
                ],
                reviewer="e2e-reviewer",
            )
        return ReviewOutput(
            comments=[
                ReviewComment(
                    severity=Severity.LOW,
                    message="Minor: consider adding docstrings",
                ),
            ],
            reviewer="e2e-reviewer",
        )


@pytest.fixture
def e2e_repo(tmp_path):
    """Create a realistic test repo with some initial content."""
    repo = tmp_path / "calculator-project"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "Initial commit"],
                   cwd=str(repo), check=True, capture_output=True)
    # Add a README
    (repo / "README.md").write_text("# Calculator Project\nA simple calculator.\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add README"],
                   cwd=str(repo), check=True, capture_output=True)
    return repo


@pytest.fixture
def e2e_config(tmp_path):
    """Config pointing to our test setup."""
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
def e2e_agent():
    return E2EAgent({"type": "claude_code"})


@pytest.fixture
def e2e_system(e2e_repo, e2e_config, e2e_agent):
    """Wire up the full system with real components."""
    # Register our e2e agent
    AgentRegistry.register("claude_code", lambda cfg: e2e_agent)

    bus = MessageBus()
    wt_manager = WorktreeManager(e2e_config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    issue_store = IssueStore(str(e2e_repo), e2e_config)
    registry = AgentRegistry(e2e_config)
    registry._instances["claude-code"] = e2e_agent

    engine = Engine(bus, issue_store, task_manager, registry, e2e_config, str(e2e_repo))
    # Mock gate and diff (no real test suite in e2e test repo)
    from unittest.mock import AsyncMock
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", ""))
    engine._get_code_diff = AsyncMock(return_value="")

    return {
        "bus": bus,
        "engine": engine,
        "store": issue_store,
        "task_manager": task_manager,
        "agent": e2e_agent,
        "repo": e2e_repo,
        "config": e2e_config,
    }


# ---- Test Cases ----


async def test_e2e_happy_path(e2e_system):
    """Full lifecycle: create → design → develop → done.
    Verify real files, real git worktrees, real state transitions."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]
    repo = e2e_system["repo"]
    agent = e2e_system["agent"]

    events = {t: [] for t in MessageType}
    for mt in MessageType:
        async def _handler(msg, _mt=mt):
            events[_mt].append(msg)
        bus.subscribe(mt, _handler)

    # --- Step 1: Create Issue ---
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "Implement calculator module",
        "priority": "high",
        "tags": ["feature", "math"],
    }))

    # Verify issue file was created on disk
    issue_file = repo / ".shadowcoder" / "issues" / "0001.md"
    assert issue_file.exists(), f"Issue file not created at {issue_file}"

    issue = store.get(1)
    assert issue.title == "Implement calculator module"
    assert issue.status == IssueStatus.CREATED
    assert issue.priority == "high"
    assert issue.tags == ["feature", "math"]
    assert len(events[MessageType.EVT_ISSUE_CREATED]) == 1

    # Verify file content is valid markdown with frontmatter
    content = issue_file.read_text()
    assert "---" in content
    assert "title: Implement calculator module" in content
    assert "status: created" in content

    # --- Step 2: Design ---
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.APPROVED
    assert "Architecture" in issue.sections.get("设计", "")
    assert "Design Review" in issue.sections
    assert "PASSED" in issue.sections["Design Review"]

    # Verify worktree was created
    wt_dir = repo / ".shadowcoder" / "worktrees" / "issue-1"
    assert wt_dir.exists(), f"Worktree not created at {wt_dir}"

    # Verify branch was created
    result = subprocess.run(
        ["git", "branch", "--list", "fix/1-*"],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert "fix/1-" in result.stdout

    # Verify agent was called correctly
    assert len(agent.execute_calls) == 1
    assert agent.execute_calls[0].action == "design"
    assert agent.execute_calls[0].issue.title == "Implement calculator module"

    # --- Step 3: Develop ---
    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert "Implementation" in issue.sections.get("开发步骤", "")
    assert "Dev Review" in issue.sections

    # Second worktree created for develop
    assert len(agent.execute_calls) == 2
    assert agent.execute_calls[1].action == "develop"

    # --- Verify final state on disk ---
    final_content = issue_file.read_text()
    assert "status: done" in final_content

    # Verify all events were fired
    assert len(events[MessageType.EVT_TASK_COMPLETED]) >= 2  # design, develop


async def test_e2e_design_review_retry(e2e_system):
    """Design review fails twice, then passes on third attempt.
    Verify retry logic, section overwriting, and event sequence."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]
    agent = e2e_system["agent"]

    # Make review fail twice (CRITICAL → retry)
    agent.set_review_fail_count(2)

    review_events = []
    async def _on_review(m): review_events.append(m)
    bus.subscribe(MessageType.EVT_REVIEW_RESULT, _on_review)
    status_events = []
    async def _on_status(m): status_events.append(m)
    bus.subscribe(MessageType.EVT_STATUS_CHANGED, _on_status)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Retry test"}))
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.APPROVED  # eventually passes

    # Agent was called 3 times (2 retries + 1 success)
    design_calls = [c for c in agent.execute_calls if c.action == "design"]
    assert len(design_calls) == 3

    # 3 review results
    assert len(review_events) == 3
    assert not review_events[0].payload["passed"]
    assert not review_events[1].payload["passed"]
    assert review_events[2].payload["passed"]

    # Design Review section has the latest summary only
    review_content = issue.sections.get("Design Review", "")
    assert "PASSED" in review_content

    # Log file contains full history (all rounds)
    log = store.get_log(1)
    assert "needs improvement" in log
    assert "RETRY" in log


async def test_e2e_review_exhaustion_and_approve(e2e_system):
    """Review fails max_review_rounds times → BLOCKED.
    Then human approves → APPROVED."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]
    agent = e2e_system["agent"]
    config = e2e_system["config"]

    # Make review fail more times than max_review_rounds
    agent.set_review_fail_count(10)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Blocked test"}))
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED

    # Agent was called exactly max_review_rounds times
    design_calls = [c for c in agent.execute_calls if c.action == "design"]
    assert len(design_calls) == config.get_max_review_rounds()

    # Human approves
    await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.APPROVED


async def test_e2e_review_exhaustion_and_resume(e2e_system):
    """Review fails → BLOCKED → human resumes → passes this time."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]
    agent = e2e_system["agent"]
    config = e2e_system["config"]

    max_rounds = config.get_max_review_rounds()
    # Fail exactly max_rounds times, then pass
    agent.set_review_fail_count(max_rounds)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Resume test"}))
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    assert store.get(1).status == IssueStatus.BLOCKED

    # Resume — review counter is past fail_count, so next reviews will pass
    await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))

    assert store.get(1).status == IssueStatus.APPROVED


async def test_e2e_cancel(e2e_system):
    """Create and cancel an issue. Verify sections and worktree preserved."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Cancel test"}))

    # Run design first so there's content to preserve
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED

    # Cancel
    await bus.publish(Message(MessageType.CMD_CANCEL, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.CANCELLED
    # Sections content preserved
    assert "Architecture" in issue.sections.get("设计", "")


async def test_e2e_list_and_info(e2e_system):
    """Verify list and info commands work end-to-end."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]

    list_events = []
    info_events = []
    async def _on_list(m): list_events.append(m)
    async def _on_info(m): info_events.append(m)
    bus.subscribe(MessageType.EVT_ISSUE_LIST, _on_list)
    bus.subscribe(MessageType.EVT_ISSUE_INFO, _on_info)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue A"}))
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Issue B"}))

    # List
    await bus.publish(Message(MessageType.CMD_LIST, {}))
    assert len(list_events) == 1
    assert len(list_events[0].payload["issues"]) == 2
    assert list_events[0].payload["issues"][0]["title"] == "Issue A"
    assert list_events[0].payload["issues"][1]["title"] == "Issue B"

    # Info
    await bus.publish(Message(MessageType.CMD_INFO, {"issue_id": 1}))
    assert len(info_events) == 1
    assert info_events[0].payload["issue"]["title"] == "Issue A"
    assert info_events[0].payload["issue"]["status"] == "created"


async def test_e2e_issue_file_integrity(e2e_system):
    """Verify the markdown file content is correct after full lifecycle."""
    bus = e2e_system["bus"]
    store = e2e_system["store"]
    repo = e2e_system["repo"]

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "File integrity test",
        "priority": "medium",
        "tags": ["e2e"],
    }))
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    # Read raw file and verify structure
    import frontmatter
    issue_file = repo / ".shadowcoder" / "issues" / "0001.md"
    post = frontmatter.load(str(issue_file))

    # Frontmatter checks
    assert post["id"] == 1
    assert post["title"] == "File integrity test"
    assert post["status"] == "approved"
    assert post["priority"] == "medium"
    assert post["tags"] == ["e2e"]

    # Content checks — should have sections
    assert "<!-- section: 设计 -->" in post.content
    assert "<!-- section: Design Review -->" in post.content
    assert "Architecture" in post.content
