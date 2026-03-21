# ShadowCoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an Agent-based issue management and development system with TUI, pluggable agents, and automated review loops.

**Architecture:** Three-layer message bus architecture — CLI layer (Textual TUI), Core layer (Engine/IssueStore/TaskManager/WorktreeManager), Agent layer (BaseAgent with pluggable implementations). Layers communicate through an async MessageBus.

**Tech Stack:** Python 3.12+, Textual (TUI), PyYAML, python-frontmatter, asyncio, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-21-shadowcoder-design.md`

---

## File Structure

```
shadowcoder/
├── pyproject.toml
├── src/shadowcoder/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli/
│   │   ├── __init__.py
│   │   └── tui/
│   │       ├── __init__.py
│   │       └── app.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── bus.py
│   │   ├── config.py
│   │   ├── engine.py
│   │   ├── issue_store.py
│   │   ├── models.py
│   │   ├── task_manager.py
│   │   └── worktree.py
│   └── agents/
│       ├── __init__.py
│       ├── base.py
│       ├── registry.py
│       └── claude_code.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── test_models.py
│   │   ├── test_config.py
│   │   ├── test_bus.py
│   │   ├── test_issue_store.py
│   │   ├── test_worktree.py
│   │   ├── test_task_manager.py
│   │   └── test_engine.py
│   └── agents/
│       ├── __init__.py
│       ├── test_registry.py
│       └── test_claude_code.py
└── docs/
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/shadowcoder/__init__.py`
- Create: `src/shadowcoder/__main__.py`
- Create: `src/shadowcoder/cli/__init__.py`
- Create: `src/shadowcoder/cli/tui/__init__.py`
- Create: `src/shadowcoder/core/__init__.py`
- Create: `src/shadowcoder/agents/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/core/__init__.py`
- Create: `tests/agents/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "shadowcoder"
version = "0.1.0"
description = "Agent-based issue management and development system"
requires-python = ">=3.12"
dependencies = [
    "textual>=3.0",
    "pyyaml>=6.0",
    "python-frontmatter>=1.1",
]

[project.scripts]
shadowcoder = "shadowcoder.cli.tui.app:main"

[tool.hatch.build.targets.wheel]
packages = ["src/shadowcoder"]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create package __init__.py files**

All `__init__.py` files are empty except the root:

`src/shadowcoder/__init__.py`:
```python
"""ShadowCoder: Agent-based issue management and development system."""
```

`src/shadowcoder/__main__.py`:
```python
from shadowcoder.cli.tui.app import main

if __name__ == "__main__":
    main()
```

Create empty `__init__.py` in:
- `src/shadowcoder/cli/__init__.py`
- `src/shadowcoder/cli/tui/__init__.py`
- `src/shadowcoder/core/__init__.py`
- `src/shadowcoder/agents/__init__.py`
- `tests/__init__.py`
- `tests/core/__init__.py`
- `tests/agents/__init__.py`

- [ ] **Step 3: Create tests/conftest.py with shared fixtures**

```python
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary git repo for testing."""
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=str(tmp_path), check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config file with default values."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""\
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
    codex:
      type: codex

reviewers:
  design: [claude-code]
  develop: [claude-code]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 3

logging:
  dir: /tmp/shadowcoder-test/logs
  level: INFO

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
""")
    return config_path
```

- [ ] **Step 4: Install in dev mode and verify**

Run: `cd /Users/xupeng/dev/github/shadowcoder && pip install -e ".[dev]"`
Expected: Successful install

Run: `python -c "import shadowcoder; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: project scaffolding with package structure and test fixtures"
```

---

### Task 2: Data Models

**Files:**
- Create: `src/shadowcoder/core/models.py`
- Create: `tests/core/test_models.py`

- [ ] **Step 1: Write tests for models**

```python
# tests/core/test_models.py
from datetime import datetime
from shadowcoder.core.models import (
    IssueStatus, Severity, TaskStatus,
    ReviewComment, ReviewResult, Issue, Task,
    InvalidTransitionError, VALID_TRANSITIONS,
)


def test_issue_status_values():
    assert IssueStatus.CREATED.value == "created"
    assert IssueStatus.BLOCKED.value == "blocked"
    assert IssueStatus.CANCELLED.value == "cancelled"


def test_task_status_values():
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.COMPLETED.value == "completed"


def test_issue_defaults():
    issue = Issue(
        id=1, title="test", status=IssueStatus.CREATED,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )
    assert issue.tags == []
    assert issue.assignee is None
    assert issue.sections == {}


def test_task_default_status():
    task = Task(
        task_id="abc", issue_id=1, repo_path="/tmp",
        action="design", agent_name="claude-code",
    )
    assert task.status == TaskStatus.RUNNING


def test_review_result():
    r = ReviewResult(
        passed=False,
        comments=[
            ReviewComment(severity=Severity.HIGH, message="bad design"),
            ReviewComment(severity=Severity.LOW, message="minor style"),
        ],
        reviewer="claude-code",
    )
    assert not r.passed
    assert len(r.comments) == 2


def test_valid_transitions_designing():
    assert IssueStatus.DESIGN_REVIEW in VALID_TRANSITIONS[IssueStatus.DESIGNING]
    assert IssueStatus.FAILED in VALID_TRANSITIONS[IssueStatus.DESIGNING]


def test_valid_transitions_blocked():
    """BLOCKED can go to DESIGNING, DEVELOPING, APPROVED, TESTING, CANCELLED"""
    blocked = VALID_TRANSITIONS[IssueStatus.BLOCKED]
    assert IssueStatus.DESIGNING in blocked
    assert IssueStatus.DEVELOPING in blocked
    assert IssueStatus.APPROVED in blocked
    assert IssueStatus.CANCELLED in blocked


def test_invalid_transition_error():
    err = InvalidTransitionError(IssueStatus.CREATED, IssueStatus.DONE)
    assert "created" in str(err)
    assert "done" in str(err)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_models.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement models**

```python
# src/shadowcoder/core/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class IssueStatus(Enum):
    CREATED = "created"
    DESIGNING = "designing"
    DESIGN_REVIEW = "design_review"
    APPROVED = "approved"
    DEVELOPING = "developing"
    DEV_REVIEW = "dev_review"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid state transitions for IssueStatus
VALID_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.CREATED: {IssueStatus.DESIGNING, IssueStatus.CANCELLED},
    IssueStatus.DESIGNING: {IssueStatus.DESIGN_REVIEW, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DESIGN_REVIEW: {IssueStatus.DESIGNING, IssueStatus.APPROVED, IssueStatus.BLOCKED, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.APPROVED: {IssueStatus.DEVELOPING, IssueStatus.CANCELLED},
    IssueStatus.DEVELOPING: {IssueStatus.DEV_REVIEW, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DEV_REVIEW: {IssueStatus.DEVELOPING, IssueStatus.TESTING, IssueStatus.BLOCKED, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.TESTING: {IssueStatus.DONE, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DONE: set(),
    IssueStatus.FAILED: {IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.TESTING, IssueStatus.CANCELLED},
    IssueStatus.BLOCKED: {IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.APPROVED, IssueStatus.TESTING, IssueStatus.CANCELLED},
    IssueStatus.CANCELLED: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, from_status: IssueStatus, to_status: IssueStatus):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Invalid transition: {from_status.value} → {to_status.value}")


@dataclass
class ReviewComment:
    severity: Severity
    message: str
    location: str | None = None


@dataclass
class ReviewResult:
    passed: bool
    comments: list[ReviewComment]
    reviewer: str


@dataclass
class Issue:
    id: int
    title: str
    status: IssueStatus
    priority: str
    created: datetime
    updated: datetime
    tags: list[str] = field(default_factory=list)
    assignee: str | None = None
    sections: dict[str, str] = field(default_factory=dict)


@dataclass
class Task:
    """Runtime concept: one task = one phase execution of an issue."""
    task_id: str
    issue_id: int
    repo_path: str
    action: str
    agent_name: str
    worktree_path: str | None = None
    status: TaskStatus = TaskStatus.RUNNING
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/models.py tests/core/test_models.py
git commit -m "feat: add core data models with status transitions"
```

---

### Task 3: Config

**Files:**
- Create: `src/shadowcoder/core/config.py`
- Create: `tests/core/test_config.py`

- [ ] **Step 1: Write tests for Config**

```python
# tests/core/test_config.py
import pytest
from shadowcoder.core.config import Config


def test_load_config(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_default_agent() == "claude-code"


def test_get_agent_config(tmp_config):
    config = Config(str(tmp_config))
    ac = config.get_agent_config("claude-code")
    assert ac["type"] == "claude_code"


def test_get_agent_config_missing(tmp_config):
    config = Config(str(tmp_config))
    with pytest.raises(KeyError):
        config.get_agent_config("nonexistent")


def test_get_available_agents(tmp_config):
    config = Config(str(tmp_config))
    agents = config.get_available_agents()
    assert "claude-code" in agents
    assert "codex" in agents


def test_get_reviewers(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_reviewers("design") == ["claude-code"]
    assert config.get_reviewers("develop") == ["claude-code"]


def test_get_max_review_rounds(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_max_review_rounds() == 3


def test_get_max_review_rounds_default(tmp_path):
    """When review_policy is missing, default to 3."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agents:\n  default: x\n  available:\n    x:\n      type: x\n")
    config = Config(str(config_path))
    assert config.get_max_review_rounds() == 3


def test_get_issue_dir(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_issue_dir() == ".shadowcoder/issues"


def test_get_worktree_dir(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_worktree_dir() == ".shadowcoder/worktrees"


def test_get_log_dir(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_log_dir() == "/tmp/shadowcoder-test/logs"


def test_get_log_level(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_log_level() == "INFO"


def test_missing_config_file():
    with pytest.raises(FileNotFoundError):
        Config("/nonexistent/config.yaml")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Config**

```python
# src/shadowcoder/core/config.py
from __future__ import annotations

from pathlib import Path

import yaml


class Config:
    def __init__(self, path: str = "~/.shadowcoder/config.yaml"):
        resolved = Path(path).expanduser()
        if not resolved.exists():
            raise FileNotFoundError(f"Config file not found: {resolved}")
        with open(resolved) as f:
            self._data: dict = yaml.safe_load(f) or {}

    def get_default_agent(self) -> str:
        return self._data["agents"]["default"]

    def get_agent_config(self, name: str) -> dict:
        return self._data["agents"]["available"][name]

    def get_available_agents(self) -> list[str]:
        return list(self._data["agents"]["available"].keys())

    def get_reviewers(self, stage: str) -> list[str]:
        return self._data.get("reviewers", {}).get(stage, [])

    def get_max_review_rounds(self) -> int:
        return self._data.get("review_policy", {}).get("max_review_rounds", 3)

    def get_issue_dir(self) -> str:
        return self._data.get("issue_store", {}).get("dir", ".shadowcoder/issues")

    def get_worktree_dir(self) -> str:
        return self._data.get("worktree", {}).get("base_dir", ".shadowcoder/worktrees")

    def get_log_dir(self) -> str:
        return self._data.get("logging", {}).get("dir", "~/.shadowcoder/logs")

    def get_log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/config.py tests/core/test_config.py
git commit -m "feat: add Config with typed accessors and defaults"
```

---

### Task 4: MessageBus

**Files:**
- Create: `src/shadowcoder/core/bus.py`
- Create: `tests/core/test_bus.py`

- [ ] **Step 1: Write tests for MessageBus**

```python
# tests/core/test_bus.py
import pytest
from shadowcoder.core.bus import MessageBus, MessageType, Message


async def test_publish_subscribe():
    bus = MessageBus()
    received = []

    async def handler(msg: Message):
        received.append(msg)

    bus.subscribe(MessageType.EVT_ISSUE_CREATED, handler)
    await bus.publish(Message(MessageType.EVT_ISSUE_CREATED, {"id": 1}))

    assert len(received) == 1
    assert received[0].payload["id"] == 1


async def test_no_subscribers():
    bus = MessageBus()
    # Should not raise
    await bus.publish(Message(MessageType.EVT_ERROR, {"message": "test"}))


async def test_multiple_subscribers():
    bus = MessageBus()
    results = []

    async def h1(msg):
        results.append("h1")

    async def h2(msg):
        results.append("h2")

    bus.subscribe(MessageType.CMD_LIST, h1)
    bus.subscribe(MessageType.CMD_LIST, h2)
    await bus.publish(Message(MessageType.CMD_LIST, {}))

    assert results == ["h1", "h2"]


async def test_handler_exception_isolated():
    """A failing handler should not prevent other handlers from running."""
    bus = MessageBus()
    results = []

    async def bad_handler(msg):
        raise RuntimeError("boom")

    async def good_handler(msg):
        results.append("ok")

    bus.subscribe(MessageType.CMD_LIST, bad_handler)
    bus.subscribe(MessageType.CMD_LIST, good_handler)
    await bus.publish(Message(MessageType.CMD_LIST, {}))

    assert results == ["ok"]


async def test_message_with_task_id():
    msg = Message(MessageType.CMD_DESIGN, {"issue_id": 1}, task_id="abc123")
    assert msg.task_id == "abc123"


async def test_different_types_isolated():
    bus = MessageBus()
    results = []

    async def handler(msg):
        results.append(msg.type)

    bus.subscribe(MessageType.CMD_LIST, handler)
    await bus.publish(Message(MessageType.CMD_DESIGN, {}))

    assert results == []  # CMD_DESIGN has no subscribers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_bus.py -v`
Expected: FAIL

- [ ] **Step 3: Implement MessageBus**

```python
# src/shadowcoder/core/bus.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


class MessageType(Enum):
    # Commands (CLI → Engine)
    CMD_CREATE_ISSUE = "cmd.create_issue"
    CMD_DESIGN = "cmd.design"
    CMD_DEVELOP = "cmd.develop"
    CMD_TEST = "cmd.test"
    CMD_LIST = "cmd.list"
    CMD_INFO = "cmd.info"
    CMD_RESUME = "cmd.resume"
    CMD_APPROVE = "cmd.approve"
    CMD_CANCEL = "cmd.cancel"

    # Events (Engine → CLI)
    EVT_ISSUE_CREATED = "evt.issue_created"
    EVT_STATUS_CHANGED = "evt.status_changed"
    EVT_AGENT_OUTPUT = "evt.agent_output"
    EVT_REVIEW_RESULT = "evt.review_result"
    EVT_TASK_STARTED = "evt.task_started"
    EVT_TASK_COMPLETED = "evt.task_completed"
    EVT_TASK_FAILED = "evt.task_failed"
    EVT_ISSUE_LIST = "evt.issue_list"
    EVT_ISSUE_INFO = "evt.issue_info"
    EVT_ERROR = "evt.error"


@dataclass
class Message:
    type: MessageType
    payload: dict
    task_id: str | None = None


class MessageBus:
    def __init__(self):
        self._handlers: dict[MessageType, list[Callable]] = {}

    def subscribe(self, msg_type: MessageType, handler: Callable):
        self._handlers.setdefault(msg_type, []).append(handler)

    async def publish(self, message: Message):
        for handler in self._handlers.get(message.type, []):
            try:
                await handler(message)
            except Exception:
                logger.exception("Handler failed for %s", message.type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_bus.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/bus.py tests/core/test_bus.py
git commit -m "feat: add MessageBus with error-isolated publish"
```

---

### Task 5: IssueStore

**Files:**
- Create: `src/shadowcoder/core/issue_store.py`
- Create: `tests/core/test_issue_store.py`

- [ ] **Step 1: Write tests for IssueStore**

```python
# tests/core/test_issue_store.py
import pytest
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import (
    IssueStatus, InvalidTransitionError, Severity,
    ReviewComment, ReviewResult,
)
from shadowcoder.core.config import Config


@pytest.fixture
def store(tmp_repo, tmp_config):
    config = Config(str(tmp_config))
    return IssueStore(str(tmp_repo), config)


def test_create_issue(store):
    issue = store.create("Login feature", priority="high", tags=["auth"])
    assert issue.id == 1
    assert issue.title == "Login feature"
    assert issue.status == IssueStatus.CREATED
    assert issue.priority == "high"
    assert issue.tags == ["auth"]


def test_create_auto_increment(store):
    i1 = store.create("First")
    i2 = store.create("Second")
    assert i1.id == 1
    assert i2.id == 2


def test_get_issue(store):
    created = store.create("Test issue")
    loaded = store.get(created.id)
    assert loaded.id == created.id
    assert loaded.title == "Test issue"
    assert loaded.status == IssueStatus.CREATED


def test_get_nonexistent(store):
    with pytest.raises(FileNotFoundError):
        store.get(999)


def test_list_all(store):
    store.create("A")
    store.create("B")
    issues = store.list_all()
    assert len(issues) == 2
    assert issues[0].id == 1
    assert issues[1].id == 2


def test_list_all_empty(store):
    assert store.list_all() == []


def test_list_by_status(store):
    store.create("A")
    store.create("B")
    store.transition_status(1, IssueStatus.DESIGNING)
    result = store.list_by_status(IssueStatus.DESIGNING)
    assert len(result) == 1
    assert result[0].id == 1


def test_list_by_tag(store):
    store.create("A", tags=["backend"])
    store.create("B", tags=["frontend"])
    store.create("C", tags=["backend", "api"])
    result = store.list_by_tag("backend")
    assert len(result) == 2


def test_transition_status_valid(store):
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    issue = store.get(1)
    assert issue.status == IssueStatus.DESIGNING


def test_transition_status_invalid(store):
    store.create("Test")
    with pytest.raises(InvalidTransitionError):
        store.transition_status(1, IssueStatus.DONE)


def test_update_section(store):
    store.create("Test")
    store.update_section(1, "设计", "Design content here")
    issue = store.get(1)
    assert issue.sections["设计"] == "Design content here"


def test_update_section_overwrites(store):
    store.create("Test")
    store.update_section(1, "设计", "v1")
    store.update_section(1, "设计", "v2")
    issue = store.get(1)
    assert issue.sections["设计"] == "v2"


def test_append_review(store):
    store.create("Test")
    review = ReviewResult(
        passed=False,
        comments=[
            ReviewComment(severity=Severity.HIGH, message="Fix this"),
            ReviewComment(severity=Severity.LOW, message="Nit"),
        ],
        reviewer="claude-code",
    )
    store.append_review(1, "Design Review", review)
    issue = store.get(1)
    content = issue.sections["Design Review"]
    assert "claude-code" in content
    assert "Fix this" in content
    assert "HIGH" in content.upper() or "high" in content


def test_assign(store):
    store.create("Test")
    store.assign(1, "codex")
    issue = store.get(1)
    assert issue.assignee == "codex"


def test_sections_roundtrip(store):
    store.create("Test")
    store.update_section(1, "需求分析", "Analysis content")
    store.update_section(1, "设计", "Design content")
    issue = store.get(1)
    assert issue.sections["需求分析"] == "Analysis content"
    assert issue.sections["设计"] == "Design content"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_issue_store.py -v`
Expected: FAIL

- [ ] **Step 3: Implement IssueStore**

```python
# src/shadowcoder/core/issue_store.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import frontmatter

from shadowcoder.core.config import Config
from shadowcoder.core.models import (
    InvalidTransitionError,
    Issue,
    IssueStatus,
    ReviewResult,
    Severity,
    VALID_TRANSITIONS,
)


class IssueStore:
    def __init__(self, repo_path: str, config: Config):
        self.base = Path(repo_path) / config.get_issue_dir()

    def _next_id(self) -> int:
        existing = list(self.base.glob("*.md"))
        if not existing:
            return 1
        return max(int(f.stem) for f in existing) + 1

    def create(self, title: str, priority: str = "medium", tags: list[str] | None = None) -> Issue:
        issue = Issue(
            id=self._next_id(),
            title=title,
            status=IssueStatus.CREATED,
            priority=priority,
            created=datetime.now(),
            updated=datetime.now(),
            tags=tags or [],
        )
        self._save(issue)
        return issue

    def get(self, issue_id: int) -> Issue:
        path = self.base / f"{issue_id:04d}.md"
        if not path.exists():
            raise FileNotFoundError(f"Issue {issue_id} not found: {path}")
        post = frontmatter.load(str(path))
        return Issue(
            id=post["id"],
            title=post["title"],
            status=IssueStatus(post["status"]),
            priority=post["priority"],
            created=datetime.fromisoformat(post["created"]),
            updated=datetime.fromisoformat(post["updated"]),
            tags=post.get("tags", []),
            assignee=post.get("assignee"),
            sections=self._markdown_to_sections(post.content),
        )

    def list_all(self) -> list[Issue]:
        if not self.base.exists():
            return []
        return [self.get(int(f.stem)) for f in sorted(self.base.glob("*.md"))]

    def list_by_status(self, status: IssueStatus) -> list[Issue]:
        return [i for i in self.list_all() if i.status == status]

    def list_by_tag(self, tag: str) -> list[Issue]:
        return [i for i in self.list_all() if tag in i.tags]

    def transition_status(self, issue_id: int, new_status: IssueStatus) -> None:
        issue = self.get(issue_id)
        if new_status not in VALID_TRANSITIONS[issue.status]:
            raise InvalidTransitionError(issue.status, new_status)
        issue.status = new_status
        self._save(issue)

    def update_section(self, issue_id: int, section: str, content: str) -> None:
        issue = self.get(issue_id)
        issue.sections[section] = content
        self._save(issue)

    def append_review(self, issue_id: int, section: str, review: ReviewResult) -> None:
        formatted = self._format_review(review)
        issue = self.get(issue_id)
        existing = issue.sections.get(section, "")
        if existing:
            issue.sections[section] = existing + "\n\n" + formatted
        else:
            issue.sections[section] = formatted
        self._save(issue)

    def assign(self, issue_id: int, agent_name: str) -> None:
        issue = self.get(issue_id)
        issue.assignee = agent_name
        self._save(issue)

    def _save(self, issue: Issue) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(
            content=self._sections_to_markdown(issue.sections),
            id=issue.id,
            title=issue.title,
            status=issue.status.value,
            priority=issue.priority,
            created=issue.created.isoformat(),
            updated=datetime.now().isoformat(),
            tags=issue.tags,
            assignee=issue.assignee,
        )
        path = self.base / f"{issue.id:04d}.md"
        path.write_text(frontmatter.dumps(post), encoding="utf-8")

    @staticmethod
    def _format_review(review: ReviewResult) -> str:
        lines = [f"**Reviewer: {review.reviewer}** — {'PASSED' if review.passed else 'NOT PASSED'}"]
        for c in review.comments:
            loc = f" ({c.location})" if c.location else ""
            lines.append(f"- [{c.severity.value.upper()}]{loc} {c.message}")
        return "\n".join(lines)

    @staticmethod
    def _sections_to_markdown(sections: dict[str, str]) -> str:
        if not sections:
            return ""
        return "\n\n".join(f"## {k}\n{v}" for k, v in sections.items())

    @staticmethod
    def _markdown_to_sections(content: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current_key: str | None = None
        lines: list[str] = []
        for line in content.split("\n"):
            if line.startswith("## "):
                if current_key:
                    sections[current_key] = "\n".join(lines).strip()
                current_key = line[3:].strip()
                lines = []
            else:
                lines.append(line)
        if current_key:
            sections[current_key] = "\n".join(lines).strip()
        return sections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_issue_store.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/issue_store.py tests/core/test_issue_store.py
git commit -m "feat: add IssueStore with CRUD, transitions, and review formatting"
```

---

### Task 6: WorktreeManager

**Files:**
- Create: `src/shadowcoder/core/worktree.py`
- Create: `tests/core/test_worktree.py`

- [ ] **Step 1: Write tests for WorktreeManager**

```python
# tests/core/test_worktree.py
import pytest
from pathlib import Path
from shadowcoder.core.worktree import WorktreeManager


@pytest.fixture
def wt_manager():
    return WorktreeManager()


async def test_create_worktree(tmp_repo, wt_manager):
    wt_path = await wt_manager.create(str(tmp_repo), 1)
    assert Path(wt_path).exists()
    assert "issue-1" in wt_path


async def test_create_worktree_branch(tmp_repo, wt_manager):
    """Worktree should create a branch named shadowcoder/issue-N."""
    import subprocess
    await wt_manager.create(str(tmp_repo), 1)
    result = subprocess.run(
        ["git", "branch", "--list", "shadowcoder/issue-1"],
        cwd=str(tmp_repo), capture_output=True, text=True,
    )
    assert "shadowcoder/issue-1" in result.stdout


async def test_remove_worktree(tmp_repo, wt_manager):
    wt_path = await wt_manager.create(str(tmp_repo), 1)
    assert Path(wt_path).exists()
    await wt_manager.remove(str(tmp_repo), 1)
    assert not Path(wt_path).exists()


async def test_list_worktrees(tmp_repo, wt_manager):
    await wt_manager.create(str(tmp_repo), 1)
    await wt_manager.create(str(tmp_repo), 2)
    wts = await wt_manager.list(str(tmp_repo))
    # Should include main worktree + 2 created
    assert len(wts) >= 2


async def test_create_duplicate_fails(tmp_repo, wt_manager):
    await wt_manager.create(str(tmp_repo), 1)
    with pytest.raises(RuntimeError):
        await wt_manager.create(str(tmp_repo), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_worktree.py -v`
Expected: FAIL

- [ ] **Step 3: Implement WorktreeManager**

```python
# src/shadowcoder/core/worktree.py
from __future__ import annotations

import asyncio
from pathlib import Path


class WorktreeManager:
    def __init__(self, base_dir: str = ".shadowcoder/worktrees"):
        self.base_dir = base_dir

    async def _run_git(self, repo_path: str, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {stderr.decode().strip()}")
        return stdout.decode()

    async def create(self, repo_path: str, issue_id: int) -> str:
        branch = f"shadowcoder/issue-{issue_id}"
        wt_path = str(Path(repo_path) / self.base_dir / f"issue-{issue_id}")
        await self._run_git(repo_path, "worktree", "add", "-b", branch, wt_path)
        return wt_path

    async def remove(self, repo_path: str, issue_id: int) -> None:
        wt_path = str(Path(repo_path) / self.base_dir / f"issue-{issue_id}")
        await self._run_git(repo_path, "worktree", "remove", wt_path)

    async def list(self, repo_path: str) -> list[str]:
        output = await self._run_git(repo_path, "worktree", "list", "--porcelain")
        return [
            line.split(maxsplit=1)[1]
            for line in output.splitlines()
            if line.startswith("worktree ")
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_worktree.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/worktree.py tests/core/test_worktree.py
git commit -m "feat: add async WorktreeManager for git worktree operations"
```

---

### Task 7: TaskManager

**Files:**
- Create: `src/shadowcoder/core/task_manager.py`
- Create: `tests/core/test_task_manager.py`

- [ ] **Step 1: Write tests for TaskManager**

```python
# tests/core/test_task_manager.py
import asyncio
import pytest
from unittest.mock import AsyncMock
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import Issue, IssueStatus, TaskStatus
from datetime import datetime


@pytest.fixture
def mock_worktree():
    wt = AsyncMock()
    wt.create = AsyncMock(return_value="/tmp/worktree/issue-1")
    return wt


@pytest.fixture
def manager(mock_worktree):
    return TaskManager(mock_worktree)


@pytest.fixture
def sample_issue():
    return Issue(
        id=1, title="Test", status=IssueStatus.CREATED,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )


async def test_create_task(manager, sample_issue):
    task = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    assert task.issue_id == 1
    assert task.action == "design"
    assert task.agent_name == "claude-code"
    assert task.status == TaskStatus.RUNNING
    assert task.worktree_path == "/tmp/worktree/issue-1"


async def test_create_calls_worktree(manager, sample_issue, mock_worktree):
    await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    mock_worktree.create.assert_called_once_with("/tmp/repo", 1)


async def test_list_active(manager, sample_issue):
    t1 = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    active = manager.list_active()
    assert len(active) == 1
    assert active[0].task_id == t1.task_id


async def test_list_active_excludes_completed(manager, sample_issue):
    t1 = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    t1.status = TaskStatus.COMPLETED
    assert manager.list_active() == []


async def test_cancel(manager, sample_issue):
    task = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")

    async def long_running():
        await asyncio.sleep(100)

    manager.launch(task.task_id, long_running())
    await manager.cancel(task.task_id)
    assert task.status == TaskStatus.CANCELLED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_task_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Implement TaskManager**

```python
# src/shadowcoder/core/task_manager.py
from __future__ import annotations

import asyncio
import uuid

from shadowcoder.core.models import Issue, Task, TaskStatus
from shadowcoder.core.worktree import WorktreeManager


class TaskManager:
    def __init__(self, worktree_manager: WorktreeManager):
        self.tasks: dict[str, Task] = {}
        self.worktree_manager = worktree_manager
        self._running: dict[str, asyncio.Task] = {}

    async def create(self, issue: Issue, repo_path: str, action: str, agent_name: str) -> Task:
        task_id = str(uuid.uuid4())[:8]
        worktree_path = await self.worktree_manager.create(repo_path, issue.id)
        task = Task(
            task_id=task_id,
            issue_id=issue.id,
            repo_path=repo_path,
            action=action,
            agent_name=agent_name,
            worktree_path=worktree_path,
        )
        self.tasks[task_id] = task
        return task

    def launch(self, task_id: str, coro) -> asyncio.Task:
        atask = asyncio.create_task(coro)
        self._running[task_id] = atask
        return atask

    def list_active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]

    async def cancel(self, task_id: str) -> None:
        if task_id in self._running:
            self._running[task_id].cancel()
        if task_id in self.tasks:
            self.tasks[task_id].status = TaskStatus.CANCELLED
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_task_manager.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/task_manager.py tests/core/test_task_manager.py
git commit -m "feat: add TaskManager with async create and cancel"
```

---

### Task 8: Agent Base + Registry

**Files:**
- Create: `src/shadowcoder/agents/base.py`
- Create: `src/shadowcoder/agents/registry.py`
- Create: `tests/agents/test_registry.py`

- [ ] **Step 1: Write tests for AgentRegistry**

```python
# tests/agents/test_registry.py
import pytest
from shadowcoder.agents.base import BaseAgent, AgentRequest, AgentResponse, AgentStream
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.config import Config
from shadowcoder.core.models import ReviewResult


class FakeAgent(BaseAgent):
    async def execute(self, request):
        return AgentResponse(content="ok", success=True)

    async def stream(self, request):
        raise NotImplementedError

    async def review(self, request):
        return ReviewResult(passed=True, comments=[], reviewer="fake")


def test_register_and_get(tmp_config):
    AgentRegistry.register("claude_code", FakeAgent)
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    agent = registry.get("claude-code")
    assert isinstance(agent, FakeAgent)


def test_get_default(tmp_config):
    AgentRegistry.register("claude_code", FakeAgent)
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    agent = registry.get("default")
    assert isinstance(agent, FakeAgent)


def test_get_caches(tmp_config):
    AgentRegistry.register("claude_code", FakeAgent)
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    a1 = registry.get("claude-code")
    a2 = registry.get("claude-code")
    assert a1 is a2


def test_get_unknown_agent(tmp_config):
    config = Config(str(tmp_config))
    registry = AgentRegistry(config)
    with pytest.raises(KeyError):
        registry.get("nonexistent")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_registry.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Agent base and registry**

```python
# src/shadowcoder/agents/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from shadowcoder.core.models import Issue, ReviewResult


@dataclass
class AgentRequest:
    action: str  # analyze / design / develop / test / review
    issue: Issue
    context: dict  # worktree_path, related files, etc.
    prompt_override: str | None = None


@dataclass
class AgentResponse:
    content: str
    success: bool
    metadata: dict | None = None


class AgentStream:
    """Async iterator for streaming agent output."""

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def execute(self, request: AgentRequest) -> AgentResponse:
        ...

    @abstractmethod
    async def stream(self, request: AgentRequest) -> AgentStream:
        ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewResult:
        ...
```

```python
# src/shadowcoder/agents/registry.py
from __future__ import annotations

from shadowcoder.agents.base import BaseAgent
from shadowcoder.core.config import Config


class AgentRegistry:
    _agent_classes: dict[str, type[BaseAgent]] = {}

    def __init__(self, config: Config):
        self.config = config
        self._instances: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, type_name: str, agent_class: type[BaseAgent]) -> None:
        cls._agent_classes[type_name] = agent_class

    def get(self, name: str) -> BaseAgent:
        if name == "default":
            name = self.config.get_default_agent()
        if name not in self._instances:
            agent_conf = self.config.get_agent_config(name)
            agent_type = agent_conf["type"]
            if agent_type not in self._agent_classes:
                raise KeyError(f"Unknown agent type: {agent_type}")
            cls = self._agent_classes[agent_type]
            self._instances[name] = cls(agent_conf)
        return self._instances[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_registry.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/agents/base.py src/shadowcoder/agents/registry.py tests/agents/test_registry.py
git commit -m "feat: add BaseAgent abstract class and AgentRegistry"
```

---

### Task 9: Engine

**Files:**
- Create: `src/shadowcoder/core/engine.py`
- Create: `tests/core/test_engine.py`

This is the most complex component. Tests use mocked agents and a real IssueStore.

- [ ] **Step 1: Write tests for Engine**

```python
# tests/core/test_engine.py
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
    """When review fails max_review_rounds times, issue goes to BLOCKED."""
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
    # Should have been called max_review_rounds times for execute
    assert agent.execute.call_count == config.get_max_review_rounds()


async def test_design_agent_failure(bus, store, task_mgr, config):
    """When agent returns success=false, issue goes to FAILED."""
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
    """When agent raises, issue goes to FAILED."""
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
    # Move to APPROVED so develop can run
    store.transition_status(issue.id, IssueStatus.DESIGNING)
    store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
    store.transition_status(issue.id, IssueStatus.APPROVED)

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.TESTING


async def test_test_happy_path(bus, store, task_mgr, registry_with, config):
    """Issue already at TESTING (normal flow after develop), _on_test skips transition."""
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
    """Retry test from FAILED state — transitions to TESTING then DONE."""
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
    """Approve a blocked issue should move it to next status."""
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

    # Run design to get BLOCKED
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.BLOCKED

    # Approve
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
    """Resume a BLOCKED design issue should re-run design and succeed if agent/review pass."""
    call_count = 0
    agent = AsyncMock()

    async def execute_side_effect(request):
        return AgentResponse(content="output", success=True)

    async def review_side_effect(request):
        nonlocal call_count
        call_count += 1
        # First 3 calls (max_review_rounds) fail, then pass on resume
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

    # Design until BLOCKED
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.BLOCKED

    # Resume — now review passes
    await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED


async def test_all_reviewers_unavailable(bus, store, task_mgr, config):
    """When all reviewers crash, issue goes to FAILED."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_engine.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Engine**

```python
# src/shadowcoder/core/engine.py
from __future__ import annotations

import logging

from shadowcoder.agents.base import AgentRequest
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import Issue, IssueStatus, TaskStatus
from shadowcoder.core.task_manager import TaskManager

logger = logging.getLogger(__name__)


class Engine:
    def __init__(
        self,
        bus: MessageBus,
        issue_store: IssueStore,
        task_manager: TaskManager,
        agent_registry: AgentRegistry,
        config: Config,
        repo_path: str,
    ):
        self.bus = bus
        self.issue_store = issue_store
        self.task_manager = task_manager
        self.agents = agent_registry
        self.config = config
        self.repo_path = repo_path
        self._bind_commands()

    def _bind_commands(self):
        self.bus.subscribe(MessageType.CMD_CREATE_ISSUE, self._on_create)
        self.bus.subscribe(MessageType.CMD_DESIGN, self._on_design)
        self.bus.subscribe(MessageType.CMD_DEVELOP, self._on_develop)
        self.bus.subscribe(MessageType.CMD_TEST, self._on_test)
        self.bus.subscribe(MessageType.CMD_RESUME, self._on_resume)
        self.bus.subscribe(MessageType.CMD_APPROVE, self._on_approve)
        self.bus.subscribe(MessageType.CMD_CANCEL, self._on_cancel)
        self.bus.subscribe(MessageType.CMD_LIST, self._on_list)
        self.bus.subscribe(MessageType.CMD_INFO, self._on_info)

    async def _on_create(self, msg: Message):
        title = msg.payload["title"]
        priority = msg.payload.get("priority", "medium")
        tags = msg.payload.get("tags")
        issue = self.issue_store.create(title, priority=priority, tags=tags)
        await self.bus.publish(Message(
            MessageType.EVT_ISSUE_CREATED,
            {"issue_id": issue.id, "title": issue.title},
        ))

    async def _review_with_retry(self, reviewer, request, max_retries=3):
        for attempt in range(1, max_retries + 1):
            try:
                return await reviewer.review(request)
            except Exception:
                if attempt == max_retries:
                    raise
                await self.bus.publish(Message(MessageType.EVT_ERROR, {
                    "message": f"reviewer failed, retry {attempt}/{max_retries}",
                }))

    async def _run_all_reviewers(self, issue, task, action, review_section_key):
        reviewer_names = self.config.get_reviewers(action)
        all_passed = True
        failed_reviewers = []

        for rname in reviewer_names:
            reviewer = self.agents.get(rname)
            request = AgentRequest(
                action="review", issue=issue,
                context={"worktree_path": task.worktree_path},
            )
            try:
                review = await self._review_with_retry(reviewer, request)
                self.issue_store.append_review(issue.id, review_section_key, review)
                await self.bus.publish(Message(MessageType.EVT_REVIEW_RESULT, {
                    "issue_id": issue.id, "reviewer": rname,
                    "passed": review.passed, "comments": len(review.comments),
                }))
                if not review.passed:
                    all_passed = False
            except Exception:
                failed_reviewers.append(rname)
                logger.warning("Reviewer %s unavailable after retries", rname)

        if len(failed_reviewers) == len(reviewer_names):
            raise RuntimeError(f"All reviewers unavailable: {failed_reviewers}")

        return all_passed

    async def _run_with_review(
        self, issue, task, action, review_stage,
        success_status, section_key, review_section_key,
    ):
        max_rounds = self.config.get_max_review_rounds()
        try:
            for round_num in range(1, max_rounds + 1):
                # --- Execute phase ---
                self.issue_store.transition_status(
                    issue.id, IssueStatus[action.upper() + "ING"])
                issue = self.issue_store.get(issue.id)
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED, {
                    "issue_id": issue.id, "status": issue.status.value,
                    "round": round_num,
                }))

                agent = self.agents.get(issue.assignee or "default")
                request = AgentRequest(
                    action=action, issue=issue,
                    context={"worktree_path": task.worktree_path},
                )
                response = await agent.execute(request)

                if not response.success:
                    self.issue_store.update_section(issue.id, section_key, response.content)
                    self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                        "issue_id": issue.id, "task_id": task.task_id,
                        "reason": "agent reported failure",
                    }))
                    return

                self.issue_store.update_section(issue.id, section_key, response.content)

                # --- Review phase ---
                self.issue_store.transition_status(
                    issue.id, IssueStatus[review_stage.upper()])
                issue = self.issue_store.get(issue.id)

                all_passed = await self._run_all_reviewers(
                    issue, task, action, review_section_key)

                if all_passed:
                    self.issue_store.transition_status(issue.id, success_status)
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED, {
                        "issue_id": issue.id, "task_id": task.task_id,
                    }))
                    return

                # Reload issue with review comments for next round
                issue = self.issue_store.get(issue.id)

            # --- Rounds exhausted → BLOCKED ---
            self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                "issue_id": issue.id, "task_id": task.task_id,
                "reason": f"review not passed after {max_rounds} rounds",
            }))

        except Exception as e:
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                "issue_id": issue.id, "task_id": task.task_id,
                "reason": str(e),
            }))

    async def _on_design(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(
            issue, repo_path=self.repo_path, action="design",
            agent_name=issue.assignee or "default")
        await self._run_with_review(
            issue, task, action="design", review_stage="design_review",
            success_status=IssueStatus.APPROVED,
            section_key="设计", review_section_key="Design Review")

    async def _on_develop(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(
            issue, repo_path=self.repo_path, action="develop",
            agent_name=issue.assignee or "default")
        await self._run_with_review(
            issue, task, action="develop", review_stage="dev_review",
            success_status=IssueStatus.TESTING,
            section_key="开发步骤", review_section_key="Dev Review")

    async def _on_test(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(
            issue, repo_path=self.repo_path, action="test",
            agent_name=issue.assignee or "default")
        try:
            if issue.status != IssueStatus.TESTING:
                self.issue_store.transition_status(issue.id, IssueStatus.TESTING)
            issue = self.issue_store.get(issue.id)

            agent = self.agents.get(issue.assignee or "default")
            response = await agent.execute(AgentRequest(
                action="test", issue=issue,
                context={"worktree_path": task.worktree_path}))
            self.issue_store.update_section(issue.id, "测试", response.content)

            if response.success:
                self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                task.status = TaskStatus.COMPLETED
                await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED, {
                    "issue_id": issue.id, "task_id": task.task_id,
                }))
            else:
                self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                task.status = TaskStatus.FAILED
                await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                    "issue_id": issue.id, "task_id": task.task_id,
                    "reason": "tests failed",
                }))

        except Exception as e:
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                "issue_id": issue.id, "task_id": task.task_id,
                "reason": str(e),
            }))

    def _infer_blocked_stage(self, issue: Issue) -> str | None:
        if "Dev Review" in issue.sections:
            return "develop"
        if "Design Review" in issue.sections:
            return "design"
        return None

    async def _on_resume(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR, {
                "message": f"issue #{issue.id} is not BLOCKED",
            }))
            return
        action = self._infer_blocked_stage(issue)
        if action == "design":
            await self._on_design(msg)
        elif action == "develop":
            await self._on_develop(msg)
        else:
            await self.bus.publish(Message(MessageType.EVT_ERROR, {
                "message": f"cannot infer blocked stage for issue #{issue.id}",
            }))

    async def _on_approve(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR, {
                "message": f"issue #{issue.id} is not BLOCKED",
            }))
            return
        stage = self._infer_blocked_stage(issue)
        next_status = IssueStatus.TESTING if stage == "develop" else IssueStatus.APPROVED
        self.issue_store.transition_status(issue.id, next_status)
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED, {
            "issue_id": issue.id, "status": next_status.value,
        }))

    async def _on_cancel(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        self.issue_store.transition_status(issue.id, IssueStatus.CANCELLED)
        for task in self.task_manager.list_active():
            if task.issue_id == issue.id:
                await self.task_manager.cancel(task.task_id)
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED, {
            "issue_id": issue.id, "status": "cancelled",
        }))

    async def _on_list(self, msg: Message):
        issues = self.issue_store.list_all()
        await self.bus.publish(Message(MessageType.EVT_ISSUE_LIST, {
            "issues": [
                {"id": i.id, "title": i.title, "status": i.status.value, "priority": i.priority}
                for i in issues
            ],
        }))

    async def _on_info(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        await self.bus.publish(Message(MessageType.EVT_ISSUE_INFO, {
            "issue": {
                "id": issue.id, "title": issue.title,
                "status": issue.status.value, "priority": issue.priority,
                "tags": issue.tags, "assignee": issue.assignee,
                "sections": list(issue.sections.keys()),
            },
        }))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_engine.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "feat: add Engine with review loops, error handling, and BLOCKED recovery"
```

---

### Task 10: Claude Code Agent Stub

**Files:**
- Create: `src/shadowcoder/agents/claude_code.py`
- Create: `tests/agents/test_claude_code.py`

A minimal stub that returns placeholder responses. Real implementation will call `claude` CLI.

- [ ] **Step 1: Write tests for ClaudeCodeAgent**

```python
# tests/agents/test_claude_code.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_claude_code.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ClaudeCodeAgent stub**

```python
# src/shadowcoder/agents/claude_code.py
from __future__ import annotations

from shadowcoder.agents.base import AgentRequest, AgentResponse, AgentStream, BaseAgent
from shadowcoder.core.models import ReviewResult


class ClaudeCodeAgent(BaseAgent):
    """Stub implementation. Will call `claude` CLI in the future."""

    async def execute(self, request: AgentRequest) -> AgentResponse:
        # TODO: call `claude -p` subprocess with proper prompt
        return AgentResponse(
            content=f"[stub] {request.action} output for: {request.issue.title}",
            success=True,
            metadata={"agent": "claude-code", "stub": True},
        )

    async def stream(self, request: AgentRequest) -> AgentStream:
        # TODO: implement streaming via claude CLI
        raise NotImplementedError("Streaming not yet implemented")

    async def review(self, request: AgentRequest) -> ReviewResult:
        # TODO: call claude with review prompt, parse structured output
        return ReviewResult(
            passed=True,
            comments=[],
            reviewer="claude-code",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_claude_code.py -v`
Expected: All PASS

- [ ] **Step 5: Register agent and commit**

Add to `src/shadowcoder/agents/__init__.py`:
```python
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.registry import AgentRegistry

AgentRegistry.register("claude_code", ClaudeCodeAgent)
```

```bash
git add src/shadowcoder/agents/ tests/agents/test_claude_code.py
git commit -m "feat: add ClaudeCodeAgent stub with auto-registration"
```

---

### Task 11: TUI

**Files:**
- Create: `src/shadowcoder/cli/tui/app.py`

No automated tests for TUI (Textual has its own testing framework, but interactive TUI testing adds complexity without proportional value at this stage). Manual verification.

- [ ] **Step 1: Implement TUI app**

```python
# src/shadowcoder/cli/tui/app.py
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Input

from shadowcoder.core.bus import Message, MessageBus, MessageType


class ShadowCoderApp(App):
    CSS_PATH = None
    TITLE = "ShadowCoder"

    def __init__(self, bus: MessageBus, **kwargs):
        super().__init__(**kwargs)
        self.bus = bus

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="output", wrap=True, highlight=True)
        yield Input(
            placeholder="命令: create <title> | list | info #id | design #id | develop #id | test #id | resume #id | approve #id | cancel #id"
        )
        yield Footer()

    async def on_mount(self):
        self.bus.subscribe(MessageType.EVT_ISSUE_CREATED, self._on_issue_created)
        self.bus.subscribe(MessageType.EVT_AGENT_OUTPUT, self._on_agent_output)
        self.bus.subscribe(MessageType.EVT_STATUS_CHANGED, self._on_status_changed)
        self.bus.subscribe(MessageType.EVT_REVIEW_RESULT, self._on_review_result)
        self.bus.subscribe(MessageType.EVT_TASK_COMPLETED, self._on_task_completed)
        self.bus.subscribe(MessageType.EVT_TASK_FAILED, self._on_task_failed)
        self.bus.subscribe(MessageType.EVT_ERROR, self._on_error)

    async def on_input_submitted(self, event: Input.Submitted):
        cmd = event.value.strip()
        event.input.clear()
        if not cmd:
            return
        log = self.query_one("#output", RichLog)
        log.write(f"[bold]> {cmd}[/bold]")
        msg = self._parse_command(cmd)
        if msg:
            await self.bus.publish(msg)

    def _parse_command(self, cmd: str) -> Message | None:
        log = self.query_one("#output", RichLog)
        parts = cmd.split()
        match parts:
            case ["create", *title_parts] if title_parts:
                return Message(MessageType.CMD_CREATE_ISSUE, {"title": " ".join(title_parts)})
            case ["list"]:
                return Message(MessageType.CMD_LIST, {})
            case ["info", ref]:
                return Message(MessageType.CMD_INFO, {"issue_id": int(ref.lstrip("#"))})
            case ["design", ref]:
                return Message(MessageType.CMD_DESIGN, {"issue_id": int(ref.lstrip("#"))})
            case ["develop", ref]:
                return Message(MessageType.CMD_DEVELOP, {"issue_id": int(ref.lstrip("#"))})
            case ["test", ref]:
                return Message(MessageType.CMD_TEST, {"issue_id": int(ref.lstrip("#"))})
            case ["resume", ref]:
                return Message(MessageType.CMD_RESUME, {"issue_id": int(ref.lstrip("#"))})
            case ["approve", ref]:
                return Message(MessageType.CMD_APPROVE, {"issue_id": int(ref.lstrip("#"))})
            case ["cancel", ref]:
                return Message(MessageType.CMD_CANCEL, {"issue_id": int(ref.lstrip("#"))})
            case _:
                log.write(f"[red]未知命令: {cmd}[/red]")
                return None

    async def _on_issue_created(self, msg: Message):
        log = self.query_one("#output", RichLog)
        log.write(f"[green]Issue #{msg.payload['issue_id']} created: {msg.payload['title']}[/green]")

    async def _on_agent_output(self, msg: Message):
        self.query_one("#output", RichLog).write(msg.payload["chunk"])

    async def _on_status_changed(self, msg: Message):
        log = self.query_one("#output", RichLog)
        extra = f" (round {msg.payload['round']})" if "round" in msg.payload else ""
        log.write(f"[blue]Issue #{msg.payload['issue_id']} → {msg.payload['status']}{extra}[/blue]")

    async def _on_review_result(self, msg: Message):
        log = self.query_one("#output", RichLog)
        passed = "[green]PASSED[/green]" if msg.payload["passed"] else "[red]NOT PASSED[/red]"
        log.write(f"Review by {msg.payload['reviewer']}: {passed} ({msg.payload['comments']} comments)")

    async def _on_task_completed(self, msg: Message):
        log = self.query_one("#output", RichLog)
        log.write(f"[green]Task {msg.payload['task_id']} completed for issue #{msg.payload['issue_id']}[/green]")

    async def _on_task_failed(self, msg: Message):
        log = self.query_one("#output", RichLog)
        reason = msg.payload.get("reason", "unknown")
        log.write(f"[red]Task failed for issue #{msg.payload['issue_id']}: {reason}[/red]")

    async def _on_error(self, msg: Message):
        log = self.query_one("#output", RichLog)
        log.write(f"[red]Error: {msg.payload['message']}[/red]")


def main():
    import shadowcoder.agents  # trigger agent registration

    from shadowcoder.core.config import Config
    from shadowcoder.core.engine import Engine
    from shadowcoder.core.issue_store import IssueStore
    from shadowcoder.core.task_manager import TaskManager
    from shadowcoder.core.worktree import WorktreeManager
    from shadowcoder.agents.registry import AgentRegistry

    import os

    config = Config()
    repo_path = os.getcwd()

    bus = MessageBus()
    wt_manager = WorktreeManager(config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    issue_store = IssueStore(repo_path, config)
    agent_registry = AgentRegistry(config)
    engine = Engine(bus, issue_store, task_manager, agent_registry, config, repo_path)

    app = ShadowCoderApp(bus)
    app.run()
```

- [ ] **Step 2: Manual verification**

Run: `cd /tmp && mkdir test-repo && cd test-repo && git init && shadowcoder`
Expected: TUI launches with header, input, footer. Typing `create Test issue` should show "Issue #1 created".

- [ ] **Step 3: Commit**

```bash
git add src/shadowcoder/cli/tui/app.py src/shadowcoder/__main__.py
git commit -m "feat: add TUI with command parsing and event rendering"
```

---

### Task 12: Integration Test & .gitignore Setup

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
import pytest
from unittest.mock import AsyncMock
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus, ReviewResult
from shadowcoder.agents.base import AgentResponse
from shadowcoder.agents.registry import AgentRegistry


async def test_full_lifecycle(tmp_repo, tmp_config):
    """Test create → design → develop → test → done."""
    config = Config(str(tmp_config))

    agent = AsyncMock()
    agent.execute = AsyncMock(return_value=AgentResponse(content="output", success=True))
    agent.review = AsyncMock(return_value=ReviewResult(passed=True, comments=[], reviewer="mock"))

    AgentRegistry.register("claude_code", lambda cfg: agent)
    # Patch get to return our mock
    registry = AgentRegistry(config)
    registry._instances["claude-code"] = agent

    bus = MessageBus()
    mock_wt = AsyncMock()
    mock_wt.create = AsyncMock(return_value="/tmp/wt")
    task_mgr = TaskManager(mock_wt)
    store = IssueStore(str(tmp_repo), config)

    engine = Engine(bus, store, task_mgr, registry, config, str(tmp_repo))

    # Create
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Full test"}))
    assert store.get(1).status == IssueStatus.CREATED

    # Design
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED

    # Develop
    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.TESTING

    # Test
    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.DONE
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: add integration test for full issue lifecycle"
```

---

### Task 13: Final Cleanup

- [ ] **Step 1: Create .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/

# ShadowCoder runtime
.shadowcoder/worktrees/

# IDE
.idea/
.vscode/
*.swp

# Testing
.pytest_cache/
.coverage
htmlcov/
```

- [ ] **Step 2: Run full test suite one more time**

Run: `pytest -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: add .gitignore"
```
