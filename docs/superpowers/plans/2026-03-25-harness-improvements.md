# Harness Improvements: Acceptance Contract, Session Resume, Observability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three improvements inspired by Anthropic's harness design article — acceptance contract (A), gate-fail session resume (C), per-phase cost observability (E).

**Architecture:** E adds `phase`/`round_num` to `AgentUsage` and per-phase breakdown to `_usage_summary`. A splits `proposed_tests` into `acceptance_tests` (locked at design review) and `supplementary_tests` (added during dev review), with a `gate.mode` config for strict/standard. C adds `session_id`/`resume_id` to agent's `develop()` signature so gate-fail retries resume the same CLI session.

**Tech Stack:** Python, pytest, claude CLI (`--session-id`, `--resume`)

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/shadowcoder/agents/types.py` | Modify | Add `phase`, `round_num` to `AgentUsage` |
| `src/shadowcoder/core/engine.py` | Modify | Phase tracking in `_track_usage`, acceptance/supplementary split in `_update_feedback` and `_gate_check`, session management in `_run_develop_cycle`, per-phase `_usage_summary` |
| `src/shadowcoder/core/config.py` | Modify | Add `get_gate_mode()` |
| `src/shadowcoder/agents/base.py` | Modify | Add `session_id`/`resume_id` params to `develop()` |
| `src/shadowcoder/agents/claude_code.py` | Modify | Wire `session_id`/`resume_id` into `_run_claude_with_usage` and `develop()` |
| `src/shadowcoder/core/issue_store.py` | Modify | Default feedback structure gains `acceptance_tests`/`supplementary_tests` |
| `tests/agents/test_claude_code.py` | Modify | Tests for session params in develop |
| `tests/test_integration.py` | Modify | Tests for acceptance vs supplementary gate logic, phase usage, session resume semantics |

---

### Task 1: E — Add `phase` and `round_num` to `AgentUsage`

**Files:**
- Modify: `src/shadowcoder/agents/types.py:24-29`
- Test: `tests/agents/test_claude_code.py`

- [ ] **Step 1: Write the failing test**

In `tests/agents/test_claude_code.py`, add:

```python
def test_agent_usage_has_phase_and_round():
    usage = AgentUsage(input_tokens=100, output_tokens=50, duration_ms=500,
                       cost_usd=0.01, phase="develop", round_num=2)
    assert usage.phase == "develop"
    assert usage.round_num == 2


def test_agent_usage_defaults():
    usage = AgentUsage()
    assert usage.phase == ""
    assert usage.round_num == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agents/test_claude_code.py::test_agent_usage_has_phase_and_round -v`
Expected: FAIL — `AgentUsage.__init__() got an unexpected keyword argument 'phase'`

- [ ] **Step 3: Add fields to `AgentUsage`**

In `src/shadowcoder/agents/types.py`, modify `AgentUsage`:

```python
@dataclass
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None
    phase: str = ""
    round_num: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agents/test_claude_code.py::test_agent_usage_has_phase_and_round tests/agents/test_claude_code.py::test_agent_usage_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/agents/types.py tests/agents/test_claude_code.py
git commit -m "feat: add phase and round_num to AgentUsage"
```

---

### Task 2: E — Phase-aware `_track_usage` and per-phase `_usage_summary`

**Files:**
- Modify: `src/shadowcoder/core/engine.py:42-70` (`_track_usage`, `_usage_summary`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_integration.py`, add a new test class after `TestVersionArchive`:

```python
class TestPhaseUsage:
    """Per-phase cost observability."""

    async def test_usage_summary_includes_phase_breakdown(self, system):
        """Usage summary breaks down cost by phase."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        # Give agent usage with cost
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
        # Should contain phase breakdown
        assert "design:" in summary.lower() or "Design:" in summary

    async def test_track_usage_records_phase(self, system):
        """_track_usage stores phase on the usage object."""
        engine = system["engine"]
        usage = AgentUsage(input_tokens=10, output_tokens=5, duration_ms=100, cost_usd=0.01)
        engine._track_usage(1, usage, phase="develop", round_num=2)
        recorded = engine._usage_by_issue[1][0]
        assert recorded.phase == "develop"
        assert recorded.round_num == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration.py::TestPhaseUsage -v`
Expected: FAIL — `_track_usage() got an unexpected keyword argument 'phase'`

- [ ] **Step 3: Modify `_track_usage` to stamp phase/round onto usage**

In `src/shadowcoder/core/engine.py`, change `_track_usage`:

```python
def _track_usage(self, issue_id: int, usage: AgentUsage | None,
                 phase: str = "", round_num: int = 0):
    """Accumulate usage for an issue, stamping phase metadata."""
    if usage is None:
        return
    usage.phase = phase
    usage.round_num = round_num
    self._usage_by_issue.setdefault(issue_id, []).append(usage)
```

- [ ] **Step 4: Modify `_usage_summary` to include per-phase breakdown**

In `src/shadowcoder/core/engine.py`, change `_usage_summary`:

```python
def _usage_summary(self, issue_id: int) -> str:
    """Format usage summary with per-phase breakdown."""
    usages = self._usage_by_issue.get(issue_id, [])
    if not usages:
        return "No usage data"
    input_t, output_t = self._total_tokens(issue_id)
    cost = self._total_cost(issue_id)
    total_duration = sum(u.duration_ms for u in usages) / 1000
    lines = [
        f"Calls: {len(usages)} | "
        f"Tokens: {input_t:,} in + {output_t:,} out | "
        f"Cost: ${cost:.4f} | "
        f"Time: {total_duration:.1f}s"
    ]

    # Per-phase breakdown (only if phases are recorded)
    phases: dict[str, list] = {}
    for u in usages:
        if u.phase:
            phases.setdefault(u.phase, []).append(u)
    if phases:
        lines.append("Phase breakdown:")
        for phase, phase_usages in phases.items():
            p_cost = sum(u.cost_usd or 0 for u in phase_usages)
            p_calls = len(phase_usages)
            pct = (p_cost / cost * 100) if cost > 0 else 0
            lines.append(f"  {phase}: {p_calls} calls, ${p_cost:.4f} ({pct:.0f}%)")

    return "\n".join(lines)
```

- [ ] **Step 5: Update all `_track_usage` call sites in engine.py to pass phase/round_num**

Find every `self._track_usage(issue.id, ...)` call and add the appropriate `phase=` and `round_num=` kwargs:

- `_run_design_cycle` line ~438: `self._track_usage(issue.id, output.usage, phase="design", round_num=round_num)`
- `_run_all_reviewers` line ~391: `self._track_usage(issue.id, review.usage, phase=f"{action}_review")`
  (`_run_all_reviewers` already receives `action` which is "design" or "develop")
- `_run_develop_cycle` line ~563: `self._track_usage(issue.id, output.usage, phase="develop", round_num=round_num)`
- `_on_design` preflight line ~715: `self._track_usage(issue.id, pf.usage, phase="preflight")`

Note: there is NO existing `_track_usage` call in the gate escalation block (lines 595-620). If gate escalation observability is desired, add a new call after the escalation reviewer call (~line 616, after `review = await reviewer.review(review_request)`):
```python
self._track_usage(issue.id, review.usage, phase="gate_escalation", round_num=round_num)
```
This is a **new addition**, not modifying an existing call.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_integration.py::TestPhaseUsage -v`
Expected: PASS

- [ ] **Step 7: Run full test suite to verify no regressions**

Run: `python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/test_integration.py
git commit -m "feat: per-phase cost observability in usage summary"
```

---

### Task 3: A — Split `proposed_tests` into `acceptance_tests` / `supplementary_tests`

**Files:**
- Modify: `src/shadowcoder/core/issue_store.py:36-41` (default feedback structure)
- Modify: `src/shadowcoder/core/engine.py:260-308` (`_update_feedback`)
- Modify: `src/shadowcoder/core/config.py` (add `get_gate_mode`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_integration.py`, add:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integration.py::TestAcceptanceContract -v`
Expected: FAIL — `acceptance_tests` key missing or tests in wrong bucket

- [ ] **Step 3: Update `load_feedback` default structure**

In `src/shadowcoder/core/issue_store.py`, change `load_feedback`:

```python
def load_feedback(self, issue_id: int) -> dict:
    path = self._feedback_path(issue_id)
    if not path.exists():
        return {"items": [], "proposed_tests": [],
                "acceptance_tests": [], "supplementary_tests": []}
    import json
    fb = json.loads(path.read_text(encoding="utf-8"))
    # Migrate: ensure new keys exist
    fb.setdefault("acceptance_tests", [])
    fb.setdefault("supplementary_tests", [])
    return fb
```

- [ ] **Step 4: Change `_update_feedback` to route tests by phase**

In `src/shadowcoder/core/engine.py`, modify `_update_feedback` to accept an `is_design_review` parameter:

```python
def _update_feedback(self, issue_id: int, review, current_round: int,
                     is_design_review: bool = False):
    """Update feedback state after a review. Pure symbolic logic."""
    fb = self.issue_store.load_feedback(issue_id)
    items = fb.get("items", [])

    # Mark resolved items
    resolved_ids = set(review.resolved_item_ids)
    for item in items:
        if item["id"] in resolved_ids and not item["resolved"]:
            item["resolved"] = True
            item["resolved_round"] = current_round

    # Unresolved items: bump times_raised
    for item in items:
        if not item["resolved"] and item["id"] not in resolved_ids:
            item["times_raised"] = item.get("times_raised", 1) + 1
            item["escalation_level"] = min(item["times_raised"], 4)

    # New comments -> new FeedbackItems
    next_num = max((int(item["id"][1:]) for item in items), default=0) + 1
    for comment in review.comments:
        fid = f"F{next_num}"
        next_num += 1
        items.append({
            "id": fid,
            "category": comment.severity.value,
            "description": comment.message,
            "round_introduced": current_round,
            "times_raised": 1,
            "resolved": False,
            "escalation_level": 1,
        })

    # Route proposed tests to the right bucket
    target_key = "acceptance_tests" if is_design_review else "supplementary_tests"
    tests = fb.get(target_key, [])
    for tc in review.proposed_tests:
        if not any(t["name"] == tc.name for t in tests):
            tests.append({
                "name": tc.name,
                "description": tc.description,
                "expected_behavior": tc.expected_behavior,
                "category": tc.category,
                "round_proposed": current_round,
            })
    fb[target_key] = tests

    # Also maintain proposed_tests as union for backward compat
    all_tests = fb.get("proposed_tests", [])
    for tc in review.proposed_tests:
        if not any(t["name"] == tc.name for t in all_tests):
            all_tests.append({
                "name": tc.name,
                "description": tc.description,
                "expected_behavior": tc.expected_behavior,
                "category": tc.category,
                "round_proposed": current_round,
            })
    fb["proposed_tests"] = all_tests

    fb["items"] = items
    self.issue_store.save_feedback(issue_id, fb)
```

- [ ] **Step 5: Update `_update_feedback` call sites**

In `_run_design_cycle` (~line 465):
```python
self._update_feedback(issue.id, last_review, round_num, is_design_review=True)
```

In `_run_develop_cycle` (~line 643):
```python
self._update_feedback(issue.id, last_review, round_num, is_design_review=False)
```

In `_run_develop_cycle` gate escalation (~line 616):
```python
self._update_feedback(issue.id, review, round_num, is_design_review=False)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_integration.py::TestAcceptanceContract -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/shadowcoder/core/engine.py src/shadowcoder/core/issue_store.py
git commit -m "feat: split proposed_tests into acceptance and supplementary"
```

---

### Task 4: A — Gate mode (strict/standard) using acceptance contract

**Files:**
- Modify: `src/shadowcoder/core/config.py` (add `get_gate_mode`)
- Modify: `src/shadowcoder/core/engine.py:114-152,524-588` (`_gate_check`, `_run_develop_cycle`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_integration.py`, add to `TestAcceptanceContract`:

```python
    async def test_standard_gate_only_checks_acceptance(self, system):
        """In standard mode, gate only checks acceptance_tests."""
        bus, store, agent, engine = (
            system["bus"], system["store"], system["agent"], system["engine"])

        # Manually set up feedback with both test types
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

        # Gate uses acceptance_tests only (standard mode)
        gate_tests = engine._get_gate_tests(1)
        assert any(t["name"] == "test_accept" for t in gate_tests)
        assert not any(t["name"] == "test_suppl" for t in gate_tests)
```

Add a separate test with strict config:

```python
class TestStrictGateMode:
    """Strict gate mode checks both acceptance and supplementary tests."""

    async def test_strict_gate_checks_all_tests(self, integ_repo, tmp_path, agent):
        """In strict mode, gate checks acceptance + supplementary tests."""
        config_path = tmp_path / "config_strict.yaml"
        config_path.write_text("""\
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
reviewers:
  design: [claude-code]
  develop: [claude-code]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integration.py::TestAcceptanceContract::test_standard_gate_only_checks_acceptance tests/test_integration.py::TestStrictGateMode -v`
Expected: FAIL — `_get_gate_tests` does not exist, `get_gate_mode` does not exist

- [ ] **Step 3: Add `get_gate_mode` to Config**

In `src/shadowcoder/core/config.py`:

```python
def get_gate_mode(self) -> str:
    """Get gate mode: 'strict' checks all tests, 'standard' checks acceptance only."""
    return self._data.get("gate", {}).get("mode", "standard")
```

- [ ] **Step 4: Add `_get_gate_tests` helper to Engine**

In `src/shadowcoder/core/engine.py`, add after `_check_budget`:

```python
def _get_gate_tests(self, issue_id: int) -> list:
    """Get tests for gate check based on gate mode."""
    fb = self.issue_store.load_feedback(issue_id)
    tests = list(fb.get("acceptance_tests", []))
    if self.config.get_gate_mode() == "strict":
        tests.extend(fb.get("supplementary_tests", []))
    # Fallback: if no categorized tests yet, use legacy proposed_tests
    if not tests:
        tests = list(fb.get("proposed_tests", []))
    return tests
```

- [ ] **Step 5: Wire `_get_gate_tests` into `_run_develop_cycle`**

In `_run_develop_cycle`, replace the line that reads `proposed_tests` from feedback (~line 532):

```python
# Before:
fb = self.issue_store.load_feedback(issue.id)
proposed_tests = fb.get("proposed_tests", [])

# After:
proposed_tests = self._get_gate_tests(issue.id)
```

And in the gate call loop, after `_update_feedback` refreshes feedback (~line 644-645), reload gate tests:

```python
# Before:
fb = self.issue_store.load_feedback(issue.id)
proposed_tests = fb.get("proposed_tests", [])

# After:
proposed_tests = self._get_gate_tests(issue.id)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_integration.py::TestAcceptanceContract::test_standard_gate_only_checks_acceptance tests/test_integration.py::TestStrictGateMode -v`
Expected: PASS

- [ ] **Step 7: Run full suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/shadowcoder/core/config.py src/shadowcoder/core/engine.py tests/test_integration.py
git commit -m "feat: gate mode (strict/standard) with acceptance contract"
```

---

### Task 5: A — Developer sees all tests, format helpers updated

**Files:**
- Modify: `src/shadowcoder/core/engine.py:349-359` (`_format_acceptance_tests_for_developer`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_integration.py`, add to `TestAcceptanceContract`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration.py::TestAcceptanceContract::test_developer_sees_both_test_types -v`
Expected: FAIL — current format only reads `proposed_tests`

- [ ] **Step 3: Update `_format_acceptance_tests_for_developer`**

In `src/shadowcoder/core/engine.py`:

```python
def _format_acceptance_tests_for_developer(self, issue_id: int) -> str:
    """Format all tests for developer context, distinguishing types."""
    fb = self.issue_store.load_feedback(issue_id)
    acceptance = fb.get("acceptance_tests", [])
    supplementary = fb.get("supplementary_tests", [])
    # Fallback for legacy issues without categorized tests
    if not acceptance and not supplementary:
        tests = fb.get("proposed_tests", [])
        if not tests:
            return ""
        lines = ["Acceptance tests to implement (from reviewer):"]
        for tc in tests:
            lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
        lines.append("\nYou must write executable tests for each.")
        return "\n".join(lines)

    lines = []
    if acceptance:
        lines.append("Acceptance tests (MUST pass for gate):")
        for tc in acceptance:
            lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
    if supplementary:
        lines.append("Supplementary tests (should implement for quality):")
        for tc in supplementary:
            lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
    if lines:
        lines.append("\nYou must write executable tests for each. Place them in the project's existing test directory.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test**

Run: `python -m pytest tests/test_integration.py::TestAcceptanceContract::test_developer_sees_both_test_types -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/test_integration.py
git commit -m "feat: developer context shows acceptance and supplementary tests"
```

---

### Task 6: C — Add session semantics to agent layer

**Files:**
- Modify: `src/shadowcoder/agents/base.py:22-25` (`develop` signature)
- Modify: `src/shadowcoder/agents/claude_code.py:79-162` (`_run_claude_with_usage`, `develop`)
- Test: `tests/agents/test_claude_code.py`

- [ ] **Step 1: Write the failing tests**

In `tests/agents/test_claude_code.py`, add:

```python
async def test_develop_passes_session_id(agent, sample_request):
    """develop() forwards session_id to _run_claude_with_usage."""
    sample_request.action = "develop"
    sample_request.context["session_id"] = "test-uuid-1234"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run_claude_with_usage.call_args[1]
    assert call_kwargs.get("session_id") == "test-uuid-1234"


async def test_develop_passes_resume_id(agent, sample_request):
    """develop() forwards resume_id to _run_claude_with_usage."""
    sample_request.action = "develop"
    sample_request.context["resume_id"] = "test-uuid-5678"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run_claude_with_usage.call_args[1]
    assert call_kwargs.get("resume_id") == "test-uuid-5678"


async def test_develop_no_session_by_default(agent, sample_request):
    """develop() without session context passes no session params."""
    sample_request.action = "develop"
    agent._run_claude_with_usage = AsyncMock(
        return_value=("Code", _make_usage()))
    agent._get_files_changed = AsyncMock(return_value=[])
    await agent.develop(sample_request)
    call_kwargs = agent._run_claude_with_usage.call_args[1]
    assert call_kwargs.get("session_id") is None
    assert call_kwargs.get("resume_id") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agents/test_claude_code.py::test_develop_passes_session_id tests/agents/test_claude_code.py::test_develop_passes_resume_id tests/agents/test_claude_code.py::test_develop_no_session_by_default -v`
Expected: FAIL — session_id/resume_id not in call kwargs

- [ ] **Step 3: Add session params to `_run_claude_with_usage`**

In `src/shadowcoder/agents/claude_code.py`, modify `_run_claude_with_usage` signature and cmd building:

```python
async def _run_claude_with_usage(self, prompt: str, cwd: str | None = None,
                                  system_prompt: str | None = None,
                                  session_id: str | None = None,
                                  resume_id: str | None = None,
                                  ) -> tuple[str, AgentUsage]:
    """Call claude CLI with JSON output to capture usage stats."""
    start = time.monotonic()
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", self._get_model(),
        "--permission-mode", self._get_permission_mode(),
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if session_id:
        cmd.extend(["--session-id", session_id])
    elif resume_id:
        cmd.extend(["--resume", resume_id])
    # ... rest unchanged
```

Also do the same for `_run_claude` (the text-output version) — add the same params for consistency, though it's only used by develop currently via `_run_claude_with_usage`.

- [ ] **Step 4: Modify `develop()` to read session context and forward it**

In `src/shadowcoder/agents/claude_code.py`, modify `develop`:

```python
async def develop(self, request: AgentRequest) -> DevelopOutput:
    cwd = request.context.get("worktree_path")
    context = self._build_context(request)

    role_instruction = self._get_role_instruction("developer")
    system = f"{role_instruction}\n\n" if role_instruction else ""
    system += dedent("""\
        Implement the code based on the design document. You MUST:
        1. Create actual source files in the working directory
        2. Write tests
        3. Make sure the code compiles/runs without errors
        4. Create a .gitignore appropriate for the project
        5. Never mark acceptance tests as ignored/skipped

        If there are previous review comments or test failures,
        address each one specifically.

        After writing code, provide a COMPLETE summary of everything
        implemented so far (not just what changed this round).
        The previous summary will be REPLACED by your output.
    """)
    prompt = f"{context}\n\nImplement the code based on the design. Write actual files."

    # Session semantics: engine passes session_id or resume_id via context
    session_id = request.context.get("session_id")
    resume_id = request.context.get("resume_id")

    result, usage = await self._run_claude_with_usage(
        prompt, cwd=cwd, system_prompt=system,
        session_id=session_id, resume_id=resume_id)
    files_changed = await self._get_files_changed(cwd or "")
    return DevelopOutput(summary=result, files_changed=files_changed, usage=usage)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/agents/test_claude_code.py::test_develop_passes_session_id tests/agents/test_claude_code.py::test_develop_passes_resume_id tests/agents/test_claude_code.py::test_develop_no_session_by_default -v`
Expected: PASS

- [ ] **Step 6: Run full agent tests**

Run: `python -m pytest tests/agents/test_claude_code.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/shadowcoder/agents/claude_code.py tests/agents/test_claude_code.py
git commit -m "feat: session_id/resume_id support in agent develop()"
```

Note: `base.py` does NOT need changes — `develop()` signature stays `(self, request: AgentRequest) -> DevelopOutput`. Session info flows through `request.context`, not method params. This keeps the abstract interface stable.

---

### Task 7: C — Engine wires session resume on gate fail

**Files:**
- Modify: `src/shadowcoder/core/engine.py:524-627` (`_run_develop_cycle`)
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_integration.py`, add:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integration.py::TestSessionResume -v`
Expected: FAIL — context has no session_id/resume_id

- [ ] **Step 3: Wire session management into `_run_develop_cycle`**

In `src/shadowcoder/core/engine.py`, modify `_run_develop_cycle`. Add `import uuid` at top of file. Then modify the method:

```python
async def _run_develop_cycle(self, issue, task):
    """Develop cycle: develop -> gate -> review -> repeat or done."""
    import uuid
    max_rounds = self.config.get_max_review_rounds()
    action_label = "Develop"
    section_key = "开发步骤"
    review_section_key = "Dev Review"

    proposed_tests = self._get_gate_tests(issue.id)

    try:
        gate_fail_count = 0
        last_gate_output = ""
        # Session management: new session per "fresh" develop, resume on gate fail
        current_session_id = str(uuid.uuid4())
        use_resume = False  # first call in a session uses session_id

        for round_num in range(1, max_rounds + 1):
            issue = self.issue_store.get(issue.id)
            if issue.status != IssueStatus.DEVELOPING:
                self.issue_store.transition_status(issue.id, IssueStatus.DEVELOPING)
            issue = self.issue_store.get(issue.id)
            self._log(issue.id, f"{action_label} R{round_num} 开始")
            await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

            agent = self.agents.get(issue.assignee or "default")
            latest_review = self._get_latest_review(issue.id, review_section_key)

            # Build context with session info
            ctx = {
                "worktree_path": task.worktree_path,
                "latest_review": latest_review,
                "feedback_summary": self._format_feedback_for_agent(issue.id),
                "acceptance_tests": self._format_acceptance_tests_for_developer(issue.id),
                "gate_output": self._truncate_output(last_gate_output) if last_gate_output else "",
            }
            if use_resume:
                ctx["resume_id"] = current_session_id
            else:
                ctx["session_id"] = current_session_id

            request = AgentRequest(action="develop", issue=issue, context=ctx)
            output = await agent.develop(request)
            # After first call, any gate-fail retry resumes this session
            use_resume = True

            # ... rest of the round logic (tracking, budget, gate, review) unchanged ...
```

After the review retry decision (the `retry` branch at end of review), reset the session:

```python
            # retry — start fresh session for next develop round
            # (review feedback may change direction)
            current_session_id = str(uuid.uuid4())
            use_resume = False
            # ... existing logging code ...
```

The gate-fail `continue` path does NOT reset the session — `use_resume` stays `True`, so next loop iteration will resume.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_integration.py::TestSessionResume -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/test_integration.py
git commit -m "feat: session resume on gate fail in develop cycle"
```

---

### Task 8: Final — Full regression and cleanup

- [ ] **Step 1: Run complete test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Verify no regressions in existing integration test scenarios**

Run: `python -m pytest tests/test_integration.py -v --tb=short`
Expected: All existing test classes (TestHappyPath, TestDesignPhase, TestDevelopPhase, TestErrorRecovery, TestBudget, TestCancelCleanup, TestListInfo, TestBreakpointRecovery, TestVersionArchive, TestEvents, TestMultipleIssues, TestFilePersistence, TestIterate) still pass.

- [ ] **Step 3: Commit any remaining fixes**

Only if needed.

- [ ] **Step 4: Final commit with all changes (if any)**

```bash
git status
# If there are uncommitted changes, stage specific files:
git add src/shadowcoder/ tests/
git commit -m "chore: final cleanup for harness improvements"
```
