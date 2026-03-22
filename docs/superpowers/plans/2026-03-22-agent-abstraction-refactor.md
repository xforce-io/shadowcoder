# Agent Abstraction Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic `execute()/review()` agent interface with per-action methods (`design()/develop()/review()/test()`) returning structured output types, moving all format parsing from Engine to agent implementations.

**Architecture:** New `agents/types.py` defines all structured output types. `BaseAgent` gets 4 abstract methods + helper methods. `ClaudeCodeAgent` adapts to new interface. Engine uses structured fields directly, zero parsing. `ReviewComment`/`Severity` migrate from `core/models.py` to `agents/types.py`.

**Tech Stack:** Python 3.12+, dataclasses, asyncio, pytest

**Spec:** `docs/superpowers/specs/2026-03-22-agent-abstraction-refactor-design.md`

---

## File Structure

```
src/shadowcoder/
├── agents/
│   ├── types.py           # NEW: all type definitions (Output types, AgentRequest,
│   │                      #   ReviewComment, Severity, AgentUsage, AgentActionFailed)
│   ├── base.py            # REWRITE: 4 abstract methods + helpers, remove old types
│   ├── claude_code.py     # REWRITE: implement design/develop/review/test
│   ├── registry.py        # unchanged
│   └── __init__.py        # unchanged
├── core/
│   ├── models.py          # MODIFY: remove ReviewResult, ReviewComment, Severity
│   ├── engine.py          # MODIFY: use structured types, add AgentActionFailed handling
│   ├── issue_store.py     # MODIFY: ReviewOutput replaces ReviewResult
│   └── ...                # unchanged
tests/
├── agents/
│   ├── test_types.py      # NEW: test new types
│   ├── test_registry.py   # MODIFY: update mocks
│   └── test_claude_code.py # MODIFY: update to new interface
├── core/
│   ├── test_models.py     # MODIFY: remove ReviewResult tests
│   ├── test_engine.py     # MODIFY: update mocks to new interface
│   ├── test_engine_test_retry.py # MODIFY: update mocks
│   ├── test_create_with_description.py # MODIFY: minor import fix
│   └── test_issue_store.py # MODIFY: ReviewOutput
├── test_e2e.py            # MODIFY: update mocks
├── test_e2e_real_task.py  # MODIFY: update agent types
├── test_e2e_sql_engine.py # MODIFY: update agent types
└── test_integration.py    # MODIFY: update mocks
```

---

### Task 1: Create agents/types.py

**Files:**
- Create: `src/shadowcoder/agents/types.py`
- Create: `tests/agents/test_types.py`

- [ ] **Step 1: Write tests for new types**

```python
# tests/agents/test_types.py
from shadowcoder.agents.types import (
    Severity, ReviewComment, AgentUsage,
    DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
    AgentRequest, AgentActionFailed,
)
from shadowcoder.core.models import Issue, IssueStatus
from datetime import datetime


def test_severity_values():
    assert Severity.CRITICAL.value == "critical"
    assert Severity.LOW.value == "low"


def test_review_comment():
    c = ReviewComment(severity=Severity.HIGH, message="bad", location="file.py:10")
    assert c.severity == Severity.HIGH
    assert c.location == "file.py:10"


def test_agent_usage_defaults():
    u = AgentUsage()
    assert u.input_tokens == 0
    assert u.cost_usd is None


def test_design_output():
    o = DesignOutput(document="design doc")
    assert o.document == "design doc"
    assert o.usage is None


def test_develop_output_defaults():
    o = DevelopOutput(summary="implemented X")
    assert o.files_changed == []
    assert o.usage is None


def test_develop_output_with_files():
    o = DevelopOutput(summary="s", files_changed=["a.py", "b.py"])
    assert len(o.files_changed) == 2


def test_review_output():
    o = ReviewOutput(passed=False, comments=[
        ReviewComment(severity=Severity.HIGH, message="fix this")
    ], reviewer="claude")
    assert not o.passed
    assert len(o.comments) == 1


def test_review_output_defaults():
    o = ReviewOutput(passed=True)
    assert o.comments == []
    assert o.reviewer == ""


def test_test_output():
    o = TestOutput(report="all pass", success=True, passed_count=10, total_count=10)
    assert o.success
    assert o.recommendation is None


def test_test_output_with_recommendation():
    o = TestOutput(report="fail", success=False, recommendation="develop",
                   passed_count=5, total_count=10)
    assert not o.success
    assert o.recommendation == "develop"


def test_agent_request():
    issue = Issue(id=1, title="t", status=IssueStatus.CREATED, priority="medium",
                  created=datetime.now(), updated=datetime.now())
    r = AgentRequest(action="design", issue=issue, context={"worktree_path": "/tmp"})
    assert r.action == "design"
    assert r.prompt_override is None


def test_agent_action_failed():
    e = AgentActionFailed("could not complete", partial_output="partial result")
    assert str(e) == "could not complete"
    assert e.partial_output == "partial result"


def test_agent_action_failed_no_partial():
    e = AgentActionFailed("failed")
    assert e.partial_output == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_types.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement types.py**

```python
# src/shadowcoder/agents/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from shadowcoder.core.models import Issue


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ReviewComment:
    severity: Severity
    message: str
    location: str | None = None


@dataclass
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None


@dataclass
class AgentRequest:
    action: str
    issue: Issue
    context: dict
    prompt_override: str | None = None


@dataclass
class DesignOutput:
    document: str
    usage: AgentUsage | None = None


@dataclass
class DevelopOutput:
    summary: str
    files_changed: list[str] = field(default_factory=list)
    usage: AgentUsage | None = None


@dataclass
class ReviewOutput:
    passed: bool
    comments: list[ReviewComment] = field(default_factory=list)
    reviewer: str = ""
    usage: AgentUsage | None = None


@dataclass
class TestOutput:
    report: str
    success: bool
    passed_count: int | None = None
    total_count: int | None = None
    recommendation: str | None = None
    usage: AgentUsage | None = None


class AgentActionFailed(Exception):
    """Agent tried but could not complete the action."""
    def __init__(self, message: str, partial_output: str = ""):
        self.partial_output = partial_output
        super().__init__(message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_types.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/agents/types.py tests/agents/test_types.py
git commit -m "feat: add structured output types for agent abstraction"
```

---

### Task 2: Rewrite BaseAgent

**Files:**
- Modify: `src/shadowcoder/agents/base.py`
- Modify: `tests/agents/test_registry.py`

- [ ] **Step 1: Rewrite base.py**

```python
# src/shadowcoder/agents/base.py
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from shadowcoder.agents.types import (
    AgentRequest, DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
)


class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def design(self, request: AgentRequest) -> DesignOutput:
        ...

    @abstractmethod
    async def develop(self, request: AgentRequest) -> DevelopOutput:
        ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewOutput:
        ...

    @abstractmethod
    async def test(self, request: AgentRequest) -> TestOutput:
        ...

    async def _get_files_changed(self, worktree_path: str) -> list[str]:
        """Get changed + untracked files via git."""
        if not worktree_path:
            return []

        async def _run(args):
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            return [f for f in stdout.decode().strip().splitlines() if f]

        changed = await _run(["git", "diff", "--name-only", "HEAD"])
        untracked = await _run(["git", "ls-files", "--others", "--exclude-standard"])
        return sorted(set(changed + untracked))

    def _extract_json(self, raw: str) -> dict:
        """Extract JSON from raw text, handling markdown code blocks."""
        import json
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
```

- [ ] **Step 2: Update test_registry.py to use new interface**

```python
# tests/agents/test_registry.py
import pytest
from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import (
    AgentRequest, DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
)
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.config import Config


class FakeAgent(BaseAgent):
    async def design(self, request):
        return DesignOutput(document="ok")

    async def develop(self, request):
        return DevelopOutput(summary="ok")

    async def review(self, request):
        return ReviewOutput(passed=True, reviewer="fake")

    async def test(self, request):
        return TestOutput(report="ok", success=True)


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

- [ ] **Step 3: Run tests**

Run: `pytest tests/agents/test_registry.py tests/agents/test_types.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/shadowcoder/agents/base.py tests/agents/test_registry.py
git commit -m "refactor: rewrite BaseAgent with per-action abstract methods"
```

---

### Task 3: Migrate types out of core/models.py

**Files:**
- Modify: `src/shadowcoder/core/models.py`
- Modify: `tests/core/test_models.py`
- Modify: `src/shadowcoder/core/issue_store.py`
- Modify: `tests/core/test_issue_store.py`

- [ ] **Step 1: Remove Severity, ReviewComment, ReviewResult from models.py**

In `src/shadowcoder/core/models.py`, delete:
- `class Severity(Enum)` (lines 17-21)
- `class ReviewComment` dataclass (lines 24-27)
- `class ReviewResult` dataclass (lines 30-33)

Keep: `IssueStatus`, `TaskStatus`, `VALID_TRANSITIONS`, `InvalidTransitionError`, `Issue`, `Task`.

- [ ] **Step 2: Update test_models.py**

Remove `test_review_result` test (it tests `ReviewResult` which moved to `agents/types.py` and is already tested in `test_types.py`). Remove `Severity` import.

- [ ] **Step 3: Update issue_store.py imports**

Change:
```python
# Before
from shadowcoder.core.models import (
    InvalidTransitionError, Issue, IssueStatus, ReviewResult, Severity, VALID_TRANSITIONS,
)
# After
from shadowcoder.core.models import (
    InvalidTransitionError, Issue, IssueStatus, VALID_TRANSITIONS,
)
from shadowcoder.agents.types import ReviewComment, ReviewOutput, Severity
```

Change `append_review` parameter type from `ReviewResult` to `ReviewOutput` (same fields, just the type name).

- [ ] **Step 4: Update test_issue_store.py imports**

Change:
```python
# Before
from shadowcoder.core.models import (
    IssueStatus, InvalidTransitionError, Severity, ReviewComment, ReviewResult,
)
# After
from shadowcoder.core.models import IssueStatus, InvalidTransitionError
from shadowcoder.agents.types import Severity, ReviewComment, ReviewOutput
```

Change `ReviewResult(...)` → `ReviewOutput(...)` in `test_append_review`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/core/test_models.py tests/core/test_issue_store.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/models.py src/shadowcoder/core/issue_store.py tests/core/test_models.py tests/core/test_issue_store.py
git commit -m "refactor: migrate ReviewComment, Severity to agents/types.py"
```

---

### Task 4: Rewrite ClaudeCodeAgent

**Files:**
- Modify: `src/shadowcoder/agents/claude_code.py`
- Modify: `tests/agents/test_claude_code.py`

- [ ] **Step 1: Rewrite claude_code.py**

```python
# src/shadowcoder/agents/claude_code.py
from __future__ import annotations

import asyncio
import json
import logging
import re
from textwrap import dedent

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import (
    AgentRequest, DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
    ReviewComment, Severity,
)

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
}


class ClaudeCodeAgent(BaseAgent):

    def _get_model(self) -> str:
        return self.config.get("model", "sonnet")

    def _get_permission_mode(self) -> str:
        return self.config.get("permission_mode", "auto")

    async def _run_claude(self, prompt: str, cwd: str | None = None,
                          system_prompt: str | None = None) -> str:
        cmd = [
            "claude", "-p",
            "--output-format", "text",
            "--model", self._get_model(),
            "--permission-mode", self._get_permission_mode(),
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate(input=prompt.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {stderr.decode().strip()}")
        return stdout.decode("utf-8")

    def _build_context(self, request: AgentRequest) -> str:
        issue = request.issue
        parts = [f"Issue: {issue.title} (#{issue.id})"]
        for section_name in ["需求", "设计", "Design Review", "开发步骤", "Dev Review", "测试"]:
            content = issue.sections.get(section_name, "")
            if content:
                parts.append(f"\n--- {section_name} ---\n{content}")
        return "\n".join(parts)

    async def design(self, request: AgentRequest) -> DesignOutput:
        system = dedent("""\
            You are a senior software architect. Produce a detailed technical
            design document. Include: architecture, components, data structures,
            interfaces, error handling, and testing strategy.
            If there are previous review comments, address each one specifically.
            Output ONLY the design document in markdown format.
        """)
        prompt = f"{self._build_context(request)}\n\nProduce the technical design."
        cwd = request.context.get("worktree_path")
        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)
        return DesignOutput(document=result)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        system = dedent("""\
            You are a senior software engineer. Implement the code based on
            the design document. You MUST:
            1. Create actual source files in the working directory
            2. Write tests
            3. Make sure the code compiles/runs without errors
            If there are previous review comments or test failures,
            address each one specifically.
            After writing code, provide a summary of what you implemented.
        """)
        prompt = f"{self._build_context(request)}\n\nImplement the code. Write actual files."
        cwd = request.context.get("worktree_path")
        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)
        files = await self._get_files_changed(cwd)
        return DevelopOutput(summary=result, files_changed=files)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        system = dedent("""\
            You are a code reviewer. Review the design or implementation
            against the requirements.
            For each issue, classify severity: critical, high, medium, low.
            Output ONLY JSON: {"passed": bool, "comments": [{"severity": "...", "message": "...", "location": "..."}]}
            Pass only if no critical or high severity issues.
        """)
        prompt = f"{self._build_context(request)}\n\nReview against requirements."
        cwd = request.context.get("worktree_path")
        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)
        try:
            data = self._extract_json(result)
            comments = [ReviewComment(
                severity=_SEVERITY_MAP.get(c.get("severity", "medium"), Severity.MEDIUM),
                message=c.get("message", ""),
                location=c.get("location"),
            ) for c in data.get("comments", [])]
            return ReviewOutput(passed=data.get("passed", False),
                               comments=comments, reviewer="claude-code")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Failed to parse review JSON: %s", e)
            return ReviewOutput(
                passed=False,
                comments=[ReviewComment(severity=Severity.MEDIUM,
                    message=f"Review output parse error: {result[:200]}")],
                reviewer="claude-code")

    async def test(self, request: AgentRequest) -> TestOutput:
        system = dedent("""\
            You are a QA engineer. Run tests and benchmarks for this project.
            1. Find and run test files (pytest, go test, etc.)
            2. Verify acceptance criteria from requirements
            3. Report results
            End output with: RESULT: PASS  or  RESULT: FAIL recommendation=develop (or =design)
        """)
        prompt = f"{self._build_context(request)}\n\nRun all tests and benchmarks."
        cwd = request.context.get("worktree_path")
        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)
        success, recommendation, passed, total = self._parse_test_result(result)
        return TestOutput(report=result, success=success,
                         passed_count=passed, total_count=total,
                         recommendation=recommendation)

    @staticmethod
    def _parse_test_result(raw: str) -> tuple[bool, str | None, int | None, int | None]:
        success = False
        recommendation = None
        passed_count = total_count = None
        for line in reversed(raw.strip().splitlines()):
            line = line.strip()
            if line.startswith("RESULT:"):
                success = "PASS" in line
                if "recommendation=" in line:
                    recommendation = line.split("recommendation=")[1].strip()
                break
        m = re.search(r"(\d+)/(\d+)", raw)
        if m:
            passed_count, total_count = int(m.group(1)), int(m.group(2))
        return success, recommendation, passed_count, total_count
```

- [ ] **Step 2: Rewrite test_claude_code.py**

```python
# tests/agents/test_claude_code.py
import pytest
from unittest.mock import AsyncMock
from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.types import (
    AgentRequest, DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
)
from shadowcoder.core.models import Issue, IssueStatus
from datetime import datetime


@pytest.fixture
def agent():
    return ClaudeCodeAgent({"type": "claude_code"})


@pytest.fixture
def sample_request():
    issue = Issue(id=1, title="Test", status=IssueStatus.DESIGNING,
                  priority="medium", created=datetime.now(), updated=datetime.now())
    return AgentRequest(action="design", issue=issue, context={"worktree_path": "/tmp"})


async def test_design(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="Design doc content")
    result = await agent.design(sample_request)
    assert isinstance(result, DesignOutput)
    assert result.document == "Design doc content"


async def test_develop(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="Implementation summary")
    agent._get_files_changed = AsyncMock(return_value=["src/main.py", "tests/test_main.py"])
    result = await agent.develop(sample_request)
    assert isinstance(result, DevelopOutput)
    assert result.files_changed == ["src/main.py", "tests/test_main.py"]


async def test_review_pass(agent, sample_request):
    agent._run_claude = AsyncMock(return_value='{"passed": true, "comments": []}')
    result = await agent.review(sample_request)
    assert isinstance(result, ReviewOutput)
    assert result.passed is True
    assert result.reviewer == "claude-code"


async def test_review_with_issues(agent, sample_request):
    agent._run_claude = AsyncMock(return_value='{"passed": false, "comments": [{"severity": "high", "message": "Missing X", "location": "a.py:10"}]}')
    result = await agent.review(sample_request)
    assert not result.passed
    assert len(result.comments) == 1
    assert result.comments[0].location == "a.py:10"


async def test_review_unparseable(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="Not JSON at all")
    result = await agent.review(sample_request)
    assert not result.passed


async def test_test_pass(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="10/10 passed\nRESULT: PASS")
    result = await agent.test(sample_request)
    assert isinstance(result, TestOutput)
    assert result.success is True
    assert result.passed_count == 10
    assert result.total_count == 10


async def test_test_fail_with_recommendation(agent, sample_request):
    agent._run_claude = AsyncMock(return_value="5/10 failed\nRESULT: FAIL recommendation=develop")
    result = await agent.test(sample_request)
    assert result.success is False
    assert result.recommendation == "develop"
    assert result.passed_count == 5
    assert result.total_count == 10
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/agents/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/shadowcoder/agents/claude_code.py tests/agents/test_claude_code.py
git commit -m "refactor: rewrite ClaudeCodeAgent with structured output methods"
```

---

### Task 5: Update Engine

**Files:**
- Modify: `src/shadowcoder/core/engine.py`
- Modify: `tests/core/test_engine.py`
- Modify: `tests/core/test_engine_test_retry.py`

This is the largest task. Engine switches from parsing `AgentResponse` to using structured output types.

- [ ] **Step 1: Update engine.py imports and _run_with_review**

In `engine.py`:
- Remove: `from shadowcoder.agents.base import AgentRequest`
- Add: `from shadowcoder.agents.types import AgentRequest, AgentActionFailed`
- Remove: all `AgentResponse` references, `response.success`, `response.content`, `response.metadata` parsing

Rewrite `_run_with_review`:

```python
async def _run_with_review(self, issue, task, action, review_stage,
                            success_status, section_key, review_section_key):
    max_rounds = self.config.get_max_review_rounds()
    try:
        for round_num in range(1, max_rounds + 1):
            target_status = IssueStatus[action.upper() + "ING"]
            issue = self.issue_store.get(issue.id)
            if issue.status != target_status:
                self.issue_store.transition_status(issue.id, target_status)
            issue = self.issue_store.get(issue.id)
            action_label = action.capitalize()
            self._log(issue.id, f"{action_label} R{round_num} 开始")
            await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

            agent = self.agents.get(issue.assignee or "default")
            request = AgentRequest(action=action, issue=issue,
                context={"worktree_path": task.worktree_path})

            # Call the appropriate method
            if action == "design":
                output = await agent.design(request)
                self.issue_store.update_section(issue.id, section_key, output.document)
                self._log(issue.id, f"{action_label} R{round_num} Agent 产出\n"
                    f"内容长度: {len(output.document)} 字符")
            elif action == "develop":
                output = await agent.develop(request)
                self.issue_store.update_section(issue.id, section_key, output.summary)
                self._log(issue.id, f"{action_label} R{round_num} Agent 产出\n"
                    f"Files changed: {', '.join(output.files_changed)}")

            # Review
            self.issue_store.transition_status(issue.id, IssueStatus[review_stage.upper()])
            issue = self.issue_store.get(issue.id)

            all_passed = await self._run_all_reviewers(issue, task, action, review_section_key)

            if all_passed:
                self.issue_store.transition_status(issue.id, success_status)
                self._log(issue.id, f"{action_label} Review R{round_num} — PASSED → {success_status.value}")
                task.status = TaskStatus.COMPLETED
                await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                    {"issue_id": issue.id, "task_id": task.task_id}))
                return

            review_content = self.issue_store.get(issue.id).sections.get(review_section_key, "")
            reject_lines = [l for l in review_content.split("\n") if "[HIGH]" in l]
            reject_summary = "; ".join(l.strip()[:80] for l in reject_lines[-5:])
            self._log(issue.id, f"{action_label} Review R{round_num} — NOT PASSED\n"
                f"HIGH issues: {reject_summary or '(see review section)'}")
            issue = self.issue_store.get(issue.id)

        self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
        self._log(issue.id, f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
        task.status = TaskStatus.FAILED
        await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
            {"issue_id": issue.id, "task_id": task.task_id,
             "reason": f"review not passed after {max_rounds} rounds"}))

    except AgentActionFailed as e:
        if e.partial_output:
            self.issue_store.update_section(issue.id, section_key, e.partial_output)
        self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
        self._log(issue.id, f"{action_label} 软失败 → FAILED: {e}")
        task.status = TaskStatus.FAILED
        await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
            {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
    except Exception as e:
        self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
        self._log(issue.id, f"{action_label} 异常 → FAILED: {e}")
        task.status = TaskStatus.FAILED
        await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
            {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
```

- [ ] **Step 2: Rewrite _on_test**

Replace the `agent.execute(AgentRequest(action="test", ...))` call with `agent.test(request)`.
Use `output.success`, `output.recommendation`, `output.passed_count`, `output.total_count` directly.

- [ ] **Step 3: Update _run_all_reviewers**

`reviewer.review(request)` now returns `ReviewOutput` instead of `ReviewResult`. The fields are the same, just update the import and type references.

- [ ] **Step 4: Update test_engine.py mocks**

All mocks change from:
```python
agent.execute = AsyncMock(return_value=AgentResponse(content="...", success=True))
agent.review = AsyncMock(return_value=ReviewResult(passed=True, ...))
```
To:
```python
agent.design = AsyncMock(return_value=DesignOutput(document="..."))
agent.develop = AsyncMock(return_value=DevelopOutput(summary="..."))
agent.review = AsyncMock(return_value=ReviewOutput(passed=True, ...))
agent.test = AsyncMock(return_value=TestOutput(report="...", success=True))
```

- [ ] **Step 5: Update test_engine_test_retry.py mocks**

Same pattern: replace `AgentResponse` with `TestOutput`/`DevelopOutput`/`DesignOutput`.

- [ ] **Step 6: Run engine tests**

Run: `pytest tests/core/test_engine.py tests/core/test_engine_test_retry.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py tests/core/test_engine_test_retry.py
git commit -m "refactor: engine uses structured output types, zero parsing"
```

---

### Task 6: Update remaining tests

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `tests/test_e2e.py`
- Modify: `tests/test_e2e_real_task.py`
- Modify: `tests/test_e2e_sql_engine.py`
- Modify: `tests/core/test_create_with_description.py`

- [ ] **Step 1: Update test_integration.py**

Replace `AgentResponse` with `DesignOutput`/`DevelopOutput`/`TestOutput`.
Replace `ReviewResult` with `ReviewOutput`.

- [ ] **Step 2: Update test_e2e.py**

The `E2EAgent` class needs to implement `design()/develop()/review()/test()` instead of `execute()/review()`.

- [ ] **Step 3: Update test_e2e_real_task.py**

The `RealisticAgent` class: same refactor.

- [ ] **Step 4: Update test_e2e_sql_engine.py**

The `StateDrivenAgent` class: `execute()` dispatched by action internally → split into 4 methods. The internal logic (`_do_design`, `_do_develop`, `_do_test`) becomes the method bodies. `review()` stays similar.

- [ ] **Step 5: Update test_create_with_description.py**

Just fix import if needed (may only need `MagicMock` for registry).

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add tests/
git commit -m "refactor: update all tests to use structured agent output types"
```

---

### Task 7: Final cleanup

- [ ] **Step 1: Remove dead imports**

Grep for any remaining references to `AgentResponse`, `AgentStream`, `ReviewResult` (from `core.models`):

```bash
grep -rn "AgentResponse\|AgentStream\|from shadowcoder.core.models import.*ReviewResult" src/ tests/
```

Fix any remaining references.

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All PASS, zero references to deleted types

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove dead type references after agent refactor"
```
