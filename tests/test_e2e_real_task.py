"""
E2E real task: develop a Markdown Link Checker CLI tool in coder-playground.

Simulates a realistic multi-round workflow:
  - Design round 1: reviewer rejects (missing error handling design)
  - Design round 2: agent addresses feedback, reviewer approves
  - Develop round 1: reviewer rejects (code quality issues)
  - Develop round 2: agent addresses feedback, reviewer approves
  - Test: passes

Agent output is realistic multi-section markdown with code blocks, tables,
and nested headers that stress-tests the section parsing.
"""
import subprocess
from pathlib import Path

import frontmatter as fm
import pytest

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import AgentRequest, DesignOutput, DevelopOutput, ReviewOutput, TestOutput, ReviewComment, Severity
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import IssueStatus
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Realistic agent that tracks rounds and produces different content each time
# ---------------------------------------------------------------------------

DESIGN_V1 = """\
## Overview

A CLI tool that scans Markdown files for links (both `[text](url)` and raw URLs),
checks their HTTP status, and reports broken ones.

## Usage

```bash
mdlinkcheck [OPTIONS] <path>
mdlinkcheck README.md
mdlinkcheck docs/ --recursive
```

## Architecture

```
┌─────────┐     ┌──────────┐     ┌───────────┐
│  CLI    │────▶│  Scanner │────▶│  Checker  │
│ (click) │     │ (regex)  │     │ (aiohttp) │
└─────────┘     └──────────┘     └───────────┘
                                       │
                                 ┌─────▼─────┐
                                 │  Reporter  │
                                 └───────────┘
```

### Components

| Component | Responsibility |
|-----------|---------------|
| CLI       | Argument parsing, entry point |
| Scanner   | Parse `.md` files, extract links |
| Checker   | HTTP HEAD requests, status codes |
| Reporter  | Format & display results |

## Tech Stack

- Python 3.12+
- `click` for CLI
- `aiohttp` for async HTTP
- `re` for link extraction

## Data Flow

1. CLI receives path argument
2. Scanner walks files, yields `(file, line, url)` tuples
3. Checker batches URLs, sends async HEAD requests
4. Reporter collects results, prints summary"""

DESIGN_V2 = """\
## Overview

A CLI tool that scans Markdown files for links (both `[text](url)` and raw URLs),
checks their HTTP status, and reports broken ones.

## Usage

```bash
mdlinkcheck [OPTIONS] <path>
mdlinkcheck README.md
mdlinkcheck docs/ --recursive
mdlinkcheck . --timeout 10 --retries 2
```

## Architecture

```
┌─────────┐     ┌──────────┐     ┌───────────┐
│  CLI    │────▶│  Scanner │────▶│  Checker  │
│ (click) │     │ (regex)  │     │ (aiohttp) │
└─────────┘     └──────────┘     └───────────┘
                                       │
                                 ┌─────▼─────┐
                                 │  Reporter  │
                                 └───────────┘
```

### Components

| Component | Responsibility |
|-----------|---------------|
| CLI       | Argument parsing, entry point |
| Scanner   | Parse `.md` files, extract links |
| Checker   | HTTP HEAD requests with retry & timeout |
| Reporter  | Format & display results (text/json) |

## Error Handling

### Network Errors

- **Timeout**: configurable via `--timeout` (default 30s), reported as `TIMEOUT`
- **DNS failure**: reported as `DNS_ERROR` with hostname
- **Connection refused**: reported as `CONN_REFUSED`
- **SSL errors**: reported as `SSL_ERROR`, with `--no-verify` option

### File Errors

- **Permission denied**: skip file, warn to stderr
- **Binary file detected**: skip silently
- **Encoding error**: try UTF-8, fallback Latin-1, else skip with warning

### Rate Limiting

- Group URLs by domain, limit to 5 concurrent per domain
- Respect `Retry-After` header
- Configurable global concurrency via `--concurrency` (default 20)

## Exit Codes

| Code | Meaning |
|------|---------|
| 0    | All links OK |
| 1    | Broken links found |
| 2    | Input/config error |

## Tech Stack

- Python 3.12+
- `click` for CLI
- `aiohttp` for async HTTP
- `re` for link extraction"""

DEVELOP_V1 = """\
## Implementation Summary

Implemented the four core modules:

### scanner.py

```python
import re
from pathlib import Path

LINK_PATTERN = re.compile(r'\\[([^\\]]+)\\]\\(([^)]+)\\)|https?://[^\\s)>]+')

def scan_file(path: Path) -> list[tuple[int, str]]:
    results = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        for match in LINK_PATTERN.finditer(line):
            url = match.group(2) or match.group(0)
            results.append((i, url))
    return results
```

### checker.py

```python
import aiohttp
import asyncio

async def check_url(url: str, timeout: int = 30) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                return {"url": url, "status": resp.status, "ok": resp.status < 400}
    except Exception as e:
        return {"url": url, "status": 0, "ok": False, "error": str(e)}
```

### reporter.py

```python
def format_results(results: list[dict], verbose: bool = False) -> str:
    broken = [r for r in results if not r["ok"]]
    lines = [f"Checked {len(results)} links, {len(broken)} broken"]
    for r in broken:
        lines.append(f"  ✗ {r['url']} - {r.get('error', f'HTTP {r[\"status\"]}')}")
    return "\\n".join(lines)
```

### Files Changed

- `src/mdlinkcheck/scanner.py` (new, 25 lines)
- `src/mdlinkcheck/checker.py` (new, 18 lines)
- `src/mdlinkcheck/reporter.py` (new, 12 lines)
- `src/mdlinkcheck/cli.py` (new, 30 lines)
- `tests/test_scanner.py` (new, 45 lines)
- `tests/test_checker.py` (new, 30 lines)"""

DEVELOP_V2 = """\
## Implementation Summary (v2)

Addressed review feedback: added per-domain concurrency, retry logic,
proper error classification, and comprehensive tests.

### checker.py (updated)

```python
import aiohttp
import asyncio
from collections import defaultdict

class LinkChecker:
    def __init__(self, timeout=30, retries=2, concurrency=20, per_domain=5):
        self.timeout = timeout
        self.retries = retries
        self.concurrency = concurrency
        self.per_domain = per_domain
        self._domain_semas: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_domain))

    async def check(self, url: str) -> dict:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        async with self._domain_semas[domain]:
            for attempt in range(1, self.retries + 1):
                try:
                    async with aiohttp.ClientSession() as session:
                        ct = aiohttp.ClientTimeout(total=self.timeout)
                        async with session.head(url, timeout=ct,
                                                allow_redirects=True) as resp:
                            return {"url": url, "status": resp.status,
                                    "ok": resp.status < 400}
                except asyncio.TimeoutError:
                    if attempt == self.retries:
                        return {"url": url, "status": 0, "ok": False,
                                "error": "TIMEOUT"}
                except aiohttp.ClientConnectorError as e:
                    if attempt == self.retries:
                        error_type = "DNS_ERROR" if "getaddrinfo" in str(e) \\
                            else "CONN_REFUSED"
                        return {"url": url, "status": 0, "ok": False,
                                "error": error_type}
                except aiohttp.ClientSSLError:
                    return {"url": url, "status": 0, "ok": False,
                            "error": "SSL_ERROR"}
                except Exception as e:
                    if attempt == self.retries:
                        return {"url": url, "status": 0, "ok": False,
                                "error": str(e)}
```

### scanner.py (updated)

```python
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

LINK_PATTERN = re.compile(r'\\[([^\\]]+)\\]\\(([^)]+)\\)|https?://[^\\s)>]+')

def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except Exception:
            logger.warning("Cannot read %s, skipping", path)
            return []
    except PermissionError:
        logger.warning("Permission denied: %s, skipping", path)
        return []

    results = []
    for i, line in enumerate(text.splitlines(), 1):
        for match in LINK_PATTERN.finditer(line):
            url = match.group(2) or match.group(0)
            if url.startswith(("http://", "https://")):
                results.append((i, url))
    return results
```

### Files Changed

- `src/mdlinkcheck/checker.py` (rewritten, 55 lines)
- `src/mdlinkcheck/scanner.py` (updated, 30 lines)
- `tests/test_checker.py` (expanded, 65 lines)
- `tests/test_scanner.py` (expanded, 55 lines)"""

TEST_RESULTS = """\
## Test Execution

```
$ pytest tests/ -v --tb=short
========================= test session starts =========================
tests/test_scanner.py::test_scan_markdown_link .............. PASSED
tests/test_scanner.py::test_scan_raw_url ................... PASSED
tests/test_scanner.py::test_scan_multiple_links_per_line ... PASSED
tests/test_scanner.py::test_scan_ignores_non_http .......... PASSED
tests/test_scanner.py::test_scan_permission_denied ......... PASSED
tests/test_scanner.py::test_scan_encoding_fallback ......... PASSED
tests/test_checker.py::test_check_200 ...................... PASSED
tests/test_checker.py::test_check_404 ...................... PASSED
tests/test_checker.py::test_check_timeout .................. PASSED
tests/test_checker.py::test_check_dns_error ................ PASSED
tests/test_checker.py::test_check_ssl_error ................ PASSED
tests/test_checker.py::test_check_retry_then_success ....... PASSED
tests/test_checker.py::test_per_domain_concurrency ......... PASSED
tests/test_reporter.py::test_format_all_ok ................. PASSED
tests/test_reporter.py::test_format_broken ................. PASSED
tests/test_cli.py::test_cli_help ........................... PASSED
tests/test_cli.py::test_cli_file ........................... PASSED
tests/test_cli.py::test_cli_exit_code_1 ................... PASSED
========================= 18 passed in 2.3s ==========================
```

## Coverage

| Module | Stmts | Miss | Cover |
|--------|-------|------|-------|
| scanner.py | 30 | 2 | 93% |
| checker.py | 55 | 5 | 91% |
| reporter.py | 12 | 0 | 100% |
| cli.py | 30 | 3 | 90% |
| **TOTAL** | **127** | **10** | **92%** |

## Summary

18/18 tests passed, 92% code coverage. All error handling paths tested."""


REVIEW_DESIGN_REJECT = ReviewOutput(
    passed=False,
    comments=[
        ReviewComment(
            severity=Severity.HIGH,
            message="Missing error handling design. The checker has no timeout, "
                    "retry, or rate limiting strategy. What happens when a server "
                    "is slow or returns 429? This needs to be addressed before "
                    "implementation.",
            location="Architecture > Checker",
        ),
        ReviewComment(
            severity=Severity.HIGH,
            message="No exit code specification. CLI tools need well-defined exit "
                    "codes so they can be used in CI pipelines and scripts.",
        ),
        ReviewComment(
            severity=Severity.MEDIUM,
            message="Scanner should handle encoding issues gracefully. Not all "
                    ".md files are UTF-8.",
            location="Architecture > Scanner",
        ),
        ReviewComment(
            severity=Severity.LOW,
            message="Consider supporting JSON output format for programmatic "
                    "consumption.",
            location="Architecture > Reporter",
        ),
    ],
    reviewer="design-reviewer",
)

REVIEW_DESIGN_APPROVE = ReviewOutput(
    passed=True,
    comments=[
        ReviewComment(
            severity=Severity.LOW,
            message="Good error handling design. Consider also documenting the "
                    "behavior when encountering redirect loops.",
        ),
        ReviewComment(
            severity=Severity.LOW,
            message="Exit codes well defined. Follows Unix conventions.",
        ),
    ],
    reviewer="design-reviewer",
)

REVIEW_DEVELOP_REJECT = ReviewOutput(
    passed=False,
    comments=[
        ReviewComment(
            severity=Severity.HIGH,
            message="checker.py creates a new ClientSession per URL check. This is "
                    "extremely inefficient — sessions should be reused. Refactor to "
                    "use a single session with per-domain semaphores.",
            location="checker.py:check_url",
        ),
        ReviewComment(
            severity=Severity.HIGH,
            message="No retry logic implemented despite it being in the design spec. "
                    "The design specifies configurable retries with --retries flag.",
            location="checker.py",
        ),
        ReviewComment(
            severity=Severity.MEDIUM,
            message="scanner.py doesn't handle encoding errors as specified in design. "
                    "Missing UTF-8 → Latin-1 fallback.",
            location="scanner.py:scan_file",
        ),
    ],
    reviewer="code-reviewer",
)

REVIEW_DEVELOP_APPROVE = ReviewOutput(
    passed=True,
    comments=[
        ReviewComment(
            severity=Severity.LOW,
            message="Good improvement. The LinkChecker class properly manages "
                    "session lifecycle and per-domain concurrency.",
        ),
        ReviewComment(
            severity=Severity.LOW,
            message="Error classification (TIMEOUT, DNS_ERROR, CONN_REFUSED, "
                    "SSL_ERROR) matches the design spec.",
        ),
    ],
    reviewer="code-reviewer",
)


class RealisticAgent(BaseAgent):
    """Agent that produces realistic, round-aware content."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._design_round = 0
        self._develop_round = 0
        self._review_design_round = 0
        self._review_develop_round = 0

    async def design(self, request: AgentRequest) -> DesignOutput:
        self._design_round += 1
        document = DESIGN_V1 if self._design_round == 1 else DESIGN_V2
        return DesignOutput(document=document)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        self._develop_round += 1
        summary = DEVELOP_V1 if self._develop_round == 1 else DEVELOP_V2
        return DevelopOutput(summary=summary)

    async def test(self, request: AgentRequest) -> TestOutput:
        return TestOutput(report=TEST_RESULTS, success=True, passed_count=18, total_count=18)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        # Infer which stage we're reviewing from issue sections
        sections = request.issue.sections
        if "开发步骤" in sections:
            self._review_develop_round += 1
            if self._review_develop_round == 1:
                return REVIEW_DEVELOP_REJECT
            return REVIEW_DEVELOP_APPROVE
        else:
            self._review_design_round += 1
            if self._review_design_round == 1:
                return REVIEW_DESIGN_REJECT
            return REVIEW_DESIGN_APPROVE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def playground(tmp_path):
    """Create a realistic project repo."""
    repo = tmp_path / "coder-playground"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)

    # Add initial project files
    (repo / "README.md").write_text(
        "# coder-playground\n\nExperimental repo for testing shadowcoder.\n"
    )
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n.shadowcoder/worktrees/\n")

    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial project setup"],
                   cwd=str(repo), check=True, capture_output=True)
    return repo


@pytest.fixture
def system(playground, tmp_path):
    """Wire up the full shadowcoder system against the playground repo."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""\
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code

reviewers:
  design: [design-reviewer]
  develop: [code-reviewer]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 3

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
""")

    config = Config(str(config_path))
    agent = RealisticAgent({"type": "claude_code"})

    bus = MessageBus()
    wt_manager = WorktreeManager(config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    issue_store = IssueStore(str(playground), config)
    registry = AgentRegistry(config)
    # Register agent under both names (executor + reviewers)
    registry._instances["claude-code"] = agent
    registry._instances["design-reviewer"] = agent
    registry._instances["code-reviewer"] = agent

    engine = Engine(bus, issue_store, task_manager, registry, config, str(playground))

    # Collect all events for verification
    collected = {mt: [] for mt in MessageType}
    for mt in MessageType:
        async def _h(msg, _mt=mt):
            collected[_mt].append(msg)
        bus.subscribe(mt, _h)

    return {
        "bus": bus,
        "store": issue_store,
        "agent": agent,
        "repo": playground,
        "config": config,
        "events": collected,
    }


# ---------------------------------------------------------------------------
# The Test
# ---------------------------------------------------------------------------

async def test_real_task_full_lifecycle(system):
    """
    Real task: build a Markdown Link Checker CLI tool.

    Exercises:
    - Multi-round design with rejection and revision
    - Multi-round develop with rejection and revision
    - Realistic markdown content with code blocks, tables, ASCII art
    - Section overwrite (design v1 → v2) and review accumulation
    - Different reviewers for design vs develop stages
    - Worktree creation for each stage
    - Full file integrity at every checkpoint
    """
    bus = system["bus"]
    store = system["store"]
    repo = system["repo"]
    agent = system["agent"]
    events = system["events"]

    # ===== CREATE =====
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "Implement Markdown Link Checker CLI",
        "priority": "high",
        "tags": ["feature", "cli", "tooling"],
    }))

    issue = store.get(1)
    assert issue.status == IssueStatus.CREATED
    assert issue.title == "Implement Markdown Link Checker CLI"

    issue_file = repo / ".shadowcoder" / "issues" / "0001.md"
    assert issue_file.exists()

    # ===== DESIGN (2 rounds: reject → approve) =====
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.APPROVED, \
        f"Expected APPROVED, got {issue.status.value}"

    # -- Verify agent was called twice for design --
    assert agent._design_round == 2, \
        f"Expected 2 design rounds, got {agent._design_round}"

    # -- Verify design content is v2 (v1 was overwritten) --
    design_content = issue.sections["设计"]
    assert "Error Handling" in design_content, \
        "Design v2 should include error handling section"
    assert "--timeout" in design_content, \
        "Design v2 should include timeout flag"
    assert "Exit Codes" in design_content, \
        "Design v2 should include exit codes"

    # -- Verify review section has summary (latest round only) --
    review_content = issue.sections["Design Review"]
    assert "PASSED" in review_content, \
        "Should contain approval summary"

    # -- Verify log has BOTH rounds (accumulated) --
    log = store.get_log(1)
    assert "NOT PASSED" in log, \
        "Log should contain first round rejection"
    assert "Missing error handling" in log, \
        "Log should contain specific feedback from round 1"
    assert "design-reviewer" in log

    # -- Verify code blocks survived roundtrip --
    assert "```bash" in design_content or "```" in design_content, \
        "Code blocks should survive markdown roundtrip"

    # -- Verify table survived roundtrip --
    assert "| Component |" in design_content, \
        "Tables should survive markdown roundtrip"

    # -- Verify worktree was created --
    wt_path = repo / ".shadowcoder" / "worktrees" / "issue-1"
    assert wt_path.exists(), "Worktree should exist after design"

    # -- File integrity check --
    raw = issue_file.read_text()
    post = fm.load(str(issue_file))
    assert post["status"] == "approved"
    assert post["priority"] == "high"
    assert post["tags"] == ["feature", "cli", "tooling"]

    # ===== DEVELOP (2 rounds: reject → approve) =====
    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.TESTING, \
        f"Expected TESTING, got {issue.status.value}"

    # -- Verify agent was called twice for develop --
    assert agent._develop_round == 2, \
        f"Expected 2 develop rounds, got {agent._develop_round}"

    # -- Verify develop content is v2 --
    develop_content = issue.sections["开发步骤"]
    assert "LinkChecker" in develop_content, \
        "Develop v2 should have LinkChecker class"
    assert "per_domain" in develop_content, \
        "Develop v2 should have per-domain concurrency"
    assert "self.retries" in develop_content, \
        "Develop v2 should have retry logic"

    # -- Verify v1 content is gone (overwritten) --
    assert "async def check_url" not in develop_content, \
        "Develop v1 standalone function should be overwritten by v2 class"

    # -- Verify dev review summary is in .md --
    dev_review = issue.sections["Dev Review"]
    assert "PASSED" in dev_review

    # -- Verify dev review details are in log --
    log = store.get_log(1)
    assert "NOT PASSED" in log
    assert "ClientSession per URL" in log, \
        "Log should contain specific v1 feedback"
    assert "code-reviewer" in log

    # ===== TEST =====
    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE, \
        f"Expected DONE, got {issue.status.value}"

    # -- Verify test content --
    test_content = issue.sections["测试"]
    assert "18 passed" in test_content
    assert "92%" in test_content
    assert "scanner.py" in test_content

    # ===== FINAL VERIFICATION =====

    # -- All sections present (航海日志 is now in .log.md, not .md) --
    expected_sections = {"设计", "Design Review", "开发步骤", "Dev Review", "测试"}
    assert expected_sections == set(issue.sections.keys()), \
        f"Missing sections: {expected_sections - set(issue.sections.keys())}"

    # -- Log file has content --
    log = store.get_log(1)
    assert len(log) > 0

    # -- Final file on disk is correct --
    post = fm.load(str(issue_file))
    assert post["status"] == "done"
    assert post["id"] == 1

    # -- Event counts --
    assert len(events[MessageType.EVT_ISSUE_CREATED]) == 1
    assert len(events[MessageType.EVT_TASK_COMPLETED]) == 3  # design, develop, test

    # -- Review events: 2 design + 2 develop = 4 --
    review_evts = events[MessageType.EVT_REVIEW_RESULT]
    assert len(review_evts) == 4, \
        f"Expected 4 review events, got {len(review_evts)}"
    assert not review_evts[0].payload["passed"]  # design round 1 reject
    assert review_evts[1].payload["passed"]       # design round 2 approve
    assert not review_evts[2].payload["passed"]   # develop round 1 reject
    assert review_evts[3].payload["passed"]        # develop round 2 approve

    # -- Status change events --
    # Engine publishes EVT_STATUS_CHANGED for executing phases only
    # (DESIGNING round 1, DESIGNING round 2, DEVELOPING round 1, DEVELOPING round 2)
    # Review/success/done transitions happen via IssueStore but don't emit events
    status_evts = events[MessageType.EVT_STATUS_CHANGED]
    statuses = [e.payload["status"] for e in status_evts]
    assert statuses.count("designing") == 2, "Should have 2 design rounds"
    assert statuses.count("developing") == 2, "Should have 2 develop rounds"

    # Verify round numbers in status events
    design_rounds = [e.payload["round"] for e in status_evts if e.payload["status"] == "designing"]
    assert design_rounds == [1, 2]
    develop_rounds = [e.payload["round"] for e in status_evts if e.payload["status"] == "developing"]
    assert develop_rounds == [1, 2]

    # -- Git verification --
    result = subprocess.run(
        ["git", "branch", "--list", "shadowcoder/*"],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert "shadowcoder/issue-1" in result.stdout
