# Config Restructure & Runtime Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix review parsing bug, improve gate feedback, add per-phase agent dispatch, restructure config into clouds/models/agents/dispatch layers.

**Architecture:** Five independent improvements applied in risk order: review parsing (Item 2), stray file detection (Item 3), gate feedback quality (Item 4), IN_PROGRESS recovery (Item 5), config restructure (Item 1). Each item is a separate commit.

**Tech Stack:** Python 3.13, pytest, YAML config

**Spec:** `docs/superpowers/specs/2026-03-25-config-and-runtime-improvements-design.md`

---

### Task 1: Review JSON Parsing Robustness

**Files:**
- Modify: `src/shadowcoder/agents/claude_code.py` (review fallback, lines 406-415)
- Test: `tests/agents/test_claude_code.py`

- [ ] **Step 1: Write failing tests for text extraction**

Add to `tests/agents/test_claude_code.py`:

```python
async def test_review_extract_numbered_chinese():
    """Extract structured comments from numbered Chinese text with severity."""
    agent = ClaudeCodeAgent({"type": "claude_code"})
    text = (
        '1. **标签模式错误**：代码用了【think】但实际是<think>。严重性：high。\n'
        '2. **清理时机**：在eval前清理是合理的。严重性：medium。'
    )
    comments = agent._extract_comments_from_text(text)
    assert len(comments) == 2
    assert comments[0].severity == Severity.HIGH
    assert comments[1].severity == Severity.MEDIUM


async def test_review_extract_bracketed_severity():
    """Extract comments with [HIGH] style severity markers."""
    agent = ClaudeCodeAgent({"type": "claude_code"})
    text = "- [HIGH] Think tag pattern is wrong\n- [MEDIUM] Cleanup timing issue"
    comments = agent._extract_comments_from_text(text)
    assert len(comments) == 2
    assert comments[0].severity == Severity.HIGH
    assert "Think tag" in comments[0].message


async def test_review_extract_no_structure():
    """Unstructured text returns empty list."""
    agent = ClaudeCodeAgent({"type": "claude_code"})
    comments = agent._extract_comments_from_text("Some random thoughts about code quality")
    assert comments == []


async def test_review_extract_default_severity():
    """Items without explicit severity default to MEDIUM."""
    agent = ClaudeCodeAgent({"type": "claude_code"})
    text = "1. Missing error handling for edge case\n2. Variable naming unclear"
    comments = agent._extract_comments_from_text(text)
    assert len(comments) == 2
    assert all(c.severity == Severity.MEDIUM for c in comments)


async def test_review_fallback_preserves_full_text():
    """When JSON parse fails and no structure found, full text is preserved (not truncated)."""
    agent = ClaudeCodeAgent({"type": "claude_code"})
    long_text = "A" * 500  # longer than the old 200-char truncation
    agent._run_claude_with_usage = AsyncMock(return_value=(long_text, _make_usage()))
    result = await agent.review(sample_request_factory())
    assert len(result.comments) == 1
    assert long_text in result.comments[0].message
```

Also add a helper at module level:

```python
def sample_request_factory():
    """Create a sample request for standalone tests."""
    issue = Issue(
        id=1, title="Test", status=IssueStatus.DESIGNING,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )
    return AgentRequest(action="review", issue=issue, context={"worktree_path": "/tmp"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agents/test_claude_code.py -k "extract" -v`
Expected: FAIL — `_extract_comments_from_text` does not exist yet

- [ ] **Step 3: Implement `_extract_comments_from_text`**

In `src/shadowcoder/agents/claude_code.py`, add method to `ClaudeCodeAgent`:

```python
import re

def _extract_comments_from_text(self, text: str) -> list[ReviewComment]:
    """Try to extract structured review comments from non-JSON text.

    Handles numbered lists (1. ...), bulleted lists (- ...), and
    severity markers in English/Chinese.
    """
    # Split into items by numbered or bulleted patterns
    items = re.split(r'\n(?=\d+[\.\)]\s|\-\s)', text.strip())
    if len(items) <= 1 and not re.match(r'\d+[\.\)]\s|\-\s', text.strip()):
        return []  # no list structure found

    severity_patterns = {
        Severity.CRITICAL: r'(?:critical|严重|致命)',
        Severity.HIGH: r'(?:high|高)',
        Severity.MEDIUM: r'(?:medium|中)',
        Severity.LOW: r'(?:low|低)',
    }
    bracket_pattern = re.compile(
        r'\[(CRITICAL|HIGH|MEDIUM|LOW)\]', re.IGNORECASE
    )
    colon_pattern = re.compile(
        r'(?:severity|严重性)[：:]\s*(critical|high|medium|low)', re.IGNORECASE
    )

    comments = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        # Clean item prefix (number or bullet)
        clean = re.sub(r'^\d+[\.\)]\s*', '', item)
        clean = re.sub(r'^-\s*', '', clean)
        if not clean:
            continue

        # Detect severity
        severity = Severity.MEDIUM  # default
        bm = bracket_pattern.search(clean)
        cm = colon_pattern.search(clean)
        if bm:
            severity = _SEVERITY_MAP[bm.group(1).lower()]
            clean = bracket_pattern.sub('', clean).strip()
        elif cm:
            severity = _SEVERITY_MAP[cm.group(1).lower()]
            # Remove the severity suffix
            clean = colon_pattern.sub('', clean).strip()
            clean = clean.rstrip('。.')
        else:
            for sev, pat in severity_patterns.items():
                if re.search(pat, clean, re.IGNORECASE):
                    severity = sev
                    break

        comments.append(ReviewComment(severity=severity, message=clean))
    return comments
```

- [ ] **Step 4: Update review fallback**

Replace lines 406-415 in `claude_code.py`:

```python
except (json.JSONDecodeError, KeyError, IndexError) as e:
    logger.warning("Failed to parse review JSON: %s", e)
    comments = self._extract_comments_from_text(result)
    if not comments:
        comments = [ReviewComment(
            severity=Severity.HIGH,
            message=f"Review output could not be parsed:\n{result}",
        )]
    return ReviewOutput(comments=comments, reviewer="claude-code", usage=usage)
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/agents/test_claude_code.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/agents/claude_code.py tests/agents/test_claude_code.py
git commit -m "fix: robust review text extraction when JSON parsing fails"
```

---

### Task 2: Gate Stray File Detection

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (`_gate_check`, `_get_untracked_files`, `_detect_stray_files`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_integration.py` in a new `TestGateStrayFiles` class:

```python
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
        env["store"].transition_status(issue.id, IssueStatus.APPROVED)
        env["store"].update_section(issue.id, "设计", "Test design")

        # Create stray file in worktree before develop
        task = await env["task_manager"].create(issue, repo_path=str(env["repo"]),
            action="develop", agent_name="claude-code")
        if task.worktree_path:
            (Path(task.worktree_path) / "test_debug.py").write_text("# temp")
            # Create a pyproject.toml so _detect_test_command finds a test runner
            (Path(task.worktree_path) / "pyproject.toml").write_text("[project]\nname='test'\n")

        # Mock _run_command to simulate passing tests (gate check runs tests first)
        from unittest.mock import AsyncMock
        env["engine"]._run_command = AsyncMock(return_value=(True, "all passed"))

        # Run gate check
        ok, msg, output = await env["engine"]._gate_check(
            issue.id, task.worktree_path, [])
        assert "WARNING" in output or "Stray" in output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration.py -k "stray" -v`
Expected: FAIL — no stray file warning in gate output

- [ ] **Step 3: Implement stray file detection**

In `src/shadowcoder/core/engine.py`, add methods:

```python
async def _get_untracked_files(self, worktree_path: str) -> list[str]:
    """Get list of untracked files in worktree."""
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-files", "--others", "--exclude-standard",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return [f for f in stdout.decode().strip().splitlines() if f]

def _detect_stray_files(self, untracked: list[str]) -> list[str]:
    """Flag files in worktree root that look like temp/debug artifacts."""
    return [f for f in untracked
            if "/" not in f and f.endswith((".py", ".js", ".ts", ".rs", ".go"))]
```

In `_gate_check`, after `self._run_command(test_cmd, ...)` and before returning, add:

```python
# Check for stray files
try:
    untracked = await self._get_untracked_files(worktree_path)
    stray = self._detect_stray_files(untracked)
    if stray:
        warning = f"\nWARNING: Stray files in worktree root: {', '.join(stray)}\nRemove these or move them to a test directory."
        output += warning
except Exception:
    pass  # best-effort
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_integration.py -k "stray" -v && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/test_integration.py
git commit -m "feat: gate warns on stray temp files in worktree root"
```

---

### Task 3: Gate Failure Feedback Quality

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (`_extract_gate_failure_summary`, develop context)
- Modify: `src/shadowcoder/agents/claude_code.py` (`_build_context`)
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write failing test for summary extraction**

Add to `tests/core/test_engine.py`:

```python
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
    """Extracts failure from cargo test output."""
    engine = integ_env["engine"]
    output = "thread 'test_foo' panicked at 'assertion failed', src/lib.rs:10"
    summary = engine._extract_gate_failure_summary(output)
    assert "panicked" in summary


def test_extract_gate_failure_summary_go(integ_env):
    """Extracts failure from go test output."""
    engine = integ_env["engine"]
    output = "--- FAIL: TestFoo (0.01s)\n    foo_test.go:15: expected 1, got 2"
    summary = engine._extract_gate_failure_summary(output)
    assert "FAIL: TestFoo" in summary


def test_extract_gate_failure_summary_empty(integ_env):
    """Returns empty string when no failures found."""
    engine = integ_env["engine"]
    assert engine._extract_gate_failure_summary("all tests passed") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_engine.py -k "gate_failure_summary" -v`
Expected: FAIL — method does not exist

- [ ] **Step 3: Implement `_extract_gate_failure_summary`**

In `src/shadowcoder/core/engine.py`, add to `Engine`:

```python
import re as _re

def _extract_gate_failure_summary(self, gate_output: str) -> str:
    """Extract FAILED test names and key error lines from gate output."""
    lines = gate_output.splitlines()
    summary_parts = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            summary_parts.append(stripped)
        elif _re.match(r'^E\s+\w*Error:', stripped):
            summary_parts.append(stripped)
        elif "panicked at" in stripped:
            summary_parts.append(stripped)
        elif stripped.startswith("--- FAIL:"):
            summary_parts.append(stripped)
    return "\n".join(summary_parts)
```

- [ ] **Step 4: Wire into develop context**

In `_run_develop_cycle` (around line 661), add `gate_failure_summary` to `ctx_dict`:

```python
ctx_dict = {
    "worktree_path": task.worktree_path,
    "gate_failure_summary": self._extract_gate_failure_summary(last_gate_output) if last_gate_output else "",
    "latest_review": latest_review,
    "feedback_summary": self._format_feedback_for_agent(issue.id),
    "acceptance_tests": self._format_acceptance_tests_for_developer(issue.id),
    "gate_output": self._truncate_output(last_gate_output) if last_gate_output else "",
}
```

- [ ] **Step 5: Render in `_build_context`**

In `src/shadowcoder/agents/claude_code.py` `_build_context()`, add right after `parts = [f"Issue: ..."]` (line 174) and **before** the section loop (line 175), so the developer sees failures first:

```python
gate_summary = request.context.get("gate_failure_summary", "")
if gate_summary:
    parts.append(f"\n!!! PREVIOUS GATE FAILURES - FIX THESE FIRST !!!\n{gate_summary}")
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/shadowcoder/core/engine.py src/shadowcoder/agents/claude_code.py tests/core/test_engine.py
git commit -m "feat: extract and highlight gate failure summary for developer"
```

---

### Task 4: IN_PROGRESS Recovery

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (`_infer_blocked_stage`, `_on_run`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_integration.py` in a new `TestInProgressRecovery` class:

```python
class TestInProgressRecovery:
    async def test_in_progress_develop_recovers(self, integ_env):
        """IN_PROGRESS with develop section -> run recovers to APPROVED -> develop resumes."""
        env = integ_env
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Recovery test"}))
        issue = store.list_all()[-1]
        store.transition_status(issue.id, IssueStatus.APPROVED)
        store.update_section(issue.id, "设计", "Test design")
        store.update_section(issue.id, "开发", "Test develop WIP")
        # Simulate crash: set to IN_PROGRESS
        issue = store.get(issue.id)
        issue.status = IssueStatus.IN_PROGRESS
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_in_progress_design_recovers(self, integ_env):
        """IN_PROGRESS with design section only -> run recovers to CREATED -> design restarts."""
        env = integ_env
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Recovery test design"}))
        issue = store.list_all()[-1]
        store.update_section(issue.id, "设计", "WIP design")
        issue = store.get(issue.id)
        issue.status = IssueStatus.IN_PROGRESS
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_in_progress_no_sections_restarts_design(self, integ_env):
        """IN_PROGRESS with no sections -> run restarts from design."""
        env = integ_env
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Recovery test empty"}))
        issue = store.list_all()[-1]
        issue.status = IssueStatus.IN_PROGRESS
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_failed_with_develop_section_resumes_develop(self, integ_env):
        """FAILED with develop section -> run recovers to APPROVED -> develop resumes."""
        env = integ_env
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Failed recovery develop"}))
        issue = store.list_all()[-1]
        store.transition_status(issue.id, IssueStatus.APPROVED)
        store.update_section(issue.id, "设计", "Test design")
        store.update_section(issue.id, "开发", "Develop WIP")
        issue = store.get(issue.id)
        issue.status = IssueStatus.FAILED
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE

    async def test_failed_with_design_only_restarts_design(self, integ_env):
        """FAILED with design section only -> run restarts from design."""
        env = integ_env
        store = env["store"]
        await env["bus"].publish(Message(MessageType.CMD_CREATE_ISSUE,
            {"title": "Failed recovery design"}))
        issue = store.list_all()[-1]
        store.update_section(issue.id, "设计", "WIP design")
        issue = store.get(issue.id)
        issue.status = IssueStatus.FAILED
        store.save(issue)

        await env["bus"].publish(Message(MessageType.CMD_RUN,
            {"issue_id": issue.id}))
        issue = store.get(issue.id)
        assert issue.status == IssueStatus.DONE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integration.py -k "InProgressRecovery" -v`
Expected: FAIL — `_on_run` doesn't handle IN_PROGRESS, or `_infer_blocked_stage` misses "开发" section

- [ ] **Step 3: Fix `_infer_blocked_stage`**

In `src/shadowcoder/core/engine.py`, update `_infer_blocked_stage`:

```python
def _infer_blocked_stage(self, issue):
    """Infer which stage was running based on issue sections."""
    if "Dev Review" in issue.sections:
        return "develop"
    if "开发" in issue.sections:
        return "develop"
    if "Design Review" in issue.sections:
        return "design"
    if "设计" in issue.sections:
        return "design"
    return None
```

- [ ] **Step 4: Verify IN_PROGRESS recovery in `_on_run`**

The recovery block was already added earlier in the session. Verify it's in place in `_on_run` (should be right after `issue = self.issue_store.get(issue_id)` and before the design phase check):

```python
# Recover interrupted issue
if issue.status == IssueStatus.IN_PROGRESS:
    stage = self._infer_blocked_stage(issue)
    if stage == "develop":
        self._log(issue_id, "run 恢复: IN_PROGRESS → 继续 develop")
        issue.status = IssueStatus.APPROVED
    else:
        self._log(issue_id, "run 恢复: IN_PROGRESS → 重跑 design")
        issue.status = IssueStatus.CREATED
    self.issue_store.save(issue)
```

- [ ] **Step 5: Also handle FAILED with develop section**

In the same recovery block, extend to handle FAILED status intelligently:

```python
if issue.status in (IssueStatus.IN_PROGRESS, IssueStatus.FAILED):
    stage = self._infer_blocked_stage(issue)
    if stage == "develop":
        self._log(issue_id, f"run 恢复: {issue.status.value} → 继续 develop")
        issue.status = IssueStatus.APPROVED
    else:
        self._log(issue_id, f"run 恢复: {issue.status.value} → 重跑 design")
        issue.status = IssueStatus.CREATED
    self.issue_store.save(issue)
```

And update the design phase check to no longer include FAILED (it's now handled above):

```python
if issue.status in (IssueStatus.CREATED, IssueStatus.BLOCKED):
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/test_integration.py
git commit -m "feat: recover IN_PROGRESS and FAILED issues via run command"
```

---

### Task 5: Config Restructure — New Schema

**NOTE:** This task updates `conftest.py` alongside `config.py` in the same commit to avoid a broken test suite between commits.

**Files:**
- Modify: `src/shadowcoder/core/config.py`
- Modify: `tests/core/test_config.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing tests for new config accessors**

Rewrite `tests/core/test_config.py`:

```python
import pytest
from shadowcoder.core.config import Config


NEW_CONFIG = """\
clouds:
  local:
    env: {}
  volcengine:
    env:
      ANTHROPIC_BASE_URL: https://example.com
      ANTHROPIC_AUTH_TOKEN: test-key

models:
  sonnet:
    cloud: local
    model: sonnet
  deepseek:
    cloud: volcengine
    model: deepseek-v3-2-251201

agents:
  fast-coder:
    type: claude_code
    model: deepseek
    permission_mode: auto
  quality-reviewer:
    type: claude_code
    model: sonnet

dispatch:
  design: fast-coder
  develop: fast-coder
  design_review: [quality-reviewer]
  develop_review: [quality-reviewer]

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
"""


@pytest.fixture
def new_config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(NEW_CONFIG)
    return Config(str(p))


def test_get_agent_for_phase_design(new_config):
    assert new_config.get_agent_for_phase("design") == "fast-coder"


def test_get_agent_for_phase_develop(new_config):
    assert new_config.get_agent_for_phase("develop") == "fast-coder"


def test_get_agent_for_phase_review_returns_list(new_config):
    assert new_config.get_agent_for_phase("design_review") == ["quality-reviewer"]
    assert new_config.get_agent_for_phase("develop_review") == ["quality-reviewer"]


def test_get_agent_for_phase_review_string_becomes_list(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(NEW_CONFIG.replace(
        "design_review: [quality-reviewer]",
        "design_review: quality-reviewer"))
    config = Config(str(p))
    assert config.get_agent_for_phase("design_review") == ["quality-reviewer"]


def test_get_agent_for_phase_fallback(tmp_path):
    """Missing dispatch falls back to first agent."""
    p = tmp_path / "config.yaml"
    p.write_text("""\
clouds:
  local:
    env: {}
models:
  sonnet:
    cloud: local
    model: sonnet
agents:
  only-agent:
    type: claude_code
    model: sonnet
""")
    config = Config(str(p))
    assert config.get_agent_for_phase("design") == "only-agent"
    assert config.get_agent_for_phase("design_review") == ["only-agent"]


def test_get_agent_config_merges_cloud_env(new_config):
    ac = new_config.get_agent_config("fast-coder")
    assert ac["type"] == "claude_code"
    assert ac["model"] == "deepseek-v3-2-251201"  # resolved from models
    assert ac["env"]["ANTHROPIC_BASE_URL"] == "https://example.com"


def test_get_agent_config_no_cloud_env(new_config):
    ac = new_config.get_agent_config("quality-reviewer")
    assert ac["model"] == "sonnet"
    assert "env" not in ac or ac.get("env", {}) == {}


def test_validation_bad_model_ref(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("""\
clouds:
  local:
    env: {}
models:
  sonnet:
    cloud: local
    model: sonnet
agents:
  bad:
    type: claude_code
    model: nonexistent
""")
    with pytest.raises(ValueError, match="unknown model"):
        Config(str(p))


def test_validation_bad_cloud_ref(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("""\
clouds: {}
models:
  sonnet:
    cloud: nonexistent
    model: sonnet
agents:
  a:
    type: claude_code
    model: sonnet
""")
    with pytest.raises(ValueError, match="unknown cloud"):
        Config(str(p))


def test_validation_bad_dispatch_ref(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("""\
clouds:
  local:
    env: {}
models:
  sonnet:
    cloud: local
    model: sonnet
agents:
  a:
    type: claude_code
    model: sonnet
dispatch:
  design: nonexistent
""")
    with pytest.raises(ValueError, match="unknown agent"):
        Config(str(p))


def test_max_review_rounds(new_config):
    assert new_config.get_max_review_rounds() == 3


def test_max_review_rounds_default(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds: {}\nmodels: {}\nagents:\n  a:\n    type: x\n")
    config = Config(str(p))
    assert config.get_max_review_rounds() == 3


def test_issue_dir(new_config):
    assert new_config.get_issue_dir() == ".shadowcoder/issues"


def test_worktree_dir(new_config):
    assert new_config.get_worktree_dir() == ".shadowcoder/worktrees"


def test_log_dir(new_config):
    assert new_config.get_log_dir() == "/tmp/shadowcoder-test/logs"


def test_log_level(new_config):
    assert new_config.get_log_level() == "INFO"


def test_missing_config_file():
    with pytest.raises(FileNotFoundError):
        Config("/nonexistent/config.yaml")


def test_max_budget_not_set(new_config):
    assert new_config.get_max_budget_usd() is None


def test_max_budget_set(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "clouds: {}\nmodels: {}\nagents:\n  a:\n    type: x\n"
        "review_policy:\n  max_budget_usd: 2.50\n"
    )
    config = Config(str(p))
    assert config.get_max_budget_usd() == 2.50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_config.py -v`
Expected: FAIL — old Config has no `get_agent_for_phase`, no validation

- [ ] **Step 3: Implement new `config.py`**

Rewrite `src/shadowcoder/core/config.py`:

```python
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
        self._validate()

    def _validate(self):
        """Validate cross-references between clouds, models, agents, dispatch."""
        clouds = self._data.get("clouds", {})
        models = self._data.get("models", {})
        agents = self._data.get("agents", {})
        dispatch = self._data.get("dispatch", {})

        for model_name, model_conf in models.items():
            cloud = model_conf.get("cloud")
            if cloud and cloud not in clouds:
                raise ValueError(
                    f"Model '{model_name}' references unknown cloud '{cloud}'")

        for agent_name, agent_conf in agents.items():
            model = agent_conf.get("model")
            if model and model not in models:
                raise ValueError(
                    f"Agent '{agent_name}' references unknown model '{model}'")

        for phase, value in dispatch.items():
            names = value if isinstance(value, list) else [value]
            for name in names:
                if name not in agents:
                    raise ValueError(
                        f"Dispatch '{phase}' references unknown agent '{name}'")

    def _first_agent(self) -> str:
        """Return the first defined agent name as default fallback."""
        agents = self._data.get("agents", {})
        if not agents:
            raise ValueError("No agents defined in config")
        return next(iter(agents))

    def get_agent_for_phase(self, phase: str) -> str | list[str]:
        """Return agent name(s) for a phase.

        design/develop -> str
        design_review/develop_review -> list[str]
        """
        value = self._data.get("dispatch", {}).get(phase)
        if value is None:
            fallback = self._first_agent()
            return [fallback] if phase.endswith("_review") else fallback
        if phase.endswith("_review"):
            return value if isinstance(value, list) else [value]
        return value

    def get_agent_config(self, name: str) -> dict:
        """Return merged config dict: agent fields + resolved model + cloud env."""
        agent = self._data["agents"][name]
        model_name = agent.get("model")
        result = dict(agent)
        if model_name:
            model = self._data["models"][model_name]
            result["model"] = model["model"]
            cloud_name = model.get("cloud")
            if cloud_name:
                cloud = self._data["clouds"][cloud_name]
                cloud_env = dict(cloud.get("env") or {})
                cloud_env.update(result.get("env") or {})
                if cloud_env:
                    result["env"] = cloud_env
        return result

    def get_max_review_rounds(self) -> int:
        return self._data.get("review_policy", {}).get("max_review_rounds", 3)

    def get_max_test_retries(self) -> int:
        return self._data.get("review_policy", {}).get("max_test_retries", 3)

    def get_max_budget_usd(self) -> float | None:
        return self._data.get("review_policy", {}).get("max_budget_usd")

    def get_issue_dir(self) -> str:
        return self._data.get("issue_store", {}).get("dir", ".shadowcoder/issues")

    def get_worktree_dir(self) -> str:
        return self._data.get("worktree", {}).get("base_dir", ".shadowcoder/worktrees")

    def get_log_dir(self) -> str:
        return self._data.get("logging", {}).get("dir", "~/.shadowcoder/logs")

    def get_log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")

    def get_test_command(self) -> str | None:
        return self._data.get("build", {}).get("test_command")

    def get_gate_mode(self) -> str:
        return self._data.get("gate", {}).get("mode", "standard")
```

- [ ] **Step 4: Update `conftest.py` fixture in the same commit**

Replace the `tmp_config` fixture in `tests/conftest.py`:

```python
@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config file with new schema."""
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
  codex:
    type: codex
    model: default-model

dispatch:
  design: claude-code
  develop: claude-code
  design_review: [claude-code]
  develop_review: [claude-code]

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

- [ ] **Step 5: Run config tests**

Run: `python -m pytest tests/core/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/config.py tests/core/test_config.py tests/conftest.py
git commit -m "refactor: config restructure with clouds/models/agents/dispatch layers"
```

---

### Task 6: Config Restructure — Registry & Engine Wiring

**Files:**
- Modify: `src/shadowcoder/agents/registry.py`
- Modify: `src/shadowcoder/core/engine.py` (agent selection calls)
- Modify: all test files using `tmp_config` / `integ_config`

- [ ] **Step 1: Update `registry.py`**

Remove the `"default"` resolution from `AgentRegistry.get()`:

```python
def get(self, name: str) -> BaseAgent:
    if name not in self._instances:
        agent_conf = self.config.get_agent_config(name)
        agent_type = agent_conf["type"]
        if agent_type not in self._agent_classes:
            raise KeyError(f"Unknown agent type: {agent_type}")
        cls = self._agent_classes[agent_type]
        self._instances[name] = cls(agent_conf)
    return self._instances[name]
```

- [ ] **Step 2: Update `engine.py` agent selection**

Replace all `self.agents.get(issue.assignee or "default")` calls:

In `_run_design_cycle` (design agent selection):
```python
agent = self.agents.get(self.config.get_agent_for_phase("design"))
```

In `_run_develop_cycle` (develop agent selection):
```python
agent = self.agents.get(self.config.get_agent_for_phase("develop"))
```

In preflight (`_on_design`):
```python
agent = self.agents.get(self.config.get_agent_for_phase("design"))
```

In `_run_all_reviewers`, replace `self.config.get_reviewers(action)`:
```python
reviewer_names = self.config.get_agent_for_phase(f"{action}_review")
```

In gate escalation, replace `self.config.get_reviewers("develop")`:
```python
reviewer_names = self.config.get_agent_for_phase("develop_review")
```

In task creation calls, replace `agent_name=issue.assignee or "default"`:
```python
agent_name=self.config.get_agent_for_phase("design")  # or "develop"
```

- [ ] **Step 3: Update `integ_config` fixture in `test_integration.py`**

Replace `integ_config` and `integ_config_with_budget` fixtures to use new schema format (same structure as `tmp_config` but with budget field where needed).

- [ ] **Step 4: Update `test_registry.py`**

Update `test_get_default` — since `"default"` resolution is removed, this test should verify that explicit names work and that unknown names raise `KeyError`. Remove or modify the default test.

- [ ] **Step 5: Update other test files using `tmp_config`**

Update config fixtures in:
- `tests/core/test_engine.py`
- `tests/core/test_engine_test_retry.py`
- `tests/core/test_feedback.py`
- `tests/core/test_issue_store.py`
- `tests/core/test_version_archive.py`
- `tests/core/test_create_with_description.py`

Each file that creates its own config inline needs the YAML updated to the new schema. Files that only use the `tmp_config` fixture from `conftest.py` are already fixed by Step 1.

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL 213+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/shadowcoder/agents/registry.py src/shadowcoder/core/engine.py \
    tests/conftest.py tests/test_integration.py tests/agents/test_registry.py \
    tests/core/
git commit -m "refactor: wire engine and registry to new config schema"
```

---

### Task 7: Update `run_real.py` and CLAUDE.md

**Files:**
- Modify: `scripts/run_real.py` (remove `issue.assignee` usage if any)
- Modify: `CLAUDE.md` (update config example)

- [ ] **Step 1: Update CLAUDE.md config example**

Replace the Multi-Model Support section with the new config format:

```yaml
clouds:
  anthropic:
    env: {}
  volcengine:
    env:
      ANTHROPIC_BASE_URL: https://ark.cn-beijing.volces.com/api/coding
      ANTHROPIC_AUTH_TOKEN: <key>

models:
  sonnet:
    cloud: anthropic
    model: sonnet
  deepseek-v3:
    cloud: volcengine
    model: deepseek-v3-2-251201

agents:
  claude-coder:
    type: claude_code
    model: sonnet
  fast-coder:
    type: claude_code
    model: deepseek-v3

dispatch:
  design: fast-coder
  develop: fast-coder
  design_review: [claude-coder]
  develop_review: [claude-coder]
```

- [ ] **Step 2: Run full test suite one final time**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/run_real.py CLAUDE.md
git commit -m "docs: update config examples for new clouds/models/agents/dispatch schema"
```
