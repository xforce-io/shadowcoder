# Config Restructure & Runtime Improvements

Issues discovered from dolphin experiment (kweaver/kweaver#84, 2026-03-25).

## 1. Config Restructure: clouds / models / agents / dispatch

### Problem

Current `agents.available` mixes cloud credentials, model selection, and agent behavior in one flat dict. Design and develop phases share a single `default` agent with no way to assign different models. `reviewers` config is separate from agent dispatch.

### New Config Schema

```yaml
clouds:
  <cloud-name>:
    env:                          # env vars passed to subprocess
      KEY: value

models:
  <model-name>:
    cloud: <cloud-name>           # reference
    model: <model-id>             # actual model identifier

agents:
  <agent-name>:
    type: claude_code             # agent class
    model: <model-name>           # reference
    permission_mode: auto
    roles:                        # optional per-role prompt overrides
      developer:
        instruction: "..."

dispatch:
  design: <agent-name>
  develop: <agent-name>
  design_review: [<agent-name>, ...]   # list: supports multi-reviewer
  develop_review: [<agent-name>, ...]  # list: supports multi-reviewer

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 5
  max_test_retries: 3

gate:
  mode: standard

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

**dispatch values**: `design` and `develop` are single agent names (strings). `design_review` and `develop_review` are lists (preserving current multi-reviewer support). A single string is also accepted and treated as a one-element list.

**Defaults when dispatch is omitted**: falls back to the first agent defined in `agents` (Python 3.7+ dict insertion order). If a specific phase is omitted, same fallback.

### Resolution Chain

`dispatch.develop` -> agent `fast-coder` -> model `deepseek-v3` -> cloud `volcengine` -> env vars

### Config Validation

At `Config.__init__` time, validate all cross-references:
- Every agent's `model` field must reference a key in `models`.
- Every model's `cloud` field must reference a key in `clouds`.
- Every name in `dispatch` must reference a key in `agents`.
- Fail with clear message: `"Agent 'fast-coder' references unknown model 'deepseek-v3'"`.

### Changes to `config.py`

Replace current accessors with:

```python
def _first_agent(self) -> str:
    """Return the first defined agent name as default fallback."""
    return next(iter(self._data.get("agents", {})))

def get_agent_for_phase(self, phase: str) -> str | list[str]:
    """Return agent name(s) for a phase.
    design/develop -> str, design_review/develop_review -> list[str]."""
    value = self._data.get("dispatch", {}).get(phase)
    if value is None:
        fallback = self._first_agent()
        return [fallback] if phase.endswith("_review") else fallback
    if phase.endswith("_review"):
        return value if isinstance(value, list) else [value]
    return value

def get_agent_config(self, name: str) -> dict:
    """Return merged config dict for agent: agent fields + model fields + cloud env."""
    agent = self._data["agents"][name]
    model_name = agent.get("model")
    result = dict(agent)
    if model_name:
        model = self._data["models"][model_name]
        result["model"] = model["model"]  # actual model id
        cloud_name = model.get("cloud")
        if cloud_name:
            cloud = self._data["clouds"][cloud_name]
            cloud_env = dict(cloud.get("env", {}))
            cloud_env.update(result.get("env", {}))
            if cloud_env:
                result["env"] = cloud_env
    return result
```

Remove: `get_default_agent()`, `get_reviewers()`, `get_available_agents()`.

### Changes to `registry.py`

- Remove `get("default")` resolution. Engine always passes explicit agent name from `get_agent_for_phase()`.
- Constructor and caching unchanged.

### Changes to `engine.py`

Replace all agent selection calls:

```python
# In _run_design_cycle, preflight, and _on_design:
agent = self.agents.get(self.config.get_agent_for_phase("design"))

# In _run_develop_cycle:
agent = self.agents.get(self.config.get_agent_for_phase("develop"))

# In _run_all_reviewers (replaces self.config.get_reviewers(action)):
reviewer_names = self.config.get_agent_for_phase(f"{action}_review")
# reviewer_names is already a list

# In gate escalation:
reviewer_names = self.config.get_agent_for_phase("develop_review")
reviewer = self.agents.get(reviewer_names[0])
```

Remove `issue.assignee` from agent selection. Set it from dispatch config for logging:
```python
issue.assignee = self.config.get_agent_for_phase("design")  # at issue creation
```

### Changes to tests

- Update `conftest.py` fixture `tmp_config` to new YAML structure.
- Update `test_config.py` to test `get_agent_for_phase()`, `get_agent_config()`, validation errors.
- Update all test files that use `tmp_config`: `tests/agents/test_registry.py`, `tests/test_integration.py`, `tests/core/test_engine.py`, `tests/core/test_engine_test_retry.py`, etc.
- Integration tests continue working since `AgentRegistry.get(name)` interface is unchanged -- only the `name` values change.

---

## 2. Review JSON Parsing Robustness (Bug Fix)

### Problem

When the reviewer model (e.g. DeepSeek) returns non-JSON output, the fallback truncates to 200 chars and wraps it as a single HIGH comment. The actual review content (which may contain valid insights) is lost.

### Solution

In `ClaudeCodeAgent.review()` fallback (except block), replace truncation with multi-strategy parsing:

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

### `_extract_comments_from_text(text)` specification

**Input patterns to handle** (observed from DeepSeek output):

```
1. **标签模式错误**：...严重性：high。
2. **清理时机**：...严重性：medium。
```

or:

```
- [HIGH] Think tag pattern is wrong...
- [MEDIUM] Cleanup timing...
```

**Extraction logic:**

1. Split text into items by numbered patterns (`\d+\.\s` or `\d+\)`) or bullet points (`-\s`).
2. For each item, search for severity keyword:
   - English: `critical`, `high`, `medium`, `low` (case-insensitive)
   - Chinese: `严重`, `高`, `中`, `低`
   - Bracketed: `[HIGH]`, `[CRITICAL]`, etc.
   - Colon-prefixed: `severity: high`, `严重性：high`
3. Map to Severity enum. Default to MEDIUM if item found but no severity keyword.
4. Return empty list if text has no numbered/bulleted structure.

**Examples:**

Input:
```
1. **标签模式错误**：代码用了【think】但实际是<think>。严重性：high。
2. **清理时机**：在eval前清理是合理的。严重性：medium。
```

Output: `[ReviewComment(HIGH, "标签模式错误：代码用了【think】但实际是<think>。"), ReviewComment(MEDIUM, "清理时机：在eval前清理是合理的。")]`

### Test location

Unit tests in `tests/agents/test_claude_code.py` with test cases for: valid numbered list, bulleted list, mixed Chinese/English severity, no structure (returns empty), severity without structure.

---

## 3. Gate: Untracked File Detection

### Problem

Agent creates temporary debug files (test_think_tags.py, test_debug.py) in worktree root. Gate and review don't detect this.

### Solution

In `_gate_check()`, after running tests, check for stray files using the same `git ls-files --others --exclude-standard` output that `_get_code_diff()` already runs. To avoid duplication, extract stray file detection into a shared helper.

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
    stray = []
    for f in untracked:
        if "/" not in f and f.endswith((".py", ".js", ".ts", ".rs", ".go")):
            stray.append(f)
    return stray
```

In `_gate_check()`, append warning to gate output (third return value) if stray files found:
```
WARNING: Stray files in worktree root: test_debug.py, test_think_tags.py
Remove these or move them to a test directory.
```

Gate does NOT fail on stray files -- warning only.

---

## 4. Gate Failure Feedback Quality

### Problem

Gate failure output is buried in the develop context among other fields. Key errors are not highlighted.

### Solution

Extract failure summary from gate output:

```python
import re

def _extract_gate_failure_summary(self, gate_output: str) -> str:
    """Extract FAILED test names and key error lines."""
    lines = gate_output.splitlines()
    summary_parts = []
    for line in lines:
        stripped = line.strip()
        # pytest: FAILED tests/...
        if stripped.startswith("FAILED "):
            summary_parts.append(stripped)
        # pytest error lines: E   SomeError: message
        elif re.match(r'^E\s+\w+Error:', stripped):
            summary_parts.append(stripped)
        # cargo test: thread '...' panicked
        elif "panicked at" in stripped:
            summary_parts.append(stripped)
        # go test: --- FAIL: TestFoo
        elif stripped.startswith("--- FAIL:"):
            summary_parts.append(stripped)
    if not summary_parts:
        return ""
    return "\n".join(summary_parts)
```

In develop context dict, add `gate_failure_summary` as a separate field. In `_build_context()` (claude_code.py), render it **first** with clear emphasis:

```python
gate_summary = request.context.get("gate_failure_summary", "")
if gate_summary:
    parts.append(f"\n!!! PREVIOUS GATE FAILURES - FIX THESE FIRST !!!\n{gate_summary}\n")
```

---

## 5. IN_PROGRESS Recovery

### Problem

If the process is killed during a develop round, the issue stays IN_PROGRESS (or FAILED). No command can resume it properly.

### Solution

Already partially implemented in `_on_run`. Fix the `_infer_blocked_stage` bug where it returns `None` when killed during first develop round (before any review exists):

```python
def _infer_blocked_stage(self, issue):
    """Infer which stage was running. Check sections and log entries."""
    if "Dev Review" in issue.sections:
        return "develop"
    if "开发" in issue.sections:      # develop section exists = was in develop
        return "develop"
    if "Design Review" in issue.sections:
        return "design"
    if "设计" in issue.sections:      # design section exists = was in design
        return "design"
    return None
```

Recovery logic in `_on_run`:

```python
if issue.status == IssueStatus.IN_PROGRESS:
    stage = self._infer_blocked_stage(issue)
    if stage == "develop":
        issue.status = IssueStatus.APPROVED
        self._log(issue_id, "run recovery: IN_PROGRESS -> continue develop")
    else:
        issue.status = IssueStatus.CREATED
        self._log(issue_id, "run recovery: IN_PROGRESS -> restart design")
    self.issue_store.save(issue)
```

Also handle FAILED status in `_on_run` the same way (currently only FAILED goes to design; should check if develop was in progress).

### Tests

In `test_integration.py`:
1. Set issue to IN_PROGRESS with "开发" section populated -> run -> verify transitions to APPROVED -> develop resumes.
2. Set issue to IN_PROGRESS with "设计" section populated -> run -> verify transitions to CREATED -> design restarts.
3. Set issue to IN_PROGRESS with no sections -> run -> verify transitions to CREATED.

---

## Implementation Order

1. **Item 2** (review parsing) -- isolated to claude_code.py, low risk
2. **Item 3** (stray file detection) -- isolated to engine.py gate, low risk
3. **Item 4** (gate feedback quality) -- engine.py + claude_code.py, low risk
4. **Item 5** (IN_PROGRESS recovery) -- engine.py, low risk, partially done
5. **Item 1** (config restructure) -- last, highest blast radius, touches config/registry/engine/all tests

---

## File Change Summary

| File | Changes |
|------|---------|
| `src/shadowcoder/core/config.py` | New schema parsing, validation, `get_agent_for_phase()`, remove old accessors |
| `src/shadowcoder/agents/registry.py` | Remove `"default"` resolution |
| `src/shadowcoder/core/engine.py` | Phase-based agent selection, stray file check, gate failure summary, `_infer_blocked_stage` fix, IN_PROGRESS/FAILED recovery |
| `src/shadowcoder/agents/claude_code.py` | Review fallback with `_extract_comments_from_text`, gate_failure_summary in `_build_context` |
| `tests/conftest.py` | Update config fixture to new YAML schema |
| `tests/core/test_config.py` | Test new config accessors and validation |
| `tests/agents/test_claude_code.py` | Test review text extraction |
| `tests/agents/test_registry.py` | Update for new config format |
| `tests/test_integration.py` | IN_PROGRESS recovery tests, update config |
| `tests/core/test_engine.py` | Update config |
| `tests/core/test_engine_test_retry.py` | Update config |
