# Metric Gate & Acceptance Restructure — Design Spec

## Problem

ShadowCoder's develop cycle has two gaps:

1. **No early bail-out for bad optimization directions.** If a develop agent's approach produces catastrophically bad metrics (e.g., recall=5%), the system doesn't detect this until acceptance — which may not check metrics at all (agent-generated, unreliable). Wasted rounds on a dead-end direction.

2. **Acceptance runs every develop round.** Acceptance scripts can be expensive (10-minute walk-forward evaluations). Running them every round is wasteful. They belong at the end, not in every iteration.

## Solution Overview

Two changes:

1. **Metric gate**: Optional gate extension that reads `metrics.json` from the worktree after pytest passes. If configured metrics fall below a baseline threshold, the round's code is discarded and the develop agent retries with a fresh session and feedback. This is an early "wrong direction" detector, not a final quality check.

2. **Acceptance repositioning**: Move acceptance check from every develop round to after review passes. Acceptance becomes the final verification gate — runs once, can be expensive.

## Current Flow

```
for each round:
    develop → acceptance → gate (pytest) → review → (DONE or retry)
```

## New Flow

```
for each round:
    checkpoint = save_checkpoint()
    develop agent writes code
    → gate:
        1. pytest
        2. read metrics.json (if metric_gate configured)
           → below baseline: revert_to(checkpoint), reset session, feedback → retry
           → above baseline or not configured: continue
    → review
    → review fail: retry develop (same direction, resume session)

review pass:
    → acceptance (final requirements verification)
    → acceptance fail: back to develop loop
    → acceptance pass: DONE
```

## Configuration

```yaml
# Project or global config
metric_gate:
  recall: ">= 0.50"
  precision: ">= 0.20"
```

- Keys are metric names, values are threshold expressions (`>=`, `<=`, `>`, `<` followed by a number).
- Not configured → metric gate is skipped, current behavior unchanged.
- These are **baselines** (early bail-out), not final targets. Final targets live in the acceptance script.

## metrics.json Contract

- **Path**: `metrics.json` in worktree root. Fixed, not configurable.
- **Format**: Flat JSON object, string keys, numeric values.
  ```json
  {"recall": 0.4326, "precision": 0.50, "f1": 0.4639}
  ```
- **Producer**: Project code. The develop agent implements evaluation logic that writes this file. Engine never executes an evaluation command — it only reads the file.
- **Freshness**: `save_checkpoint` deletes `metrics.json` if it exists, ensuring each develop round starts without a stale file. If develop agent doesn't regenerate it, gate sees "file not present" — never reads stale data from a previous round.
- **When not present**: If `metric_gate` is configured but `metrics.json` doesn't exist after develop, treat as **normal gate failure** — code is preserved, agent continues fixing. This is NOT a "wrong direction" failure (no revert). The agent likely hasn't implemented the evaluation logic yet.
- **Stray file detection**: `metrics.json` is not a source file (`.json` extension), so existing stray-file detection (which filters by source extensions like `.py`, `.js`) does not flag it.
- **Validation**: All keys in `metric_gate` config must exist in the JSON. Values must be finite floats. Extra keys in JSON are ignored. Duplicate keys are rejected by JSON parsing naturally.

## Gate: Three Failure Modes

| | pytest fail / metrics.json missing | metric values below baseline |
|---|---|---|
| Meaning | Code bug / incomplete | Wrong direction |
| Code | Preserved, agent continues fixing | Reverted to pre-develop checkpoint |
| Session | Resume (same session) | Reset (new session ID) |
| Feedback | error output / "metrics.json not found" | "recall=0.05 far below baseline 0.50, try different approach" |
| Counter | Shared `gate_fail_count` | Separate `metric_gate_retries` (max configurable, default 3) |
| Exhausted | Escalate to reviewer | BLOCKED with reason `metric_gate_exhausted` |

**`metrics.json` missing or invalid** (malformed JSON, missing required keys, non-finite values) is treated as a normal gate failure — the code is incomplete, not wrong. The agent needs to implement or fix the evaluation logic that produces `metrics.json`.

## WorktreeManager Extension

Add code state management to the existing `WorktreeManager`:

```python
class WorktreeManager:
    # Existing
    async def ensure(self, repo_path, issue_id, title) -> str
    async def cleanup(self, repo_path, issue_id, delete_branch) -> None
    async def exists(self, repo_path, issue_id) -> bool

    # New: code state management
    async def save_checkpoint(self, worktree_path: str, label: str) -> str
        """Commit current worktree state as a checkpoint. Returns commit hash.

        Steps:
        1. Delete metrics.json if present (freshness guarantee)
        2. git add -A
        3. git commit --allow-empty -m 'checkpoint: {label}'
        4. Return commit hash

        Checkpoint commits are internal bookkeeping. They will appear in the
        branch history; the merge/PR workflow is responsible for squashing
        them if a clean history is desired (e.g., `git merge --squash`).
        Uses --allow-empty so checkpoints work even with no changes.
        """

    async def revert_to(self, worktree_path: str, checkpoint: str) -> bool
        """Hard reset to checkpoint commit. Returns success.

        Equivalent to: git reset --hard {checkpoint} && git clean -fd
        Scope: worktree only. Issue metadata (acceptance.sh, feedback.json,
        version archives) lives in issue_store, outside worktree — unaffected.
        Returns False if either git command fails (caller should BLOCKED).
        """

    async def current_head(self, worktree_path: str) -> str
        """Return current HEAD commit hash."""
```

## Acceptance Repositioning

### Before (current)

Acceptance runs inside every develop round, before gate:
```
develop → acceptance → gate → review
```

### After (new)

Acceptance runs once, after review passes:
```
develop → gate → review → [review pass] → acceptance → DONE
```

If acceptance fails after review pass:
1. **Session reset**: The develop agent gets a fresh session. Continuing the review-passed session would leave the agent in a "my code is done" mental model, conflicting with "acceptance says it's not done".
2. **Feedback reset**: Previously resolved feedback items are reset to unresolved — code changes to fix acceptance may invalidate earlier resolutions.
3. **Acceptance failure feedback**: The acceptance error output is passed to the next develop round as primary context, same as current behavior.

### Impact

- **Acceptance script generation** (`_run_acceptance_phase`): Unchanged. Still runs before the develop loop starts.
- **Pre-gate validation**: Unchanged. Acceptance script must FAIL on unchanged code before develop begins.
- **Acceptance execution timing**: Moves from inside `for round_num` loop to after `if decision == "pass"` / `"conditional_pass"`.
- **Expensive acceptance scripts**: Now viable. A 10-minute evaluation runs once, not every round.

## Metric Gate Feedback

When metric gate fails:
- Code is reverted (WorktreeManager.revert_to)
- Issue section (develop summary) is preserved — serves as "what this attempt tried"
- A feedback item is recorded with ID `F<next>` but tagged with `"source": "metric_gate"`. On each metric gate failure, previous metric_gate-sourced items are replaced (not accumulated). On metric gate pass, they are auto-resolved.
- The existing `_update_feedback` ID generation (`int(item["id"][1:])`) must filter to `F`-prefixed items only when computing `next_num`, to avoid conflicts with any future non-F ID schemes.

Feedback text includes: which metrics failed, actual values vs baselines, explicit instruction to try a different approach.

## Blocked Reasons

New constant:
```python
BLOCKED_METRIC_GATE = "metric_gate_exhausted"
```

Triggered when `metric_gate_retries` exceeds max (default 3). Unblock from this reason re-enters the develop loop normally (no special handling needed — code was already reverted, worktree is clean).

## Design Decisions

1. **Metric gate is a gate extension, not a new phase.** No new states in the state machine. Metric gate failure is a type of gate failure with different handling (revert vs preserve).

2. **metrics.json is produced by project code, not by engine.** Engine has zero knowledge of how evaluation works. It reads a file and compares numbers. Trust boundary: the file content is as trustworthy as the project's test suite — developer-written, reviewer-audited.

3. **Baselines, not targets.** metric_gate thresholds are low bars to catch catastrophic failures early. Final quality targets are enforced by acceptance (which can run expensive evaluations). This eliminates the "if acceptance checks it, why does metric gate check it too?" contradiction.

4. **Revert is scoped to worktree.** Issue section (develop summary) is intentionally preserved after revert — it documents what was tried. The next develop round sees "previous attempt summary + metric gate failure feedback" and knows to change direction.

5. **Acceptance moves to post-review.** This is a structural change to the develop cycle. Benefits: expensive acceptance runs once instead of every round. Trade-off: develop iterations no longer get acceptance feedback between rounds — but gate (pytest + metrics) provides sufficient iteration feedback.

6. **Acceptance script quality is the reviewer's responsibility.** The final metric targets live in the acceptance script, which is agent-generated. This is a known trust boundary — the reviewer is expected to verify that acceptance scripts actually test the requirements' quantitative targets. Metric gate provides a deterministic safety net (baselines), but does not replace the need for well-written acceptance scripts.

## What This Does NOT Do

- No new states in the state machine
- No stdout/stderr parsing for metrics
- No dependency on acceptance script output format
- No evaluation command execution by engine
- No changes to design phase or design review
- No changes to acceptance script generation logic
