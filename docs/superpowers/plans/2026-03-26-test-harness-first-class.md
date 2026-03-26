# Test Harness as First-Class Citizen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure gate can always run tests: preflight fails fast when test command is undetectable, designer must specify test_command, gate uses it.

**Architecture:** DesignOutput gains `test_command` field. Engine stores it in feedback JSON. Preflight checks test command detectability for existing projects. Gate resolves test_command via priority chain: config > design > detect_language > fail.

**Tech Stack:** Python, pytest

---

### Task 1: DesignOutput gains test_command field

**Files:**
- Modify: `src/shadowcoder/agents/types.py:42-44`
- Test: `tests/agents/test_types.py`

- [ ] **Step 1: Write test for new field**

```python
def test_design_output_test_command():
    d = DesignOutput(document="doc", test_command="make -C bkn test")
    assert d.test_command == "make -C bkn test"

def test_design_output_test_command_default():
    d = DesignOutput(document="doc")
    assert d.test_command is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agents/test_types.py::test_design_output_test_command -v`
Expected: FAIL — unexpected keyword argument

- [ ] **Step 3: Add field to DesignOutput**

In `src/shadowcoder/agents/types.py`, add to DesignOutput:

```python
@dataclass
class DesignOutput:
    document: str
    test_command: str | None = None
    usage: AgentUsage | None = None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/agents/test_types.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/agents/types.py tests/agents/test_types.py
git commit -m "feat: add test_command field to DesignOutput"
```

---

### Task 2: Designer prompt requires test_command

**Files:**
- Modify: `src/shadowcoder/agents/claude_code.py:271-290`

- [ ] **Step 1: Update designer prompt to require test_command**

In `claude_code.py`, modify the design system prompt to add at the end (before the "Output ONLY" line):

```python
system += dedent("""\
    Produce a CONCISE technical design document (target 5,000-15,000 characters).
    Focus on: architecture decisions, component interfaces, data flow,
    error handling strategy, and TEST STRATEGY.

    TEST STRATEGY is mandatory. You MUST include:
    - The exact test command to run all tests (e.g. "make -C module test",
      "go test ./...", "pytest -v"). For monorepos, specify the full path.
    - What tests to add or modify, and how they map to acceptance criteria.

    Do NOT include implementation details (code, pseudocode, function
    bodies) — those belong in the code.
    Do NOT repeat the requirements — reference them by name.

    If there are previous review comments, address each one specifically.

    CRITICAL: You MUST output the COMPLETE design document every time,
    not just the changes or a supplement. The previous version will be
    REPLACED entirely by your output. If you only output a patch,
    the full design will be lost.

    At the END of the document, output a fenced metadata block:
    ```yaml
    test_command: "<exact shell command to run tests>"
    ```

    Output the design document in markdown format.
""")
```

- [ ] **Step 2: Parse test_command from design output**

In `claude_code.py`, modify the `design()` method to extract `test_command`:

```python
async def design(self, request: AgentRequest) -> DesignOutput:
    # ... existing code ...
    result, usage = await self._run_claude_with_usage(prompt, cwd=cwd, system_prompt=system)
    test_command = self._extract_test_command(result)
    return DesignOutput(document=result, test_command=test_command, usage=usage)
```

Add the extraction method:

```python
@staticmethod
def _extract_test_command(document: str) -> str | None:
    """Extract test_command from yaml metadata block at end of design document."""
    import re
    match = re.search(r'```ya?ml\s*\n.*?test_command:\s*["\']?(.+?)["\']?\s*\n.*?```',
                      document, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
```

- [ ] **Step 3: Write test for extraction**

In `tests/agents/test_claude_code.py`:

```python
def test_extract_test_command_from_design():
    from shadowcoder.agents.claude_code import ClaudeCodeAgent
    doc = '# Design\n\nSome content.\n\n```yaml\ntest_command: "make -C bkn/bkn-backend test"\n```\n'
    assert ClaudeCodeAgent._extract_test_command(doc) == "make -C bkn/bkn-backend test"

def test_extract_test_command_missing():
    from shadowcoder.agents.claude_code import ClaudeCodeAgent
    doc = '# Design\n\nNo metadata block.'
    assert ClaudeCodeAgent._extract_test_command(doc) is None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/agents/test_claude_code.py::test_extract_test_command_from_design tests/agents/test_claude_code.py::test_extract_test_command_missing -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/agents/claude_code.py tests/agents/test_claude_code.py
git commit -m "feat: designer prompt requires test_command, parse from output"
```

---

### Task 3: Design reviewer checks test strategy

**Files:**
- Modify: `src/shadowcoder/agents/claude_code.py:368-375`

- [ ] **Step 1: Update design reviewer prompt**

In the design review system prompt (the `else` branch for non-develop review), add test strategy check:

```python
system += dedent("""\
    You are reviewing a DESIGN DOCUMENT, not code.
    Evaluate the design for: completeness, architectural soundness,
    interface clarity, error handling strategy, and testability.
    Do NOT check whether source files or code exist — implementation
    happens in a later phase. Focus only on the design quality.

    CRITICAL review item: The design MUST include a test strategy section with:
    - An exact test command (e.g. "make -C module test", "go test ./...")
    - A yaml metadata block at the end with test_command field
    If either is missing, flag as HIGH severity.""")
```

- [ ] **Step 2: Commit**

```bash
git add src/shadowcoder/agents/claude_code.py
git commit -m "feat: design reviewer checks for test strategy and test_command"
```

---

### Task 4: Engine stores and uses design test_command

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (design cycle + gate_check)
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write test — gate uses design test_command when detect_language fails**

```python
async def test_gate_uses_design_test_command(bus, store, task_mgr, config):
    """When detect_language fails but design provided test_command, gate uses it."""
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="simple"))
    agent.design = AsyncMock(return_value=DesignOutput(document="design", test_command="make -C sub test"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="done"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    # Save test_command in feedback (simulating what design cycle does)
    fb = store.load_feedback(1)
    fb["test_command"] = "make -C sub test"
    store.save_feedback(1, fb)

    # Gate should use "make -C sub test" instead of failing
    # We test _gate_check directly
    passed, msg, output = await engine._gate_check(1, "/nonexistent", [])
    # It will fail because /nonexistent doesn't exist, but the error should be
    # about command execution, NOT "Cannot detect test command"
    assert "Cannot detect test command" not in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_engine.py::test_gate_uses_design_test_command -v`
Expected: FAIL — gate still returns "Cannot detect test command"

- [ ] **Step 3: Store test_command in feedback after design**

In `engine.py`, in `_run_design_cycle`, after saving design output, store `test_command`:

```python
# After: self.issue_store.update_section(issue.id, section_key, content)
# Add:
if output.test_command:
    fb = self.issue_store.load_feedback(issue.id)
    fb["test_command"] = output.test_command
    self.issue_store.save_feedback(issue.id, fb)
    self._log(issue.id, f"Test command: {output.test_command}")
```

- [ ] **Step 4: Update gate to use design test_command**

In `_gate_check`, add design test_command lookup between config and detect_language:

```python
async def _gate_check(self, issue_id: int, worktree_path: str,
                      proposed_tests: list) -> tuple[bool, str, str]:
    test_cmd = self.config.get_test_command()
    profile = None
    if not test_cmd:
        # Try design-provided test_command
        fb = self.issue_store.load_feedback(issue_id)
        test_cmd = fb.get("test_command")
    if not test_cmd:
        if not worktree_path:
            return True, "no worktree, gate skipped", ""
        profile = detect_language(worktree_path)
        if not profile:
            return False, (
                f"Cannot detect test command for {worktree_path}. "
                f"Set build.test_command in config."), ""
        test_cmd = profile.test_command
    # ... rest unchanged
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/core/test_engine.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "feat: engine stores design test_command, gate uses priority chain"
```

---

### Task 5: Preflight fail fast for existing projects

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (`_on_design`)
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write test — preflight blocks when no test command detectable**

```python
async def test_preflight_blocks_no_test_command(bus, store, task_mgr, config):
    """Existing project with no detectable test command should block at preflight."""
    agent = AsyncMock()
    agent.preflight = AsyncMock(return_value=PreflightOutput(feasibility="high", estimated_complexity="simple"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)

    engine = make_engine(bus, store, task_mgr, reg, config)
    store.create("Test issue")

    # Simulate existing project: worktree exists with files but no marker
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        # Create a file so it's not empty (existing project)
        Path(os.path.join(td, "main.go")).write_text("package main")
        # Monkey-patch task creation to use this dir
        task_mgr.create = AsyncMock()
        task_mgr.create.return_value = MagicMock(worktree_path=td, task_id="t1")

        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.BLOCKED

        log = store.get_log(1)
        assert "test command" in log.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_engine.py::test_preflight_blocks_no_test_command -v`
Expected: FAIL — design proceeds without blocking

- [ ] **Step 3: Add test command check in _on_design**

In `_on_design`, after preflight and before `_run_design_cycle`, add:

```python
# Check test command detectability for existing projects
task = await self.task_manager.create(issue, repo_path=self.repo_path,
    action="design", agent_name=self.config.get_agent_for_phase("design"))

if not self.config.get_test_command():
    wt = task.worktree_path
    if wt and any(Path(wt).iterdir()):
        # Existing project — must be able to detect test command
        if not detect_language(wt):
            self._log(issue.id,
                "Preflight BLOCKED: Cannot detect test command for existing project. "
                "Set build.test_command in config, or add a standard marker file "
                "(go.mod, Cargo.toml, pyproject.toml, Makefile) at the project root.")
            self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                "issue_id": issue.id,
                "reason": "No detectable test command for existing project"}))
            return

await self._run_design_cycle(issue, task)
```

Note: Move the `task = await self.task_manager.create(...)` BEFORE the check so we have `worktree_path`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "feat: preflight fail fast when test command undetectable for existing projects"
```

---

### Task 6: Full regression + final commit

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS (250+ tests)

- [ ] **Step 2: Verify with manual check**

Confirm the priority chain works:
1. `config.test_command` → used if set
2. `feedback["test_command"]` (from design) → used if config not set
3. `detect_language` → fallback
4. None → fail with clear message
