# BLOCKED State Semantics: Unblock vs Approve

## Problem

`approve` currently means both "human accepts current result, close the issue" and "human
has addressed the blocker, let the system continue." These are fundamentally different
operations conflated into one command.

Real scenario: issue BLOCKED due to acceptance script bug. Human fixes the script, wants
the system to continue iterating. But `approve` transitions to DONE, terminating the
issue. The only alternative is `resume`, which re-enters the cycle but has unclear
semantics around what changed.

## Design

### New Command: `unblock`

```bash
python scripts/run_real.py ~/repo unblock <id> ["optional message"]
```

**Semantics:** Human has addressed the blocking reason. Restore the issue to its
pre-BLOCKED state and automatically re-enter the corresponding cycle.

**Behavior:**
1. Validate `status == BLOCKED`
2. Restore status to `blocked_from` (stored when BLOCKED was set)
3. If message provided, write to issue log and pass as `human_intervention` context to next agent call
4. Clear `blocked_reason` and `blocked_from`
5. Auto-trigger the corresponding cycle (design or develop)

### Revised Command: `approve`

```bash
python scripts/run_real.py ~/repo approve <id>
```

**Semantics unchanged:** Human accepts current work, skip remaining process, close the issue.

**Behavior:**
- BLOCKED during develop → DONE
- BLOCKED during design → APPROVED
- Clears `blocked_reason` and `blocked_from`

### Revised Command: `resume`

```bash
python scripts/run_real.py ~/repo resume <id>
```

**Semantics narrowed:** Resume execution from a non-BLOCKED active state (e.g., process
interrupted, CLI crashed).

**Behavior:**
- If `status == BLOCKED`, reject with message: "Issue is BLOCKED. Use `unblock` to
  continue or `approve` to accept current state."
- Otherwise, infer current stage and re-enter cycle (existing behavior)

### Data Model Changes

Add two fields to `Issue` in `models.py`:

```python
@dataclass
class Issue:
    ...
    blocked_reason: str | None = None
    blocked_from: IssueStatus | None = None
```

Both fields are set atomically when transitioning to BLOCKED, cleared on unblock/approve.

### Blocked Reason Constants

Defined in `models.py` as module-level constants:

```python
BLOCKED_BUDGET = "budget_exceeded"
BLOCKED_MAX_ROUNDS = "max_review_rounds"
BLOCKED_ACCEPTANCE_WEAK = "acceptance_too_weak"
BLOCKED_ACCEPTANCE_CONFIRMED = "acceptance_confirmed"
BLOCKED_ACCEPTANCE_BUG = "acceptance_script_bug"
BLOCKED_LOW_FEASIBILITY = "low_feasibility"
```

### Engine Helper: `_block_issue`

All 6 BLOCKED entry points refactored to call a single helper:

```python
async def _block_issue(self, issue_id: int, task, reason: str,
                       from_status: IssueStatus | None = None,
                       event_reason: str = "") -> None:
```

This method:
1. Records `blocked_reason` and `blocked_from` (auto-inferred from current status if not passed)
2. Transitions to BLOCKED
3. Logs with structured reason
4. Publishes EVT_TASK_FAILED with reason

### Unblock Message Flow

When `unblock` is called with a message:

1. Message written to issue log: `[timestamp] 人类介入: unblock — {message}`
2. Message stored temporarily so the next agent call receives it as `human_intervention` context
3. Agent sees: `--- Human Intervention ---\n{message}` in its prompt

This is critical for scenarios like `acceptance_script_bug` where the agent needs to know
the acceptance script was modified.

### Issue Serialization

`blocked_reason` and `blocked_from` are persisted in the issue YAML frontmatter:

```yaml
---
status: blocked
blocked_reason: acceptance_script_bug
blocked_from: developing
---
```

Cleared (set to null/omitted) when issue leaves BLOCKED state.

### State Transition Summary

```
BLOCKED --unblock--> blocked_from (DEVELOPING/DESIGNING/etc) --> auto-execute cycle
BLOCKED --approve--> DONE or APPROVED (depending on stage)
BLOCKED --cancel---> CANCELLED

Non-BLOCKED --resume--> re-enter current cycle
BLOCKED    --resume--> ERROR: use unblock or approve
```

### BLOCKED Entry Points (all 6 refactored)

| Location | Reason Constant | `blocked_from` |
|----------|----------------|----------------|
| Design budget exceeded | `BLOCKED_BUDGET` | DESIGNING |
| Develop budget exceeded | `BLOCKED_BUDGET` | DEVELOPING |
| Design max review rounds | `BLOCKED_MAX_ROUNDS` | DESIGNING |
| Develop max review rounds | `BLOCKED_MAX_ROUNDS` | DEVELOPING |
| Acceptance script too weak | `BLOCKED_ACCEPTANCE_WEAK` | APPROVED |
| Acceptance confirmed, await human | `BLOCKED_ACCEPTANCE_CONFIRMED` | APPROVED |
| Reviewer flagged acceptance bug | `BLOCKED_ACCEPTANCE_BUG` | DEVELOPING |
| Preflight low feasibility | `BLOCKED_LOW_FEASIBILITY` | CREATED |

### CLI Output Improvements

`info` command shows blocked reason when applicable:

```
Issue #1: Expand calc evaluator
Status: BLOCKED (acceptance_script_bug)
Blocked from: DEVELOPING
```

### Testing Strategy

- Unit tests for `_block_issue` helper (sets fields correctly)
- Unit tests for `_on_unblock` (restores state, clears fields, triggers cycle)
- Unit tests for revised `_on_approve` (clears fields)
- Unit tests for revised `_on_resume` (rejects BLOCKED)
- Integration test: BLOCKED → unblock with message → develop cycle receives human_intervention
- Integration test: BLOCKED → approve → DONE
- Backward compatibility: issues without `blocked_reason`/`blocked_from` fields still work (fallback to `_infer_blocked_stage`)
