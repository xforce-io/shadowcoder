# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-time web dashboard that lets you observe ShadowCoder task execution in a browser — pipeline progress, live log streaming, changed files, review/gate results.

**Architecture:** FastAPI server reads `.shadowcoder/issues/` files directly (no engine changes). Watchdog monitors file changes, pushes updates via SSE. Frontend uses HTMX + Jinja2 for partial DOM updates. Launched as standalone `dashboard` subcommand.

**Tech Stack:** FastAPI, uvicorn, watchdog, Jinja2, HTMX (CDN)

---

## File Structure

```
src/shadowcoder/dashboard/
├── __init__.py            # Package init
├── server.py              # FastAPI app, routes, SSE endpoints, CLI entry
├── watcher.py             # Watchdog-based file monitoring, change broadcasting
├── parsers.py             # Log/issue/feedback/worktree parsing
├── state.py               # DashboardState: central state container per issue
├── templates/
│   ├── base.html          # Page shell: CSS, HTMX script, layout grid
│   ├── dashboard.html     # Full dashboard (extends base)
│   └── partials/
│       ├── topbar.html    # Project name + issue selector
│       ├── status_bar.html
│       ├── pipeline.html  # Progress bar + retry timeline
│       ├── log.html       # Single log entry (appended via SSE)
│       ├── files.html     # Changed files list
│       ├── review.html    # Review summary
│       ├── gate.html      # Gate output
│       └── metrics.html   # Metric gate comparison table
└── static/
    ├── style.css          # Dark theme, pipeline colors, animations
    └── dashboard.js       # Auto-scroll, elapsed timer, SSE reconnect
```

**Modified files:**
- `pyproject.toml` — add `dashboard` optional dependency group
- `scripts/run_real.py` — add `dashboard` subcommand

---

### Task 1: Dependencies and Package Scaffold

**Files:**
- Modify: `pyproject.toml`
- Create: `src/shadowcoder/dashboard/__init__.py`

- [ ] **Step 1: Add dashboard optional dependency group to pyproject.toml**

In `pyproject.toml`, add under `[project.optional-dependencies]`:

```toml
dashboard = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "watchdog>=6.0",
    "jinja2>=3.1",
]
```

- [ ] **Step 2: Create the dashboard package**

Create `src/shadowcoder/dashboard/__init__.py`:

```python
"""ShadowCoder web dashboard — real-time execution observer."""
```

- [ ] **Step 3: Install dashboard dependencies**

Run: `pip install -e ".[dev,dashboard]"`
Expected: Successfully installed fastapi, uvicorn, watchdog, jinja2

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/shadowcoder/dashboard/__init__.py
git commit -m "feat(dashboard): add package scaffold and dependencies"
```

---

### Task 2: Log Parser

**Files:**
- Create: `src/shadowcoder/dashboard/parsers.py`
- Create: `tests/dashboard/test_parsers.py`

- [ ] **Step 1: Write tests for LogParser**

Create `tests/dashboard/test_parsers.py`:

```python
from shadowcoder.dashboard.parsers import LogEntry, LogParser


class TestLogParser:
    def test_parse_single_entry(self):
        raw = "[2026-03-26 11:42:10] Issue 创建: Add auth\n"
        entries = LogParser.parse_all(raw)
        assert len(entries) == 1
        assert entries[0].timestamp == "11:42"
        assert entries[0].text == "Issue 创建: Add auth"
        assert entries[0].category == "info"

    def test_parse_multiline_entry(self):
        raw = (
            "[2026-03-26 11:45:00] Design Review\n"
            "PASSED (CRITICAL=0, HIGH=0, 7 comments)\n"
            "  [MEDIUM] (src/main.py) refactor needed\n"
        )
        entries = LogParser.parse_all(raw)
        assert len(entries) == 1
        assert "Design Review" in entries[0].text
        assert len(entries[0].continuation) == 2

    def test_parse_gate_fail(self):
        raw = "[2026-03-26 11:48:00] Gate FAIL R1: 3 tests failed\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "error"

    def test_parse_gate_pass(self):
        raw = "[2026-03-26 11:50:00] Gate PASS R2\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "success"

    def test_parse_usage(self):
        raw = "[2026-03-26 11:45:03] Usage: 1200+800 tokens, $0.08\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "info"

    def test_parse_metric_gate_fail(self):
        raw = "[2026-03-26 11:50:00] Metric gate FAIL: recall 0.32 < 0.50\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "error"

    def test_parse_revert(self):
        raw = "[2026-03-26 11:50:00] 代码回滚至 checkpoint\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "warning"

    def test_parse_developing_start(self):
        raw = "[2026-03-26 11:46:00] Develop R1 开始\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "active"

    def test_tail_new_lines(self):
        raw_initial = "[2026-03-26 11:42:10] Line 1\n"
        parser = LogParser()
        entries = parser.parse_tail(raw_initial)
        assert len(entries) == 1
        raw_appended = raw_initial + "[2026-03-26 11:43:00] Line 2\n"
        new_entries = parser.parse_tail(raw_appended)
        assert len(new_entries) == 1
        assert new_entries[0].text == "Line 2"

    def test_empty_log(self):
        entries = LogParser.parse_all("")
        assert entries == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/dashboard/test_parsers.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement LogParser**

Create `src/shadowcoder/dashboard/parsers.py`:

```python
"""Parsers for issue log, feedback, and worktree data."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2})\] (.*)$")

# Category detection patterns (checked in order, first match wins)
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("error", re.compile(r"Gate FAIL|FAIL|failed|error", re.IGNORECASE)),
    ("warning", re.compile(r"回滚|revert|升级|escalat|⚠|BLOCKED", re.IGNORECASE)),
    ("success", re.compile(r"Gate PASS|PASS|DONE|✓|通过", re.IGNORECASE)),
    ("active", re.compile(r"开始|started|running|Develop R\d|Design R\d", re.IGNORECASE)),
]


@dataclass
class LogEntry:
    timestamp: str  # "HH:MM"
    text: str  # first line content
    category: str  # "info", "error", "success", "warning", "active"
    continuation: list[str] = field(default_factory=list)  # subsequent lines


def _classify(text: str) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return "info"


class LogParser:
    """Parses ShadowCoder issue log files.

    Supports both full parsing (parse_all) and incremental tailing
    (parse_tail) which tracks the byte offset of the last read.
    """

    def __init__(self) -> None:
        self._offset: int = 0

    @staticmethod
    def parse_all(raw: str) -> list[LogEntry]:
        """Parse an entire log string into entries."""
        if not raw.strip():
            return []
        entries: list[LogEntry] = []
        current: LogEntry | None = None
        for line in raw.splitlines():
            m = _TS_RE.match(line)
            if m:
                if current is not None:
                    entries.append(current)
                current = LogEntry(
                    timestamp=m.group(2),
                    text=m.group(3),
                    category=_classify(m.group(3)),
                )
            elif current is not None:
                current.continuation.append(line)
        if current is not None:
            entries.append(current)
        return entries

    def parse_tail(self, raw: str) -> list[LogEntry]:
        """Parse only new content since last call.

        Pass the full file content each time; the parser tracks
        how far it has already read.
        """
        new_content = raw[self._offset:]
        self._offset = len(raw)
        return self.parse_all(new_content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/dashboard/test_parsers.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/dashboard/parsers.py tests/dashboard/test_parsers.py
git commit -m "feat(dashboard): add LogParser with incremental tailing"
```

---

### Task 3: Feedback and Worktree Parsers

**Files:**
- Modify: `src/shadowcoder/dashboard/parsers.py`
- Modify: `tests/dashboard/test_parsers.py`

- [ ] **Step 1: Write tests for FeedbackParser and WorktreeParser**

Append to `tests/dashboard/test_parsers.py`:

```python
from shadowcoder.dashboard.parsers import FeedbackSummary, FeedbackParser
from shadowcoder.dashboard.parsers import ChangedFile, WorktreeParser


class TestFeedbackParser:
    def test_parse_empty(self):
        data = {"items": [], "proposed_tests": [], "acceptance_tests": [], "supplementary_tests": []}
        summary = FeedbackParser.summarize(data)
        assert summary.verdict is None
        assert summary.critical == 0
        assert summary.high == 0

    def test_parse_with_items(self):
        data = {
            "items": [
                {"id": "1", "category": "bug", "description": "fix it",
                 "severity": "CRITICAL", "resolved": False},
                {"id": "2", "category": "style", "description": "rename",
                 "severity": "MEDIUM", "resolved": True},
                {"id": "3", "category": "bug", "description": "another",
                 "severity": "HIGH", "resolved": False},
            ],
            "proposed_tests": [{"name": "test_a", "passed": True}],
            "acceptance_tests": [],
            "supplementary_tests": [],
        }
        summary = FeedbackParser.summarize(data)
        assert summary.critical == 1
        assert summary.high == 1
        assert summary.medium == 0  # resolved items excluded
        assert summary.total == 2  # unresolved only

    def test_missing_severity_key(self):
        data = {
            "items": [{"id": "1", "category": "bug", "description": "x"}],
            "proposed_tests": [], "acceptance_tests": [], "supplementary_tests": [],
        }
        summary = FeedbackParser.summarize(data)
        assert summary.total == 0  # items without severity are skipped


class TestWorktreeParser:
    def test_parse_name_status(self):
        output = "A\tsrc/auth.py\nM\tsrc/main.py\nD\told.py\n"
        files = WorktreeParser.parse_name_status(output)
        assert len(files) == 3
        assert files[0].status == "A"
        assert files[0].path == "src/auth.py"
        assert files[1].status == "M"
        assert files[2].status == "D"

    def test_parse_empty(self):
        files = WorktreeParser.parse_name_status("")
        assert files == []

    def test_parse_stat(self):
        output = " src/auth.py | 142 +++\n src/main.py |  15 ++-\n 2 files changed\n"
        stats = WorktreeParser.parse_stat(output)
        assert stats["src/auth.py"] == "142 +++"
        assert stats["src/main.py"] == "15 ++-"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/dashboard/test_parsers.py::TestFeedbackParser tests/dashboard/test_parsers.py::TestWorktreeParser -v`
Expected: FAIL — classes not found

- [ ] **Step 3: Implement FeedbackParser and WorktreeParser**

Append to `src/shadowcoder/dashboard/parsers.py`:

```python
@dataclass
class FeedbackSummary:
    verdict: str | None  # "PASSED", "NOT PASSED", or None
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0  # total unresolved

    @property
    def passed(self) -> bool | None:
        if self.verdict is None:
            return None
        return self.verdict == "PASSED"


class FeedbackParser:
    """Summarizes feedback.json into severity counts."""

    @staticmethod
    def summarize(data: dict) -> FeedbackSummary:
        summary = FeedbackSummary(verdict=data.get("verdict"))
        for item in data.get("items", []):
            severity = item.get("severity")
            if not severity or item.get("resolved"):
                continue
            summary.total += 1
            sev = severity.upper()
            if sev == "CRITICAL":
                summary.critical += 1
            elif sev == "HIGH":
                summary.high += 1
            elif sev == "MEDIUM":
                summary.medium += 1
            elif sev == "LOW":
                summary.low += 1
        return summary


@dataclass
class ChangedFile:
    status: str  # "A" (added), "M" (modified), "D" (deleted)
    path: str
    stat: str = ""  # e.g. "142 +++"


class WorktreeParser:
    """Parses git diff output for worktree file changes."""

    @staticmethod
    def parse_name_status(output: str) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        for line in output.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                files.append(ChangedFile(status=parts[0].strip(), path=parts[1].strip()))
        return files

    @staticmethod
    def parse_stat(output: str) -> dict[str, str]:
        """Parse `git diff --stat` output into {path: stat_string}."""
        stats: dict[str, str] = {}
        for line in output.strip().splitlines():
            if "|" not in line:
                continue
            parts = line.split("|", 1)
            if len(parts) == 2:
                path = parts[0].strip()
                stat = parts[1].strip()
                stats[path] = stat
        return stats

    @staticmethod
    def get_changed_files(worktree_path: str) -> list[ChangedFile]:
        """Run git commands in the worktree to get changed files."""
        try:
            ns = subprocess.run(
                ["git", "diff", "--name-status", "HEAD~1"],
                cwd=worktree_path, capture_output=True, text=True, timeout=10,
            )
            st = subprocess.run(
                ["git", "diff", "--stat", "HEAD~1"],
                cwd=worktree_path, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        files = WorktreeParser.parse_name_status(ns.stdout)
        stats = WorktreeParser.parse_stat(st.stdout)
        for f in files:
            f.stat = stats.get(f.path, "")
        return files
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/dashboard/test_parsers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/dashboard/parsers.py tests/dashboard/test_parsers.py
git commit -m "feat(dashboard): add FeedbackParser and WorktreeParser"
```

---

### Task 4: DashboardState

**Files:**
- Create: `src/shadowcoder/dashboard/state.py`
- Create: `tests/dashboard/test_state.py`

- [ ] **Step 1: Write tests for DashboardState**

Create `tests/dashboard/test_state.py`:

```python
import json
import os
from pathlib import Path

from shadowcoder.dashboard.state import DashboardState, PipelineStage


def _make_issue_dir(tmp_path: Path, issue_id: int = 1) -> Path:
    """Create a minimal issue directory for testing."""
    d = tmp_path / ".shadowcoder" / "issues" / f"{issue_id:04d}"
    d.mkdir(parents=True)

    # Write issue.md with frontmatter
    (d / "issue.md").write_text(
        '---\n'
        f'id: {issue_id}\n'
        'title: "Test Issue"\n'
        'status: "developing"\n'
        'priority: "medium"\n'
        'created: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T11:00:00"\n'
        'tags: []\n'
        '---\n'
    )

    # Write log
    (d / "issue.log").write_text(
        "[2026-04-01 10:00:00] Issue 创建: Test Issue\n"
        "[2026-04-01 10:05:00] Design R1 开始\n"
        "[2026-04-01 10:10:00] Design Review\n"
        "PASSED (CRITICAL=0, HIGH=0)\n"
        "[2026-04-01 10:15:00] Develop R1 开始\n"
    )

    # Write feedback.json
    (d / "feedback.json").write_text(json.dumps({
        "items": [
            {"id": "1", "category": "bug", "description": "fix",
             "severity": "MEDIUM", "resolved": False},
        ],
        "proposed_tests": [],
        "acceptance_tests": [],
        "supplementary_tests": [],
    }))

    # Create worktree dir (won't have git, so files will be empty)
    wt = tmp_path / ".shadowcoder" / "worktrees" / f"issue-{issue_id}"
    wt.mkdir(parents=True)

    return tmp_path


class TestDashboardState:
    def test_load_issues(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        issues = state.get_issue_list()
        assert len(issues) == 1
        assert issues[0]["id"] == 1
        assert issues[0]["title"] == "Test Issue"
        assert issues[0]["status"] == "developing"

    def test_load_issue_detail(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(1)
        assert detail["title"] == "Test Issue"
        assert detail["status"] == "developing"
        assert len(detail["log_entries"]) == 4  # 4 log entries (3rd is multiline)
        assert detail["feedback"]["total"] == 1

    def test_pipeline_stages(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(1)
        stages = detail["pipeline"]
        # Design and Design Review should be "completed",
        # Develop should be "active", rest "pending"
        assert stages[0].state == "completed"  # Design
        assert stages[1].state == "completed"  # Design Review
        assert stages[2].state == "active"     # Develop
        assert stages[3].state == "pending"    # Gate
        assert stages[4].state == "pending"    # Dev Review
        assert stages[5].state == "pending"    # Acceptance

    def test_nonexistent_issue(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(99)
        assert detail is None

    def test_retry_timeline_from_log(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        d = repo / ".shadowcoder" / "issues" / "0001"
        (d / "issue.log").write_text(
            "[2026-04-01 10:00:00] Design R1 开始\n"
            "[2026-04-01 10:05:00] Design Review\n"
            "PASSED\n"
            "[2026-04-01 10:10:00] Develop R1 开始\n"
            "[2026-04-01 10:15:00] Gate FAIL R1: 3 tests failed\n"
            "[2026-04-01 10:20:00] Develop R2 开始\n"
            "[2026-04-01 10:25:00] Gate PASS R2\n"
        )
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(1)
        retries = detail["retries"]
        assert len(retries) == 2
        assert retries[0]["round"] == "R1"
        assert retries[0]["result"] == "fail"
        assert retries[1]["round"] == "R2"
        assert retries[1]["result"] == "pass"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/dashboard/test_state.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement DashboardState**

Create `src/shadowcoder/dashboard/state.py`:

```python
"""Central state container — reads issue files and produces dashboard data."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from shadowcoder.dashboard.parsers import (
    FeedbackParser,
    LogParser,
    WorktreeParser,
)

_ISSUE_GLOB = "[0-9][0-9][0-9][0-9]"

# Maps issue status to which pipeline stages are completed
_STATUS_PROGRESS = {
    "created": 0,
    "designing": 0,
    "design_review": 1,
    "approved": 2,
    "developing": 2,
    "dev_review": 4,
    "done": 6,
    "blocked": -1,  # special handling
    "failed": -1,
    "cancelled": -1,
}

_PIPELINE_STAGES = ["Design", "Design Review", "Develop", "Gate", "Dev Review", "Acceptance"]

# Patterns to detect gate results in log text
_GATE_FAIL_RE = re.compile(r"Gate FAIL R(\d+)")
_GATE_PASS_RE = re.compile(r"Gate PASS R(\d+)")
_METRIC_FAIL_RE = re.compile(r"Metric gate FAIL|metric_gate", re.IGNORECASE)
_REVERT_RE = re.compile(r"回滚|revert", re.IGNORECASE)
_DEVELOP_RE = re.compile(r"Develop (?:R(\d+)|Attempt (\d+))")
_COST_RE = re.compile(r"\$(\d+\.?\d*)")
_USAGE_RE = re.compile(r"总计|=== 总计 ===")


@dataclass
class PipelineStage:
    name: str
    state: str  # "completed", "active", "pending", "failed", "reverted"


class DashboardState:
    """Reads .shadowcoder/issues/ and produces structured dashboard data.

    This class does not cache — each call re-reads from disk.
    Caching/diffing is handled by the watcher layer.
    """

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path
        self.issues_dir = Path(repo_path) / ".shadowcoder" / "issues"
        self.worktrees_dir = Path(repo_path) / ".shadowcoder" / "worktrees"

    def get_issue_list(self) -> list[dict]:
        """Return list of {id, title, status, updated} for all issues."""
        issues: list[dict] = []
        if not self.issues_dir.exists():
            return issues
        for d in sorted(self.issues_dir.glob(_ISSUE_GLOB)):
            if not d.is_dir():
                continue
            md_path = d / "issue.md"
            if not md_path.exists():
                continue
            try:
                post = frontmatter.load(str(md_path))
                issues.append({
                    "id": post["id"],
                    "title": post["title"],
                    "status": post["status"],
                    "updated": post.get("updated", ""),
                })
            except Exception:
                continue
        return issues

    def get_issue_detail(self, issue_id: int) -> dict | None:
        """Return full dashboard data for a single issue."""
        d = self.issues_dir / f"{issue_id:04d}"
        md_path = d / "issue.md"
        if not md_path.exists():
            return None

        try:
            post = frontmatter.load(str(md_path))
        except Exception:
            return None

        status = post["status"]

        # Parse log
        log_path = d / "issue.log"
        log_raw = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        log_entries = LogParser.parse_all(log_raw)

        # Parse feedback
        fb_path = d / "feedback.json"
        if fb_path.exists():
            fb_data = json.loads(fb_path.read_text(encoding="utf-8"))
        else:
            fb_data = {"items": [], "proposed_tests": [],
                       "acceptance_tests": [], "supplementary_tests": []}
        feedback = FeedbackParser.summarize(fb_data)

        # Pipeline stages
        pipeline = self._build_pipeline(status, log_entries)

        # Retry timeline
        retries = self._extract_retries(log_entries)

        # Changed files
        wt_path = self.worktrees_dir / f"issue-{issue_id}"
        if wt_path.exists():
            changed_files = WorktreeParser.get_changed_files(str(wt_path))
        else:
            changed_files = []

        # Cost
        cost = self._extract_cost(log_raw)

        # Gate output (last gate entry from log)
        gate_output = self._extract_gate_output(log_entries)

        return {
            "id": post["id"],
            "title": post["title"],
            "status": status,
            "assignee": post.get("assignee"),
            "created": post.get("created", ""),
            "updated": post.get("updated", ""),
            "blocked_reason": post.get("blocked_reason"),
            "log_entries": log_entries,
            "feedback": feedback,
            "pipeline": pipeline,
            "retries": retries,
            "changed_files": changed_files,
            "cost": cost,
            "gate_output": gate_output,
        }

    def _build_pipeline(self, status: str, log_entries) -> list[PipelineStage]:
        """Build pipeline stages from issue status and log entries."""
        progress = _STATUS_PROGRESS.get(status, 0)
        stages: list[PipelineStage] = []

        for i, name in enumerate(_PIPELINE_STAGES):
            if progress == -1:
                # Terminal state: infer from log
                stage_state = self._infer_stage_from_log(i, status, log_entries)
            elif i < progress:
                stage_state = "completed"
            elif i == progress:
                stage_state = "active"
            else:
                stage_state = "pending"
            stages.append(PipelineStage(name=name, state=stage_state))

        return stages

    def _infer_stage_from_log(self, stage_idx: int, status: str, log_entries) -> str:
        """For terminal states (BLOCKED/FAILED), figure out how far we got."""
        # Check log for evidence of each stage completing
        log_text = " ".join(e.text for e in log_entries)

        completed_markers = [
            re.search(r"Design R\d", log_text) is not None,       # Design started
            re.search(r"Design Review", log_text) is not None,    # Design Review
            re.search(r"Develop R\d", log_text) is not None,      # Develop started
            re.search(r"Gate (PASS|FAIL)", log_text) is not None, # Gate ran
            re.search(r"Dev Review|develop_review", log_text, re.IGNORECASE) is not None,
            re.search(r"Acceptance|acceptance", log_text, re.IGNORECASE) is not None,
        ]

        if stage_idx < len(completed_markers) and completed_markers[stage_idx]:
            # Check if this is where it failed/blocked
            later_started = any(completed_markers[stage_idx + 1:])
            if later_started:
                return "completed"
            if status in ("blocked", "failed"):
                return "failed"
            return "completed"
        return "pending"

    def _extract_retries(self, log_entries) -> list[dict]:
        """Extract retry timeline from log entries."""
        retries: list[dict] = []
        for entry in log_entries:
            m = _GATE_FAIL_RE.search(entry.text)
            if m:
                summary = entry.text.split(":", 1)[1].strip() if ":" in entry.text else ""
                retries.append({
                    "round": f"R{m.group(1)}",
                    "result": "fail",
                    "type": "test",
                    "summary": summary,
                })
                continue
            m = _GATE_PASS_RE.search(entry.text)
            if m:
                retries.append({
                    "round": f"R{m.group(1)}",
                    "result": "pass",
                    "type": "test",
                    "summary": "",
                })
                continue
            if _METRIC_FAIL_RE.search(entry.text):
                retries.append({
                    "round": f"Attempt",
                    "result": "reverted",
                    "type": "metric",
                    "summary": entry.text,
                })
        return retries

    def _extract_cost(self, log_raw: str) -> str:
        """Extract total cost from the summary line in the log."""
        # Look for "Cost: $X.XX" in the summary section
        for line in reversed(log_raw.splitlines()):
            if "Cost:" in line or "cost" in line.lower():
                m = _COST_RE.search(line)
                if m:
                    return f"${m.group(1)}"
        # Fallback: sum all Usage lines
        total = 0.0
        for line in log_raw.splitlines():
            if line.strip().startswith("[") and "Usage:" in line:
                m = _COST_RE.search(line)
                if m:
                    total += float(m.group(1))
        if total > 0:
            return f"${total:.2f}"
        return "$0.00"

    def _extract_gate_output(self, log_entries) -> str | None:
        """Extract the last gate-related log entry."""
        for entry in reversed(log_entries):
            if "Gate" in entry.text:
                lines = [entry.text] + entry.continuation
                return "\n".join(lines)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/dashboard/test_state.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/dashboard/state.py tests/dashboard/test_state.py
git commit -m "feat(dashboard): add DashboardState for issue data aggregation"
```

---

### Task 5: File Watcher

**Files:**
- Create: `src/shadowcoder/dashboard/watcher.py`
- Create: `tests/dashboard/test_watcher.py`

- [ ] **Step 1: Write tests for FileWatcher**

Create `tests/dashboard/test_watcher.py`:

```python
import asyncio
from pathlib import Path

import pytest

from shadowcoder.dashboard.watcher import FileWatcher


@pytest.mark.asyncio
async def test_watcher_detects_log_change(tmp_path):
    issues_dir = tmp_path / ".shadowcoder" / "issues" / "0001"
    issues_dir.mkdir(parents=True)
    log_path = issues_dir / "issue.log"
    log_path.write_text("[2026-04-01 10:00:00] init\n")

    events: list[dict] = []

    async def on_change(event: dict):
        events.append(event)

    watcher = FileWatcher(str(tmp_path), on_change)
    watcher.start()

    try:
        await asyncio.sleep(0.5)  # let watcher initialize

        # Append to log
        with open(log_path, "a") as f:
            f.write("[2026-04-01 10:05:00] new entry\n")

        # Wait for event
        for _ in range(20):
            if events:
                break
            await asyncio.sleep(0.2)

        assert len(events) >= 1
        assert events[0]["issue_id"] == 1
        assert events[0]["file_type"] in ("log", "issue", "feedback")
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_ignores_unrelated_files(tmp_path):
    issues_dir = tmp_path / ".shadowcoder" / "issues" / "0001"
    issues_dir.mkdir(parents=True)

    events: list[dict] = []

    async def on_change(event: dict):
        events.append(event)

    watcher = FileWatcher(str(tmp_path), on_change)
    watcher.start()

    try:
        await asyncio.sleep(0.5)

        # Write unrelated file
        (issues_dir / "random.txt").write_text("hello")

        await asyncio.sleep(1.0)
        assert len(events) == 0
    finally:
        watcher.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/dashboard/test_watcher.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement FileWatcher**

Create `src/shadowcoder/dashboard/watcher.py`:

```python
"""Watchdog-based file monitoring for .shadowcoder/issues/."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

_ISSUE_DIR_RE = re.compile(r"/(\d{4})/")

# Files we care about
_WATCHED_FILES = {
    "issue.md": "issue",
    "issue.log": "log",
    "feedback.json": "feedback",
    "metrics_history.json": "metrics",
}

OnChangeCallback = Callable[[dict], Awaitable[None]]


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: OnChangeCallback, loop: asyncio.AbstractEventLoop) -> None:
        self._callback = callback
        self._loop = loop

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(event.src_path)

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        file_type = _WATCHED_FILES.get(path.name)
        if file_type is None:
            return

        m = _ISSUE_DIR_RE.search(path_str)
        if m is None:
            return

        issue_id = int(m.group(1))
        event = {"issue_id": issue_id, "file_type": file_type, "path": path_str}
        asyncio.run_coroutine_threadsafe(self._callback(event), self._loop)


class FileWatcher:
    """Watches .shadowcoder/issues/ for file changes and invokes async callback."""

    def __init__(self, repo_path: str, on_change: OnChangeCallback) -> None:
        self._watch_path = str(Path(repo_path) / ".shadowcoder" / "issues")
        self._on_change = on_change
        self._observer = Observer()

    def start(self) -> None:
        loop = asyncio.get_event_loop()
        handler = _Handler(self._on_change, loop)
        Path(self._watch_path).mkdir(parents=True, exist_ok=True)
        self._observer.schedule(handler, self._watch_path, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/dashboard/test_watcher.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/dashboard/watcher.py tests/dashboard/test_watcher.py
git commit -m "feat(dashboard): add FileWatcher with watchdog"
```

---

### Task 6: Static Assets (CSS + JS)

**Files:**
- Create: `src/shadowcoder/dashboard/static/style.css`
- Create: `src/shadowcoder/dashboard/static/dashboard.js`

- [ ] **Step 1: Create CSS**

Create `src/shadowcoder/dashboard/static/style.css`:

```css
:root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-tertiary: #21262d;
    --border: #30363d;
    --text-primary: #c9d1d9;
    --text-secondary: #8b949e;
    --text-muted: #484f58;
    --blue: #1f6feb;
    --blue-light: #58a6ff;
    --green: #238636;
    --green-light: #3fb950;
    --green-text: #7ee787;
    --red: #f85149;
    --orange: #f0883e;
    --yellow: #d29922;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    background: var(--bg-primary);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
}

/* Top bar */
.topbar {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 8px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
}

.topbar-brand {
    color: var(--blue-light);
    font-weight: bold;
    font-size: 14px;
}

.topbar-repo {
    color: var(--text-secondary);
    font-size: 12px;
}

.topbar-spacer { flex: 1; }

.topbar select {
    background: var(--bg-tertiary);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    font-family: inherit;
}

/* Status bar */
.status-bar {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 6px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 12px;
}

.status-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: bold;
    color: #fff;
}
.status-badge.designing, .status-badge.design_review { background: var(--blue); }
.status-badge.developing, .status-badge.dev_review { background: var(--blue); }
.status-badge.approved { background: var(--green); }
.status-badge.done { background: var(--green); }
.status-badge.blocked { background: var(--red); }
.status-badge.failed { background: var(--red); }
.status-badge.cancelled { background: var(--text-muted); }
.status-badge.created { background: var(--bg-tertiary); }

.status-sep { color: var(--border); }
.status-cost { color: var(--orange); }
.status-time { color: var(--text-secondary); }
.status-agent { color: var(--text-primary); }

/* Pipeline */
.pipeline {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
}

.pipeline-bar {
    display: flex;
    align-items: center;
    gap: 0;
    margin-bottom: 6px;
}

.pipeline-segment {
    flex: 1;
    text-align: center;
}

.pipeline-segment.develop { flex: 1.5; }

.pipeline-label {
    font-size: 10px;
    margin-bottom: 4px;
    color: var(--text-muted);
}
.pipeline-label.completed { color: var(--green-light); }
.pipeline-label.active { color: var(--blue-light); font-weight: bold; }
.pipeline-label.failed { color: var(--red); }
.pipeline-label.reverted { color: var(--yellow); }

.pipeline-fill {
    height: 6px;
    background: var(--bg-tertiary);
}
.pipeline-fill.completed { background: var(--green); }
.pipeline-fill.active { background: var(--blue); position: relative; overflow: hidden; }
.pipeline-fill.failed { background: var(--red); }
.pipeline-fill.reverted { background: var(--yellow); opacity: 0.4; }
.pipeline-fill:first-child { border-radius: 3px 0 0 3px; }
.pipeline-fill:last-child { border-radius: 0 3px 3px 0; }

.pipeline-fill.active::after {
    content: '';
    position: absolute;
    top: 0; left: 0; width: 60%; height: 100%;
    background: linear-gradient(90deg, var(--blue), var(--blue-light));
    animation: pulse-bar 2s ease-in-out infinite;
}

@keyframes pulse-bar {
    0%, 100% { opacity: 0.6; }
    50% { opacity: 1; }
}

/* Retry timeline */
.retry-timeline {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 6px;
    padding: 6px 8px;
    background: var(--bg-primary);
    border-radius: 4px;
}

.retry-item {
    display: flex;
    align-items: center;
    gap: 4px;
    background: var(--bg-tertiary);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    border-left: 3px solid var(--border);
}
.retry-item.fail { border-left-color: var(--red); }
.retry-item.pass { border-left-color: var(--green); }
.retry-item.active { border-left-color: var(--blue); border: 1px solid var(--blue); background: var(--bg-secondary); }
.retry-item.reverted { border-left-color: var(--yellow); opacity: 0.6; text-decoration: line-through; }

.retry-round { font-weight: bold; }
.retry-round.fail { color: var(--red); }
.retry-round.pass { color: var(--green-light); }
.retry-round.active { color: var(--blue-light); }
.retry-round.reverted { color: var(--yellow); }

.retry-arrow { color: var(--border); font-size: 10px; }

/* Main content */
.main-content {
    display: flex;
    gap: 0;
    height: calc(100vh - 140px);  /* subtract top bars */
    overflow: hidden;
}

/* Log panel */
.log-panel {
    flex: 1;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.panel-header {
    padding: 8px 12px;
    font-size: 11px;
    font-weight: bold;
    color: var(--blue-light);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
}

.panel-header .count {
    color: var(--text-muted);
    font-weight: normal;
}

.log-entries {
    flex: 1;
    overflow-y: auto;
    padding: 8px 12px;
}

.log-entry {
    display: flex;
    gap: 8px;
    margin-bottom: 3px;
    font-size: 12px;
    font-family: 'SF Mono', 'Fira Code', monospace;
}

.log-ts { color: var(--text-muted); flex-shrink: 0; font-size: 11px; }
.log-text { word-break: break-word; }
.log-text.info { color: var(--text-secondary); }
.log-text.active { color: var(--text-primary); }
.log-text.success { color: var(--green-text); }
.log-text.error { color: var(--red); }
.log-text.warning { color: var(--yellow); }

.log-continuation {
    padding-left: 52px;
    font-size: 11px;
    color: var(--text-secondary);
    font-style: italic;
}

/* Right panels */
.right-panels {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.right-panel {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    overflow: hidden;
}

.right-panel.files { flex: 1; overflow-y: auto; }
.right-panel.review { flex-shrink: 0; }
.right-panel.gate { flex-shrink: 0; }

.panel-body { padding: 8px 12px; }

/* Files */
.file-item {
    font-size: 11px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    display: flex;
    gap: 8px;
    padding: 2px 0;
}

.file-status { width: 14px; text-align: center; font-weight: bold; }
.file-status.A { color: var(--green-light); }
.file-status.M { color: var(--orange); }
.file-status.D { color: var(--red); }

.file-path { color: var(--text-primary); flex: 1; }
.file-stat { color: var(--text-secondary); text-align: right; font-size: 10px; }

/* Review */
.review-verdict { font-weight: bold; margin-right: 12px; }
.review-verdict.passed { color: var(--green-light); }
.review-verdict.failed { color: var(--red); }

.review-counts { font-size: 11px; }
.sev-critical { color: var(--red); }
.sev-high { color: var(--orange); }
.sev-medium, .sev-low { color: var(--text-secondary); }

/* Gate */
.gate-output {
    background: var(--bg-primary);
    border-radius: 4px;
    padding: 6px 8px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    color: var(--text-secondary);
    white-space: pre-wrap;
    max-height: 120px;
    overflow-y: auto;
}

/* Metrics table */
.metrics-table {
    width: 100%;
    font-size: 11px;
    border-collapse: collapse;
}

.metrics-table th {
    color: var(--text-secondary);
    font-weight: normal;
    text-align: left;
    padding: 4px 8px;
    border-bottom: 1px solid var(--border);
}

.metrics-table td {
    padding: 4px 8px;
}

.metric-pass { color: var(--green-light); }
.metric-fail { color: var(--red); font-weight: bold; }

/* Blocked reason */
.blocked-banner {
    background: rgba(248, 81, 73, 0.1);
    border: 1px solid var(--red);
    border-radius: 4px;
    padding: 8px 12px;
    margin: 8px 16px;
    color: var(--red);
    font-size: 12px;
}

/* Empty state */
.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-muted);
    font-size: 14px;
}

/* Blink animation for active indicator */
@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
}
.blink { animation: blink 1s infinite; }
```

- [ ] **Step 2: Create JavaScript**

Create `src/shadowcoder/dashboard/static/dashboard.js`:

```javascript
(function () {
    "use strict";

    // Auto-scroll: scroll to bottom unless user has scrolled up
    const logContainer = document.getElementById("log-entries");
    let autoScroll = true;

    if (logContainer) {
        logContainer.addEventListener("scroll", function () {
            const atBottom =
                logContainer.scrollHeight - logContainer.scrollTop - logContainer.clientHeight < 30;
            autoScroll = atBottom;
        });

        // Observe new log entries being added
        const observer = new MutationObserver(function () {
            if (autoScroll) {
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        });
        observer.observe(logContainer, { childList: true, subtree: true });
    }

    // Elapsed time counter
    const elapsedEl = document.getElementById("elapsed-time");
    if (elapsedEl) {
        const created = elapsedEl.dataset.created;
        if (created) {
            const startTime = new Date(created).getTime();
            function updateElapsed() {
                const now = Date.now();
                const diff = Math.floor((now - startTime) / 1000);
                const m = Math.floor(diff / 60);
                const s = diff % 60;
                elapsedEl.textContent = m + "m " + s + "s";
            }
            updateElapsed();
            setInterval(updateElapsed, 1000);
        }
    }

    // SSE reconnect on error
    document.body.addEventListener("htmx:sseError", function () {
        console.log("SSE connection lost, reconnecting in 3s...");
        setTimeout(function () {
            htmx.trigger(document.body, "htmx:load");
        }, 3000);
    });
})();
```

- [ ] **Step 3: Commit**

```bash
git add src/shadowcoder/dashboard/static/
git commit -m "feat(dashboard): add CSS theme and JS (auto-scroll, elapsed timer)"
```

---

### Task 7: Jinja2 Templates

**Files:**
- Create: `src/shadowcoder/dashboard/templates/base.html`
- Create: `src/shadowcoder/dashboard/templates/dashboard.html`
- Create: `src/shadowcoder/dashboard/templates/partials/topbar.html`
- Create: `src/shadowcoder/dashboard/templates/partials/status_bar.html`
- Create: `src/shadowcoder/dashboard/templates/partials/pipeline.html`
- Create: `src/shadowcoder/dashboard/templates/partials/log.html`
- Create: `src/shadowcoder/dashboard/templates/partials/files.html`
- Create: `src/shadowcoder/dashboard/templates/partials/review.html`
- Create: `src/shadowcoder/dashboard/templates/partials/gate.html`
- Create: `src/shadowcoder/dashboard/templates/partials/metrics.html`

- [ ] **Step 1: Create base.html**

Create `src/shadowcoder/dashboard/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ShadowCoder Dashboard</title>
    <link rel="stylesheet" href="/static/style.css">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://unpkg.com/htmx-ext-sse@2.2.2/sse.js"></script>
</head>
<body>
    {% block content %}{% endblock %}
    <script src="/static/dashboard.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create dashboard.html**

Create `src/shadowcoder/dashboard/templates/dashboard.html`:

```html
{% extends "base.html" %}

{% block content %}
{% include "partials/topbar.html" %}

{% if issue %}
<div hx-ext="sse" sse-connect="/sse/issue/{{ issue.id }}">
    <div id="status-bar" sse-swap="status">
        {% include "partials/status_bar.html" %}
    </div>

    <div id="pipeline" sse-swap="pipeline">
        {% include "partials/pipeline.html" %}
    </div>

    {% if issue.blocked_reason %}
    <div class="blocked-banner">
        BLOCKED: {{ issue.blocked_reason }}
    </div>
    {% endif %}

    <div class="main-content">
        <div class="log-panel">
            <div class="panel-header">LIVE ACTIVITY</div>
            <div class="log-entries" id="log-entries">
                {% for entry in issue.log_entries %}
                {% include "partials/log.html" %}
                {% endfor %}
            </div>
            {# New log entries appended here via SSE #}
            <div id="log-append" hx-swap-oob="beforeend:#log-entries" sse-swap="log"></div>
        </div>

        <div class="right-panels">
            <div class="right-panel files">
                <div id="files-panel" sse-swap="files">
                    {% include "partials/files.html" %}
                </div>
            </div>

            <div class="right-panel review">
                <div id="review-panel" sse-swap="review">
                    {% include "partials/review.html" %}
                </div>
            </div>

            <div class="right-panel gate">
                <div id="gate-panel" sse-swap="gate">
                    {% include "partials/gate.html" %}
                </div>
            </div>
        </div>
    </div>
</div>
{% else %}
<div class="empty-state">
    No issues found. Start a task with <code>python scripts/run_real.py &lt;repo&gt; run</code>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Create partials/topbar.html**

Create `src/shadowcoder/dashboard/templates/partials/topbar.html`:

```html
<div class="topbar">
    <span class="topbar-brand">ShadowCoder</span>
    <span class="topbar-repo">{{ repo_path }}</span>
    <span class="topbar-spacer"></span>
    <select onchange="window.location.href='/?issue=' + this.value">
        {% for i in issues %}
        <option value="{{ i.id }}" {% if issue and i.id == issue.id %}selected{% endif %}>
            #{{ i.id }} {{ i.title }} ({{ i.status | upper }})
        </option>
        {% endfor %}
    </select>
</div>
```

- [ ] **Step 4: Create partials/status_bar.html**

Create `src/shadowcoder/dashboard/templates/partials/status_bar.html`:

```html
<div class="status-bar">
    <span class="status-badge {{ issue.status }}">{{ issue.status | upper }}</span>
    {% if issue.assignee %}
    <span class="status-sep">·</span>
    <span class="status-agent">{{ issue.assignee }}</span>
    {% endif %}
    <span class="status-sep">·</span>
    <span class="status-cost">{{ issue.cost }}</span>
    <span class="status-sep">·</span>
    <span class="status-time" id="elapsed-time" data-created="{{ issue.created }}">--</span>
</div>
```

- [ ] **Step 5: Create partials/pipeline.html**

Create `src/shadowcoder/dashboard/templates/partials/pipeline.html`:

```html
<div class="pipeline">
    <div class="pipeline-bar">
        {% for stage in issue.pipeline %}
        <div class="pipeline-segment {% if stage.name == 'Develop' %}develop{% endif %}">
            <div class="pipeline-label {{ stage.state }}">
                {% if stage.state == 'completed' %}{{ stage.name }} ✓
                {% elif stage.state == 'active' %}▼ {{ stage.name }}
                {% elif stage.state == 'failed' %}{{ stage.name }} ✗
                {% else %}{{ stage.name }}
                {% endif %}
            </div>
            <div class="pipeline-fill {{ stage.state }}"></div>
        </div>
        {% endfor %}
    </div>

    {% if issue.retries %}
    <div class="retry-timeline">
        {% for r in issue.retries %}
        {% if not loop.first %}
        <span class="retry-arrow">→</span>
        {% endif %}
        <div class="retry-item {{ r.result }}">
            <span class="retry-round {{ r.result }}">{{ r.round }}</span>
            <span>
                {% if r.result == 'fail' %}✗ {{ r.summary }}
                {% elif r.result == 'pass' %}✓
                {% elif r.result == 'reverted' %}⏪ reverted
                {% else %}●
                {% endif %}
            </span>
        </div>
        {% endfor %}
    </div>
    {% endif %}
</div>
```

- [ ] **Step 6: Create partials/log.html**

Create `src/shadowcoder/dashboard/templates/partials/log.html`:

```html
<div class="log-entry">
    <span class="log-ts">{{ entry.timestamp }}</span>
    <span class="log-text {{ entry.category }}">{{ entry.text }}</span>
</div>
{% for line in entry.continuation %}
<div class="log-continuation">{{ line }}</div>
{% endfor %}
```

- [ ] **Step 7: Create partials/files.html**

Create `src/shadowcoder/dashboard/templates/partials/files.html`:

```html
<div class="panel-header">CHANGED FILES <span class="count">{{ issue.changed_files | length }}</span></div>
<div class="panel-body">
    {% if issue.changed_files %}
    {% for f in issue.changed_files %}
    <div class="file-item">
        <span class="file-status {{ f.status }}">{{ f.status }}</span>
        <span class="file-path">{{ f.path }}</span>
        <span class="file-stat">{{ f.stat }}</span>
    </div>
    {% endfor %}
    {% else %}
    <div style="color: var(--text-muted); font-size: 11px;">No changes yet</div>
    {% endif %}
</div>
```

- [ ] **Step 8: Create partials/review.html**

Create `src/shadowcoder/dashboard/templates/partials/review.html`:

```html
<div class="panel-header">REVIEW SUMMARY</div>
<div class="panel-body">
    {% if issue.feedback.verdict %}
    <span class="review-verdict {% if issue.feedback.passed %}passed{% else %}failed{% endif %}">
        {{ issue.feedback.verdict }}
    </span>
    {% endif %}
    <span class="review-counts">
        <span class="sev-critical">C:{{ issue.feedback.critical }}</span>
        <span class="sev-high">H:{{ issue.feedback.high }}</span>
        <span class="sev-medium">M:{{ issue.feedback.medium }}</span>
        <span class="sev-low">L:{{ issue.feedback.low }}</span>
    </span>
</div>
```

- [ ] **Step 9: Create partials/gate.html**

Create `src/shadowcoder/dashboard/templates/partials/gate.html`:

```html
<div class="panel-header">GATE OUTPUT</div>
<div class="panel-body">
    {% if issue.gate_output %}
    <div class="gate-output">{{ issue.gate_output }}</div>
    {% else %}
    <div style="color: var(--text-muted); font-size: 11px;">No gate output yet</div>
    {% endif %}
</div>
```

- [ ] **Step 10: Create partials/metrics.html**

Create `src/shadowcoder/dashboard/templates/partials/metrics.html`:

```html
<div class="panel-header">METRIC GATE</div>
<div class="panel-body">
    {% if metrics %}
    <table class="metrics-table">
        <tr><th>Metric</th><th>Required</th><th>Actual</th><th>Status</th></tr>
        {% for m in metrics %}
        <tr>
            <td>{{ m.name }}</td>
            <td style="color: var(--text-secondary);">{{ m.threshold }}</td>
            <td class="{% if m.passed %}metric-pass{% else %}metric-fail{% endif %}">{{ m.value }}</td>
            <td class="{% if m.passed %}metric-pass{% else %}metric-fail{% endif %}">
                {% if m.passed %}✓ PASS{% else %}✗ FAIL{% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div style="color: var(--text-muted); font-size: 11px;">No metric gate configured</div>
    {% endif %}
</div>
```

- [ ] **Step 11: Commit**

```bash
git add src/shadowcoder/dashboard/templates/
git commit -m "feat(dashboard): add Jinja2 templates (base, dashboard, all partials)"
```

---

### Task 8: FastAPI Server with SSE

**Files:**
- Create: `src/shadowcoder/dashboard/server.py`
- Create: `tests/dashboard/test_server.py`

- [ ] **Step 1: Write tests for the server**

Create `tests/dashboard/test_server.py`:

```python
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from shadowcoder.dashboard.server import create_app


def _make_repo(tmp_path: Path) -> str:
    """Create a minimal repo structure for testing."""
    issues_dir = tmp_path / ".shadowcoder" / "issues" / "0001"
    issues_dir.mkdir(parents=True)

    (issues_dir / "issue.md").write_text(
        '---\n'
        'id: 1\n'
        'title: "Test Issue"\n'
        'status: "developing"\n'
        'priority: "medium"\n'
        'created: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T11:00:00"\n'
        'tags: []\n'
        '---\n'
    )
    (issues_dir / "issue.log").write_text(
        "[2026-04-01 10:00:00] Issue 创建: Test Issue\n"
        "[2026-04-01 10:05:00] Design R1 开始\n"
    )
    (issues_dir / "feedback.json").write_text(json.dumps({
        "items": [], "proposed_tests": [],
        "acceptance_tests": [], "supplementary_tests": [],
    }))

    wt = tmp_path / ".shadowcoder" / "worktrees" / "issue-1"
    wt.mkdir(parents=True)

    return str(tmp_path)


@pytest.mark.asyncio
async def test_index_returns_dashboard(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "ShadowCoder" in resp.text
        assert "Test Issue" in resp.text


@pytest.mark.asyncio
async def test_index_with_issue_param(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/?issue=1")
        assert resp.status_code == 200
        assert "Test Issue" in resp.text


@pytest.mark.asyncio
async def test_api_issues(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Issue"


@pytest.mark.asyncio
async def test_api_issue_detail(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Issue"
        assert data["status"] == "developing"


@pytest.mark.asyncio
async def test_api_issue_not_found(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues/99")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_no_issues(tmp_path):
    (tmp_path / ".shadowcoder" / "issues").mkdir(parents=True)
    app = create_app(str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "No issues found" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/dashboard/test_server.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the server**

Create `src/shadowcoder/dashboard/server.py`:

```python
"""FastAPI dashboard server with SSE support."""
from __future__ import annotations

import asyncio
import json
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from sse_starlette.sse import EventSourceResponse

from shadowcoder.dashboard.state import DashboardState
from shadowcoder.dashboard.watcher import FileWatcher
from shadowcoder.dashboard.parsers import LogParser

_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"


def create_app(repo_path: str) -> FastAPI:
    app = FastAPI(title="ShadowCoder Dashboard")
    state = DashboardState(repo_path)

    # Jinja2 environment
    jinja_env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # SSE subscribers: {issue_id: [asyncio.Queue]}
    subscribers: dict[int, list[asyncio.Queue]] = {}

    async def broadcast(issue_id: int, event_type: str, data: str) -> None:
        for q in subscribers.get(issue_id, []):
            await q.put({"event": event_type, "data": data})
        # Also broadcast to the "all" channel for issue list updates
        for q in subscribers.get(0, []):
            await q.put({"event": "issues", "data": data})

    # File watcher callback
    async def on_file_change(event: dict) -> None:
        issue_id = event["issue_id"]
        file_type = event["file_type"]
        detail = state.get_issue_detail(issue_id)
        if detail is None:
            return

        if file_type == "log":
            # Re-render log partial with new entries
            template = jinja_env.get_template("partials/log.html")
            if detail["log_entries"]:
                entry = detail["log_entries"][-1]
                html = template.render(entry=entry)
                await broadcast(issue_id, "log", html)

        if file_type == "issue":
            # Re-render status bar and pipeline
            sb_template = jinja_env.get_template("partials/status_bar.html")
            await broadcast(issue_id, "status", sb_template.render(issue=detail))
            pl_template = jinja_env.get_template("partials/pipeline.html")
            await broadcast(issue_id, "pipeline", pl_template.render(issue=detail))

        if file_type == "feedback":
            rv_template = jinja_env.get_template("partials/review.html")
            await broadcast(issue_id, "review", rv_template.render(issue=detail))

        if file_type == "metrics":
            gt_template = jinja_env.get_template("partials/gate.html")
            await broadcast(issue_id, "gate", gt_template.render(issue=detail))

        # Always refresh files on any change
        files_template = jinja_env.get_template("partials/files.html")
        await broadcast(issue_id, "files", files_template.render(issue=detail))

    # Start watcher on app startup
    watcher = FileWatcher(repo_path, on_file_change)

    @app.on_event("startup")
    async def startup():
        watcher.start()

    @app.on_event("shutdown")
    async def shutdown():
        watcher.stop()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, issue: int | None = None):
        issues = state.get_issue_list()
        template = jinja_env.get_template("dashboard.html")

        # Select issue to display
        issue_detail = None
        if issue is not None:
            issue_detail = state.get_issue_detail(issue)
        elif issues:
            # Default: most recently updated issue
            latest = max(issues, key=lambda i: i.get("updated", ""))
            issue_detail = state.get_issue_detail(latest["id"])

        return HTMLResponse(template.render(
            repo_path=repo_path,
            issues=issues,
            issue=issue_detail,
        ))

    @app.get("/api/issues")
    async def api_issues():
        return JSONResponse(state.get_issue_list())

    @app.get("/api/issues/{issue_id}")
    async def api_issue_detail(issue_id: int):
        detail = state.get_issue_detail(issue_id)
        if detail is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Serialize dataclasses to dicts for JSON
        result = {**detail}
        result["log_entries"] = [
            {"timestamp": e.timestamp, "text": e.text, "category": e.category,
             "continuation": e.continuation}
            for e in detail["log_entries"]
        ]
        result["pipeline"] = [
            {"name": s.name, "state": s.state}
            for s in detail["pipeline"]
        ]
        result["changed_files"] = [
            {"status": f.status, "path": f.path, "stat": f.stat}
            for f in detail["changed_files"]
        ]
        result["feedback"] = {
            "verdict": detail["feedback"].verdict,
            "critical": detail["feedback"].critical,
            "high": detail["feedback"].high,
            "medium": detail["feedback"].medium,
            "low": detail["feedback"].low,
            "total": detail["feedback"].total,
            "passed": detail["feedback"].passed,
        }
        return JSONResponse(result)

    @app.get("/sse/issue/{issue_id}")
    async def sse_issue(request: Request, issue_id: int):
        queue: asyncio.Queue = asyncio.Queue()
        if issue_id not in subscribers:
            subscribers[issue_id] = []
        subscribers[issue_id].append(queue)

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield event
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": ""}
            finally:
                subscribers[issue_id].remove(queue)
                if not subscribers[issue_id]:
                    del subscribers[issue_id]

        return EventSourceResponse(event_generator())

    return app


def run_server(repo_path: str, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Entry point: create app and run with uvicorn."""
    import uvicorn

    app = create_app(repo_path)
    print(f"Dashboard: http://{host}:{port}")
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
```

- [ ] **Step 4: Add sse-starlette to dependencies**

Update the `dashboard` extras in `pyproject.toml`:

```toml
dashboard = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "watchdog>=6.0",
    "jinja2>=3.1",
    "sse-starlette>=2.0",
    "httpx>=0.27",
]
```

Run: `pip install -e ".[dev,dashboard]"`

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/dashboard/test_server.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/dashboard/server.py tests/dashboard/test_server.py pyproject.toml
git commit -m "feat(dashboard): add FastAPI server with SSE and REST endpoints"
```

---

### Task 9: CLI Integration

**Files:**
- Modify: `scripts/run_real.py`

- [ ] **Step 1: Add dashboard subcommand to run_real.py**

In `scripts/run_real.py`, add the dashboard command handler in the command dispatch section (before the `else: Unknown command` block, around line 269):

```python
    elif command == "dashboard":
        port = 8420
        host = "127.0.0.1"
        i = 0
        while i < len(args):
            if args[i] == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
                i += 2
            elif args[i] == "--host" and i + 1 < len(args):
                host = args[i + 1]
                i += 2
            else:
                i += 1
        from shadowcoder.dashboard.server import run_server
        run_server(repo_path, host=host, port=port)
        return
```

Also add the `dashboard` command to the module docstring at the top of the file:

```
  python scripts/run_real.py ~/lab/coder-playground dashboard
  python scripts/run_real.py ~/lab/coder-playground dashboard --port 9000
```

- [ ] **Step 2: Test the CLI manually**

Run: `python scripts/run_real.py /tmp/test-repo dashboard --port 8421`
Expected: Server starts, browser opens to `http://127.0.0.1:8421`, shows "No issues found" (empty repo).
Press Ctrl+C to stop.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_real.py
git commit -m "feat(dashboard): add 'dashboard' subcommand to run_real.py"
```

---

### Task 10: Integration Test with Real Issue Data

**Files:**
- Create: `tests/dashboard/test_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/dashboard/test_integration.py`:

```python
"""Integration test: full dashboard render with realistic issue data."""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from shadowcoder.dashboard.server import create_app


def _make_full_repo(tmp_path: Path) -> str:
    """Create a repo with a realistic issue in DEVELOPING state with gate retries."""
    d = tmp_path / ".shadowcoder" / "issues" / "0001"
    d.mkdir(parents=True)

    (d / "issue.md").write_text(
        '---\n'
        'id: 1\n'
        'title: "Add user authentication"\n'
        'status: "developing"\n'
        'priority: "high"\n'
        'created: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T10:50:00"\n'
        'tags: ["auth"]\n'
        'assignee: "fast-coder"\n'
        '---\n'
        '<!-- section: 需求 -->\n'
        'Add JWT-based authentication\n'
        '<!-- /section -->\n'
    )

    (d / "issue.log").write_text(
        "[2026-04-01 10:00:00] Issue 创建: Add user authentication\n"
        "[2026-04-01 10:01:00] Preflight 评估\n"
        "Feasibility: high | Complexity: moderate | Risks: none\n"
        "[2026-04-01 10:02:00] Design R1 开始\n"
        "[2026-04-01 10:05:00] Usage: 1200+800 tokens, $0.08\n"
        "[2026-04-01 10:05:00] Design Review\n"
        "PASSED (CRITICAL=0, HIGH=0, 3 comments total)\n"
        "[2026-04-01 10:10:00] Develop R1 开始 (fast-coder)\n"
        "[2026-04-01 10:20:00] Usage: 5000+3000 tokens, $0.32\n"
        "[2026-04-01 10:20:00] Gate FAIL R1: 3 tests failed\n"
        "  AssertionError: expected 200 got 401 in test_login\n"
        "[2026-04-01 10:25:00] Develop R2 开始 (fast-coder)\n"
        "[2026-04-01 10:35:00] Usage: 4000+2500 tokens, $0.26\n"
        "[2026-04-01 10:35:00] Gate PASS R2\n"
        "[2026-04-01 10:40:00] Dev Review 开始 (claude-coder)\n"
    )

    (d / "feedback.json").write_text(json.dumps({
        "items": [
            {"id": "1", "category": "bug", "description": "Missing CSRF protection",
             "severity": "HIGH", "resolved": False, "round_introduced": 1,
             "times_raised": 1, "escalation_level": 0},
            {"id": "2", "category": "style", "description": "Inconsistent naming",
             "severity": "MEDIUM", "resolved": True, "round_introduced": 1,
             "times_raised": 1, "escalation_level": 0},
            {"id": "3", "category": "performance", "description": "Token expiry check",
             "severity": "LOW", "resolved": False, "round_introduced": 1,
             "times_raised": 1, "escalation_level": 0},
        ],
        "proposed_tests": [
            {"name": "test_login", "passed": True},
            {"name": "test_register", "passed": True},
        ],
        "acceptance_tests": [],
        "supplementary_tests": [],
        "verdict": "NOT PASSED",
    }))

    wt = tmp_path / ".shadowcoder" / "worktrees" / "issue-1"
    wt.mkdir(parents=True)

    return str(tmp_path)


@pytest.mark.asyncio
async def test_full_dashboard_render(tmp_path):
    repo = _make_full_repo(tmp_path)
    app = create_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    html = resp.text

    # Top bar
    assert "ShadowCoder" in html
    assert "Add user authentication" in html

    # Status
    assert "DEVELOPING" in html
    assert "fast-coder" in html

    # Pipeline stages
    assert "Design" in html
    assert "Develop" in html
    assert "Gate" in html

    # Log entries
    assert "Issue 创建" in html
    assert "Gate FAIL R1" in html
    assert "Gate PASS R2" in html

    # Review
    assert "NOT PASSED" in html


@pytest.mark.asyncio
async def test_api_detail_has_retries(tmp_path):
    repo = _make_full_repo(tmp_path)
    app = create_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues/1")

    data = resp.json()
    assert len(data["retries"]) == 2
    assert data["retries"][0]["round"] == "R1"
    assert data["retries"][0]["result"] == "fail"
    assert data["retries"][1]["round"] == "R2"
    assert data["retries"][1]["result"] == "pass"


@pytest.mark.asyncio
async def test_blocked_issue_shows_reason(tmp_path):
    d = tmp_path / ".shadowcoder" / "issues" / "0001"
    d.mkdir(parents=True)

    (d / "issue.md").write_text(
        '---\n'
        'id: 1\n'
        'title: "Blocked task"\n'
        'status: "blocked"\n'
        'priority: "medium"\n'
        'created: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T11:00:00"\n'
        'tags: []\n'
        'blocked_reason: "BLOCKED_MAX_ROUNDS"\n'
        '---\n'
    )
    (d / "issue.log").write_text("[2026-04-01 10:00:00] init\n")
    (d / "feedback.json").write_text(json.dumps({
        "items": [], "proposed_tests": [],
        "acceptance_tests": [], "supplementary_tests": [],
    }))

    app = create_app(str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")

    assert "BLOCKED_MAX_ROUNDS" in resp.text
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/dashboard/test_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/dashboard/test_integration.py
git commit -m "test(dashboard): add integration tests with realistic issue data"
```

---

### Task 11: Final Verification

- [ ] **Step 1: Run all dashboard tests**

Run: `python -m pytest tests/dashboard/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run full test suite to check for regressions**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still PASS

- [ ] **Step 3: Manual smoke test**

If a repo with existing issues is available (e.g. the shadowcoder project itself or a test repo):

Run: `python scripts/run_real.py <repo-with-issues> dashboard`

Verify:
- Browser opens automatically
- Issue selector shows all issues
- Pipeline progress bar renders correctly
- Log entries are visible
- Switching issues works

- [ ] **Step 4: Final commit**

If any fixes were needed during verification, commit them:

```bash
git add -A
git commit -m "fix(dashboard): adjustments from smoke testing"
```
