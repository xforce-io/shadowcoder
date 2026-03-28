# BLOCKED State Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the conflated `approve` command into `approve` (close issue) and `unblock` (continue automatic flow), with structured blocked metadata on the Issue model.

**Architecture:** Add `blocked_reason` and `blocked_from` fields to Issue, refactor all BLOCKED transitions through a `_block_issue` helper, add `CMD_UNBLOCK` message type, implement `_on_unblock` handler, narrow `resume` to reject BLOCKED issues, update CLI and info output.

**Tech Stack:** Python, pytest, existing Engine/IssueStore/MessageBus abstractions

---

## File Structure

| File | Changes |
|------|---------|
| `src/shadowcoder/core/models.py` | Add `blocked_reason`, `blocked_from` to Issue; add reason constants |
| `src/shadowcoder/core/issue_store.py` | Persist/load new fields in YAML frontmatter; add `block_issue` and `unblock_issue` helpers |
| `src/shadowcoder/core/bus.py` | Add `CMD_UNBLOCK` message type |
| `src/shadowcoder/core/engine.py` | Add `_block_issue` helper, `_on_unblock` handler; refactor all BLOCKED sites; update `_on_approve`, `_on_resume`, `_on_info` |
| `scripts/run_real.py` | Add `unblock` CLI command |
| `tests/core/test_engine.py` | Tests for unblock, revised approve, revised resume, _block_issue |
| `tests/core/test_issue_store.py` | Tests for blocked field persistence |

---

### Task 1: Add blocked fields to Issue model and reason constants

**Files:**
- Modify: `src/shadowcoder/core/models.py:51-61`
- Test: `tests/core/test_issue_store.py`

- [ ] **Step 1: Write tests for the new fields**

In `tests/core/test_issue_store.py`, add tests verifying that `blocked_reason` and `blocked_from` are persisted and loaded correctly:

```python
def test_blocked_fields_persist(store):
    """blocked_reason and blocked_from round-trip through save/load."""
    from shadowcoder.core.models import IssueStatus
    issue = store.create("test blocked fields")
    issue.blocked_reason = "acceptance_script_bug"
    issue.blocked_from = IssueStatus.DEVELOPING
    store.save(issue)

    loaded = store.get(issue.id)
    assert loaded.blocked_reason == "acceptance_script_bug"
    assert loaded.blocked_from == IssueStatus.DEVELOPING


def test_blocked_fields_default_none(store):
    """New issues have None for blocked fields."""
    issue = store.create("test defaults")
    loaded = store.get(issue.id)
    assert loaded.blocked_reason is None
    assert loaded.blocked_from is None


def test_blocked_fields_clear(store):
    """Setting blocked fields back to None persists correctly."""
    from shadowcoder.core.models import IssueStatus
    issue = store.create("test clear")
    issue.blocked_reason = "budget_exceeded"
    issue.blocked_from = IssueStatus.DESIGNING
    store.save(issue)

    issue = store.get(issue.id)
    issue.blocked_reason = None
    issue.blocked_from = None
    store.save(issue)

    loaded = store.get(issue.id)
    assert loaded.blocked_reason is None
    assert loaded.blocked_from is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_issue_store.py::test_blocked_fields_persist tests/core/test_issue_store.py::test_blocked_fields_default_none tests/core/test_issue_store.py::test_blocked_fields_clear -v`
Expected: FAIL — fields don't exist yet.

- [ ] **Step 3: Add fields to Issue and constants to models.py**

In `src/shadowcoder/core/models.py`, add the reason constants before the Issue class, and add the two new fields:

```python
# --- Blocked reason constants ---
BLOCKED_BUDGET = "budget_exceeded"
BLOCKED_MAX_ROUNDS = "max_review_rounds"
BLOCKED_ACCEPTANCE_WEAK = "acceptance_too_weak"
BLOCKED_ACCEPTANCE_CONFIRMED = "acceptance_confirmed"
BLOCKED_ACCEPTANCE_BUG = "acceptance_script_bug"
BLOCKED_LOW_FEASIBILITY = "low_feasibility"


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
    blocked_reason: str | None = None
    blocked_from: IssueStatus | None = None
```

- [ ] **Step 4: Update IssueStore._save to persist blocked fields**

In `src/shadowcoder/core/issue_store.py`, update the `_save` method to include the new fields in the YAML frontmatter:

```python
    def _save(self, issue: Issue) -> None:
        issue_dir = self._issue_dir(issue.id)
        issue_dir.mkdir(parents=True, exist_ok=True)
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
            blocked_reason=issue.blocked_reason,
            blocked_from=issue.blocked_from.value if issue.blocked_from else None,
        )
        path = issue_dir / "issue.md"
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
```

- [ ] **Step 5: Update IssueStore.get to load blocked fields**

In `src/shadowcoder/core/issue_store.py`, update the `get` method:

```python
    def get(self, issue_id: int) -> Issue:
        path = self._issue_dir(issue_id) / "issue.md"
        if not path.exists():
            raise FileNotFoundError(f"Issue {issue_id} not found: {path}")
        post = frontmatter.load(str(path))
        blocked_from_raw = post.get("blocked_from")
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
            blocked_reason=post.get("blocked_reason"),
            blocked_from=IssueStatus(blocked_from_raw) if blocked_from_raw else None,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_issue_store.py -v`
Expected: All PASS

- [ ] **Step 7: Run full test suite for regressions**

Run: `python -m pytest tests/ -v`
Expected: All PASS (new fields default to None, no existing behavior changes)

- [ ] **Step 8: Commit**

```bash
git add src/shadowcoder/core/models.py src/shadowcoder/core/issue_store.py tests/core/test_issue_store.py
git commit -m "feat(models): add blocked_reason and blocked_from fields to Issue

Add structured metadata for BLOCKED state: reason constant and the
status to restore on unblock. Persisted in YAML frontmatter, backward
compatible (defaults to None)."
```

---

### Task 2: Add CMD_UNBLOCK message type

**Files:**
- Modify: `src/shadowcoder/core/bus.py:11-22`

- [ ] **Step 1: Add CMD_UNBLOCK to MessageType enum**

In `src/shadowcoder/core/bus.py`, add after `CMD_APPROVE`:

```python
    CMD_UNBLOCK = "cmd.unblock"
```

- [ ] **Step 2: Run existing tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS (adding an enum value breaks nothing)

- [ ] **Step 3: Commit**

```bash
git add src/shadowcoder/core/bus.py
git commit -m "feat(bus): add CMD_UNBLOCK message type"
```

---

### Task 3: Add _block_issue helper and refactor all BLOCKED entry points

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (8 BLOCKED entry points)
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write test for _block_issue**

```python
@pytest.mark.asyncio
async def test_block_issue_sets_metadata(bus, config, store, task_mgr):
    """_block_issue sets blocked_reason and blocked_from on the issue."""
    from shadowcoder.core.models import BLOCKED_ACCEPTANCE_BUG
    agent = _make_mock_agent()
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)
    engine = make_engine(bus, store, task_mgr, reg, config)

    issue = store.create("Test block metadata")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    task = MagicMock()
    task.task_id = "t1"
    task.status = TaskStatus.RUNNING

    await engine._block_issue(1, task, BLOCKED_ACCEPTANCE_BUG)

    issue = store.get(1)
    assert issue.status == IssueStatus.BLOCKED
    assert issue.blocked_reason == BLOCKED_ACCEPTANCE_BUG
    assert issue.blocked_from == IssueStatus.DEVELOPING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_engine.py::test_block_issue_sets_metadata -v`
Expected: FAIL — `_block_issue` doesn't exist yet.

- [ ] **Step 3: Implement _block_issue**

In `engine.py`, add near the existing `_on_approve` / `_on_resume` methods:

```python
    async def _block_issue(self, issue_id: int, task, reason: str,
                           from_status: IssueStatus | None = None,
                           event_reason: str = "") -> None:
        """Transition to BLOCKED with structured metadata."""
        issue = self.issue_store.get(issue_id)
        if from_status is None:
            from_status = issue.status
        issue.blocked_reason = reason
        issue.blocked_from = from_status
        issue.status = IssueStatus.BLOCKED
        # Validate transition is allowed
        from shadowcoder.core.models import VALID_TRANSITIONS
        if IssueStatus.BLOCKED not in VALID_TRANSITIONS.get(from_status, set()):
            # Some paths go through FAILED first; just save directly
            pass
        self.issue_store.save(issue)
        if task:
            task.status = TaskStatus.FAILED
        if event_reason:
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                "issue_id": issue_id,
                "task_id": task.task_id if task else None,
                "reason": event_reason}))
```

Note: Some existing BLOCKED paths go through `transition_status(FAILED)` then `transition_status(BLOCKED)`. The `_block_issue` helper uses `save()` directly (bypassing transition validation) since it's setting metadata atomically. The `from_status` captures the status *before* the BLOCKED transition (not FAILED).

- [ ] **Step 4: Refactor all 8 BLOCKED entry points**

Replace each inline BLOCKED transition with a call to `_block_issue`. Here are the 8 sites:

**Site 1: Design budget exceeded (line ~736)**

Before:
```python
self._log(issue.id, f"预算超限 → BLOCKED\n{summary}")
self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
task.status = TaskStatus.FAILED
await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
    "issue_id": issue.id, "task_id": task.task_id,
    "reason": f"budget exceeded: {summary}"}))
```

After:
```python
self._log(issue.id, f"预算超限 → BLOCKED\n{summary}")
await self._block_issue(issue.id, task, BLOCKED_BUDGET,
    from_status=IssueStatus.DESIGNING,
    event_reason=f"budget exceeded: {summary}")
```

**Site 2: Design max review rounds (line ~796)**

Before:
```python
self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
self._log(issue.id, f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
task.status = TaskStatus.FAILED
await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
    "issue_id": issue.id, "task_id": task.task_id,
    "reason": f"review not passed after {max_rounds} rounds"}))
```

After:
```python
self._log(issue.id, f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
await self._block_issue(issue.id, task, BLOCKED_MAX_ROUNDS,
    event_reason=f"review not passed after {max_rounds} rounds")
```

**Site 3: Acceptance script too weak (line ~946)**

Before:
```python
self._log(issue.id, "Pre-gate: acceptance script still PASS after "
    f"{max_attempts} attempts → BLOCKED")
self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
    "issue_id": issue.id, "task_id": task.task_id,
    "reason": "acceptance tests already pass — criteria too weak"}))
```

After:
```python
self._log(issue.id, "Pre-gate: acceptance script still PASS after "
    f"{max_attempts} attempts → BLOCKED")
await self._block_issue(issue.id, task, BLOCKED_ACCEPTANCE_WEAK,
    from_status=IssueStatus.APPROVED,
    event_reason="acceptance tests already pass — criteria too weak")
```

**Site 4: Acceptance confirmed (line ~973)**

Before:
```python
self._log(issue.id, "Acceptance tests xfail 确认 ✓ → BLOCKED，等待人类确认")
self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
    {"issue_id": issue.id, "status": "blocked",
     "reason": "acceptance_confirmed"}))
```

After:
```python
self._log(issue.id, "Acceptance tests xfail 确认 ✓ → BLOCKED，等待人类确认")
await self._block_issue(issue.id, task, BLOCKED_ACCEPTANCE_CONFIRMED,
    from_status=IssueStatus.APPROVED,
    event_reason="acceptance_confirmed")
```

**Site 5: Develop budget exceeded (line ~1032)**

After:
```python
self._log(issue.id, f"预算超限 → BLOCKED\n{summary}")
await self._block_issue(issue.id, task, BLOCKED_BUDGET,
    from_status=IssueStatus.DEVELOPING,
    event_reason=f"budget exceeded: {summary}")
```

**Site 6: Acceptance script bug — reviewer flagged (line ~1075)**

Before:
```python
self._log(issue.id,
    "Reviewer 判定 acceptance script 有误 → BLOCKED，需人类介入修正验收标准")
self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
task.status = TaskStatus.FAILED
await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
    "issue_id": issue.id, "task_id": task.task_id,
    "reason": "acceptance script may be incorrect — reviewer flagged it"}))
```

After:
```python
self._log(issue.id,
    "Reviewer 判定 acceptance script 有误 → BLOCKED，需人类介入修正验收标准")
await self._block_issue(issue.id, task, BLOCKED_ACCEPTANCE_BUG,
    from_status=IssueStatus.DEVELOPING,
    event_reason="acceptance script may be incorrect — reviewer flagged it")
```

**Site 7: Develop max review rounds (line ~1201)**

After:
```python
self._log(issue.id, f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
await self._block_issue(issue.id, task, BLOCKED_MAX_ROUNDS,
    event_reason=f"review not passed after {max_rounds} rounds")
```

**Site 8: Preflight low feasibility (line ~1245)**

Before:
```python
self._log(issue.id, "Preflight: feasibility=low → BLOCKED，等待人类确认")
self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
    "issue_id": issue.id,
    ...
```

After:
```python
self._log(issue.id, "Preflight: feasibility=low → BLOCKED，等待人类确认")
await self._block_issue(issue.id, task, BLOCKED_LOW_FEASIBILITY,
    from_status=IssueStatus.CREATED,
    event_reason=f"Preflight: low feasibility — {pf_summary}")
```

Add imports at the top of engine.py:
```python
from shadowcoder.core.models import (
    BLOCKED_BUDGET, BLOCKED_MAX_ROUNDS, BLOCKED_ACCEPTANCE_WEAK,
    BLOCKED_ACCEPTANCE_CONFIRMED, BLOCKED_ACCEPTANCE_BUG,
    BLOCKED_LOW_FEASIBILITY,
)
```

- [ ] **Step 5: Run test and full suite**

Run: `python -m pytest tests/core/test_engine.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "refactor(engine): unify BLOCKED transitions through _block_issue helper

All 8 BLOCKED entry points now call _block_issue() which atomically
sets blocked_reason, blocked_from, and status. This replaces scattered
inline transition_status calls and enables structured unblock recovery."
```

---

### Task 4: Implement _on_unblock handler

**Files:**
- Modify: `src/shadowcoder/core/engine.py`
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write tests for unblock**

```python
@pytest.mark.asyncio
async def test_unblock_restores_develop(bus, config, store, task_mgr, tmp_repo):
    """unblock restores blocked_from status and re-enters develop cycle."""
    from shadowcoder.core.models import BLOCKED_ACCEPTANCE_BUG
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(
            comments=[], reviewer="mock")),
    )
    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = make_engine(bus, store, task_mgr, reg, config, repo_path=str(tmp_repo))

    issue = store.create("Test unblock")
    store.update_section(1, "需求", "implement foo")
    store.update_section(1, "设计", "design foo")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    # Manually block with metadata
    issue = store.get(1)
    issue.blocked_reason = BLOCKED_ACCEPTANCE_BUG
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    # Write acceptance script that passes (so develop can complete)
    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text("#!/bin/bash\nset -euo pipefail\nexit 0\n")

    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff")

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {"issue_id": 1}))

    issue = store.get(1)
    # Should have completed the develop cycle
    assert issue.status == IssueStatus.DONE
    assert issue.blocked_reason is None
    assert issue.blocked_from is None


@pytest.mark.asyncio
async def test_unblock_with_message_logs(bus, config, store, task_mgr, tmp_repo):
    """unblock message is written to issue log."""
    from shadowcoder.core.models import BLOCKED_MAX_ROUNDS
    mock_agent = _make_mock_agent(
        preflight=AsyncMock(return_value=PreflightOutput(
            feasibility="high", estimated_complexity="low")),
        develop=AsyncMock(return_value=DevelopOutput(summary="code")),
        review=AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock")),
    )
    reg = MagicMock()
    reg.get = MagicMock(return_value=mock_agent)
    engine = make_engine(bus, store, task_mgr, reg, config, repo_path=str(tmp_repo))

    issue = store.create("Test unblock msg")
    store.update_section(1, "需求", "foo")
    store.update_section(1, "设计", "bar")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    issue = store.get(1)
    issue.blocked_reason = BLOCKED_MAX_ROUNDS
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    acc_path = Path(store.base) / "0001" / "acceptance.sh"
    acc_path.write_text("#!/bin/bash\nexit 0\n")
    engine._gate_check = AsyncMock(return_value=(True, "ok", ""))
    engine._get_code_diff = AsyncMock(return_value="diff")

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {
        "issue_id": 1, "message": "fixed acceptance script"}))

    log = store.get_log(1)
    assert "fixed acceptance script" in log


@pytest.mark.asyncio
async def test_unblock_rejects_non_blocked(bus, config, store, task_mgr):
    """unblock on non-BLOCKED issue emits error."""
    engine = make_engine(bus, store, task_mgr, MagicMock(), config)
    store.create("Test not blocked")

    errors = []
    bus.subscribe(MessageType.EVT_ERROR, lambda m: errors.append(m))

    await bus.publish(Message(MessageType.CMD_UNBLOCK, {"issue_id": 1}))
    assert len(errors) == 1
    assert "not BLOCKED" in errors[0].payload["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_engine.py::test_unblock_restores_develop tests/core/test_engine.py::test_unblock_with_message_logs tests/core/test_engine.py::test_unblock_rejects_non_blocked -v`
Expected: FAIL — `CMD_UNBLOCK` handler doesn't exist.

- [ ] **Step 3: Implement _on_unblock**

In `engine.py`, in the `__init__` method, add subscription after the approve handler:

```python
self.bus.subscribe(MessageType.CMD_UNBLOCK, self._on_unblock)
```

Add the handler near `_on_approve`:

```python
    async def _on_unblock(self, msg):
        """Unblock: restore pre-BLOCKED state and re-enter cycle."""
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is not BLOCKED"}))
            return

        message = msg.payload.get("message", "")
        from_status = issue.blocked_from
        reason = issue.blocked_reason

        # Log the unblock
        if message:
            self._log(issue.id, f"人类介入: unblock ({reason}) — {message}")
        else:
            self._log(issue.id, f"人类介入: unblock ({reason})")

        # Clear blocked metadata and restore status
        issue.blocked_reason = None
        issue.blocked_from = None
        if from_status:
            issue.status = from_status
        else:
            # Fallback for legacy issues without blocked_from
            stage = self._infer_blocked_stage(issue)
            if stage == "develop":
                issue.status = IssueStatus.APPROVED
            elif stage == "design":
                issue.status = IssueStatus.CREATED
            else:
                await self.bus.publish(Message(MessageType.EVT_ERROR,
                    {"message": f"cannot infer stage for issue #{issue.id}"}))
                return
        self.issue_store.save(issue)

        # Store unblock message for agent context
        if message:
            self._unblock_message = message

        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": issue.status.value}))

        # Auto-trigger the corresponding cycle
        stage = self._infer_blocked_stage(self.issue_store.get(issue.id))
        if stage == "develop":
            await self._on_develop(msg)
        elif stage == "design":
            await self._on_design(msg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_engine.py::test_unblock_restores_develop tests/core/test_engine.py::test_unblock_with_message_logs tests/core/test_engine.py::test_unblock_rejects_non_blocked -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/core/test_engine.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "feat(engine): add _on_unblock handler for BLOCKED recovery

Unblock restores the issue to its pre-BLOCKED state using the stored
blocked_from field, logs the human message, and auto-triggers the
corresponding cycle. Falls back to _infer_blocked_stage for legacy
issues without blocked_from."
```

---

### Task 5: Update _on_approve to clear blocked fields

**Files:**
- Modify: `src/shadowcoder/core/engine.py:1395-1406`
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write test**

```python
@pytest.mark.asyncio
async def test_approve_clears_blocked_fields(bus, config, store, task_mgr):
    """approve clears blocked_reason and blocked_from."""
    from shadowcoder.core.models import BLOCKED_MAX_ROUNDS
    agent = _make_mock_agent()
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)
    engine = make_engine(bus, store, task_mgr, reg, config)

    store.create("Test approve clear")
    store.update_section(1, "开发步骤", "some code")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    issue = store.get(1)
    issue.blocked_reason = BLOCKED_MAX_ROUNDS
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))

    issue = store.get(1)
    assert issue.status == IssueStatus.DONE
    assert issue.blocked_reason is None
    assert issue.blocked_from is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_engine.py::test_approve_clears_blocked_fields -v`
Expected: FAIL — approve doesn't clear the fields yet.

- [ ] **Step 3: Update _on_approve**

```python
    async def _on_approve(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is not BLOCKED"}))
            return
        stage = self._infer_blocked_stage(issue)
        next_status = IssueStatus.DONE if stage == "develop" else IssueStatus.APPROVED
        # Clear blocked metadata
        issue.blocked_reason = None
        issue.blocked_from = None
        issue.status = next_status
        self.issue_store.save(issue)
        self._log(issue.id, f"人类介入: approve → {next_status.value}")
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": next_status.value}))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/core/test_engine.py -v`
Expected: All PASS (including existing `test_approve_blocked`)

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "feat(engine): approve clears blocked metadata on transition"
```

---

### Task 6: Narrow resume to reject BLOCKED issues

**Files:**
- Modify: `src/shadowcoder/core/engine.py:1379-1393`
- Test: `tests/core/test_engine.py`

- [ ] **Step 1: Write test**

```python
@pytest.mark.asyncio
async def test_resume_rejects_blocked(bus, config, store, task_mgr):
    """resume on BLOCKED issue returns error suggesting unblock."""
    from shadowcoder.core.models import BLOCKED_MAX_ROUNDS
    engine = make_engine(bus, store, task_mgr, MagicMock(), config)
    store.create("Test resume blocked")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)
    store.transition_status(1, IssueStatus.DEVELOPING)

    issue = store.get(1)
    issue.blocked_reason = BLOCKED_MAX_ROUNDS
    issue.blocked_from = IssueStatus.DEVELOPING
    issue.status = IssueStatus.BLOCKED
    store.save(issue)

    errors = []
    bus.subscribe(MessageType.EVT_ERROR, lambda m: errors.append(m))

    await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": 1}))

    assert len(errors) == 1
    assert "unblock" in errors[0].payload["message"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_engine.py::test_resume_rejects_blocked -v`
Expected: FAIL — current `resume` accepts BLOCKED issues.

- [ ] **Step 3: Update _on_resume**

```python
    async def _on_resume(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status == IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is BLOCKED. Use `unblock` to continue or `approve` to accept current state."}))
            return
        # Resume from non-BLOCKED active states (e.g., process interrupted)
        action = self._infer_blocked_stage(issue)
        self._log(issue.id, f"人类介入: resume → 重跑 {action}")
        if action == "design":
            await self._on_design(msg)
        elif action == "develop":
            await self._on_develop(msg)
        else:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"cannot infer stage for issue #{issue.id}"}))
```

- [ ] **Step 4: Update existing test_resume_blocked_design**

The existing `test_resume_blocked_design` test uses `resume` on a BLOCKED issue. It needs to use `CMD_UNBLOCK` instead:

```python
async def test_resume_blocked_design(bus, store, task_mgr, config):
    # ... (same setup as before) ...
    assert store.get(1).status == IssueStatus.BLOCKED

    # Use unblock instead of resume
    await bus.publish(Message(MessageType.CMD_UNBLOCK, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/core/test_engine.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/engine.py tests/core/test_engine.py
git commit -m "feat(engine): resume rejects BLOCKED issues, suggests unblock

Resume is now for non-BLOCKED active states only (e.g., process crash
recovery). BLOCKED issues must use unblock or approve."
```

---

### Task 7: Add unblock CLI command and update info output

**Files:**
- Modify: `scripts/run_real.py:217-224`
- Modify: `src/shadowcoder/core/engine.py` (`_on_info`)

- [ ] **Step 1: Add unblock command to CLI**

In `scripts/run_real.py`, after the `approve` handler:

```python
    elif command == "unblock":
        issue_id = int(args[0])
        message = " ".join(args[1:]) if len(args) > 1 else ""
        payload = {"issue_id": issue_id}
        if message:
            payload["message"] = message
        await bus.publish(Message(MessageType.CMD_UNBLOCK, payload))
```

Add the import for `CMD_UNBLOCK` if `MessageType` is imported selectively (check the import at top of file).

- [ ] **Step 2: Update _on_info to show blocked metadata**

In `engine.py`, update `_on_info`:

```python
    async def _on_info(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        info = {
            "id": issue.id, "title": issue.title,
            "status": issue.status.value, "priority": issue.priority,
            "tags": issue.tags, "assignee": issue.assignee,
            "sections": list(issue.sections.keys()),
        }
        if issue.blocked_reason:
            info["blocked_reason"] = issue.blocked_reason
        if issue.blocked_from:
            info["blocked_from"] = issue.blocked_from.value
        await self.bus.publish(Message(MessageType.EVT_ISSUE_INFO, {"issue": info}))
```

- [ ] **Step 3: Update CLAUDE.md with unblock command**

In `CLAUDE.md`, in the "Managing Issues" section, add:

```bash
# Unblock a BLOCKED issue (after fixing the blocker)
python scripts/run_real.py ~/dev/github/<name> unblock 1 "fixed acceptance script"
```

And update the `approve` description to clarify:

```bash
# Approve a BLOCKED issue (accept current state, skip remaining work)
python scripts/run_real.py ~/dev/github/<name> approve 1
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/run_real.py src/shadowcoder/core/engine.py CLAUDE.md
git commit -m "feat(cli): add unblock command and show blocked reason in info

unblock <id> [message] restores a BLOCKED issue and continues the
automatic flow. info now shows blocked_reason and blocked_from when
the issue is BLOCKED."
```

---

### Task 8: Update run command BLOCKED handling

The `run` command currently does `issue.status = IssueStatus.APPROVED` when it encounters a BLOCKED develop issue. This should use `unblock` semantics instead.

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (`_on_run`, around line 1328)

- [ ] **Step 1: Read the current run handler**

Read: `src/shadowcoder/core/engine.py` the `_on_run` section that handles BLOCKED.

- [ ] **Step 2: Update run to use blocked_from**

```python
        # BLOCKED → use blocked_from to restore
        if issue.status == IssueStatus.BLOCKED:
            from_status = issue.blocked_from
            if from_status:
                self._log(issue_id,
                    f"run 恢复: BLOCKED ({issue.blocked_reason}) → {from_status.value}")
                issue.blocked_reason = None
                issue.blocked_from = None
                issue.status = from_status
                self.issue_store.save(issue)
            else:
                # Legacy fallback
                stage = self._infer_blocked_stage(issue)
                if stage == "develop":
                    self._log(issue_id, "run 恢复: BLOCKED → 继续 develop")
                    issue.status = IssueStatus.APPROVED
                    self.issue_store.save(issue)
                elif stage == "design":
                    self._log(issue_id, "run 恢复: BLOCKED → 继续 design")
                    issue.status = IssueStatus.CREATED
                    self.issue_store.save(issue)
```

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/shadowcoder/core/engine.py
git commit -m "feat(engine): run command uses blocked_from for BLOCKED recovery

Uses structured blocked_from field when available, falls back to
_infer_blocked_stage for legacy issues."
```

---

### Verification

After all tasks are complete:

- [ ] Run full test suite: `python -m pytest tests/ -v`
- [ ] Manual smoke test: create issue → BLOCKED → `info` shows reason → `unblock` continues → `approve` closes
- [ ] Verify backward compat: old issues without `blocked_reason`/`blocked_from` still work with `unblock`
