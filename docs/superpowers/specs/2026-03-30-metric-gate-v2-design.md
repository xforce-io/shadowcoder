# Metric Gate v2 — Pareto Improvement Detection

## Problem

ShadowCoder's develop cycle has three gaps for optimization-type tasks:

1. **No stagnation detection.** If a develop agent doesn't make meaningful progress (e.g., ignores a requested feature change and just tunes parameters), the system can't tell. Gate passes, review passes, metrics stay the same — wasted rounds.

2. **No direction change mechanism.** When an approach isn't working, the system keeps iterating in the same worktree with the same codebase. There's no way to "abandon this direction and start fresh."

3. **Acceptance runs every develop round.** Acceptance scripts can be expensive (10-minute walk-forward evaluations). They belong at the end, not in every iteration.

## Solution Overview

Three changes:

1. **Pareto improvement detection**: After pytest passes, read `metrics.json` and compare against the previous round's metrics. If no Pareto improvement for `max_stagnant_rounds` consecutive rounds, mark the worktree as STAGNATED and start fresh in a new worktree.

2. **Metric targets as final goals**: When all configured metric targets are met, skip directly to review (optimization complete). Acceptance only checks functional correctness.

3. **Acceptance repositioning**: Move acceptance from every develop round to after review passes. Runs once as final verification.

## Key Concept: Pareto Improvement

A round is a **Pareto improvement** if:
- No configured metric got worse (within tolerance)
- At least one configured metric improved by a meaningful amount

```
R1: recall=0.79, precision=0.73
R2: recall=0.79, precision=0.74  → Pareto ✓ (precision up, recall same)
R2: recall=0.80, precision=0.71  → NOT Pareto (precision dropped)
R2: recall=0.79, precision=0.73  → NOT Pareto (nothing improved)
```

**Tolerance**: A metric is "same" if change < 1% (configurable). This prevents noise from counting as improvement.

## Configuration

```yaml
metric_gate:
  targets:
    recall: ">= 0.90"
    precision: ">= 0.50"
  max_stagnant_rounds: 2       # consecutive non-Pareto rounds → STAGNATED
  improvement_threshold: 0.01  # minimum delta to count as improvement (default 1%)
```

- Not configured → metric gate skipped, current behavior unchanged.
- `targets`: final optimization goals. All met → proceed to review.
- `max_stagnant_rounds`: how many non-improving rounds before abandoning direction. Default 2.

## metrics.json Contract

Same as v1 — unchanged:
- **Path**: `metrics.json` in worktree root. Fixed.
- **Format**: `{"recall": 0.79, "precision": 0.73}`
- **Producer**: Project code. Engine only reads.
- **Missing**: Treated as normal gate failure (code incomplete).
- **Validation**: Keys from `targets` must exist. Values must be finite floats.

## New Flow

```
for each round:
    develop agent writes code
    → gate (pytest)
    → read metrics.json (if metric_gate configured)
       → missing/invalid: normal gate fail (preserve code, retry)
       → all targets met: proceed to review (optimization done)
       → compare with previous round's metrics:
           → Pareto improvement: continue, proceed to review
           → NOT Pareto (round N of max_stagnant_rounds):
               log warning, continue to review anyway
           → NOT Pareto (round max_stagnant_rounds):
               → STAGNATED: preserve worktree, create new one, restart develop
    → review
    → review pass → acceptance (final, once) → DONE
```

## Stagnation Handling

When `max_stagnant_rounds` consecutive non-Pareto rounds are detected:

1. **Log**: "Metric gate: {max_stagnant_rounds} consecutive rounds without Pareto improvement → STAGNATED"
2. **Record**: Save current metrics and stagnation reason as feedback
3. **Preserve**: Current worktree stays (not deleted). Issue log records the stagnation.
4. **Status**: Issue moves to BLOCKED with reason `metric_stagnated`
5. **Recovery**: Human can either:
   - `unblock` to continue in the same worktree (maybe with new instructions)
   - `cleanup` the old worktree + `iterate` to start fresh

Note: automatic new-worktree creation is deferred. For v2, stagnation → BLOCKED is sufficient. The human decides whether to continue or restart. This avoids complexity of managing multiple worktrees per issue.

## Metrics History

Engine stores metrics per round in the issue's feedback system:

```python
# In issue_store, alongside feedback.json
# metrics_history.json
{
  "rounds": [
    {"round": 1, "metrics": {"recall": 0.74, "precision": 0.95}},
    {"round": 2, "metrics": {"recall": 0.79, "precision": 0.73}},
    {"round": 3, "metrics": {"recall": 0.79, "precision": 0.73}}
  ]
}
```

Simple append-only JSON file. Engine reads last entry to compare with current round.

## Acceptance Repositioning

Same as v1 — unchanged:

```
develop → gate (pytest + metrics) → review → [pass] → acceptance → DONE
```

If acceptance fails after review pass:
1. Session reset
2. Resolved feedback items reset to unresolved
3. Acceptance failure feedback passed to next develop round

## Gate: Three Outcomes

| Outcome | Meaning | Action |
|---------|---------|--------|
| pytest fail / metrics.json missing | Code bug / incomplete | Preserve code, retry develop |
| Metrics: all targets met | Optimization complete | Proceed to review |
| Metrics: Pareto improvement | Making progress | Record metrics, proceed to review |
| Metrics: NOT Pareto (< max rounds) | Slowing down | Log warning, proceed to review anyway |
| Metrics: NOT Pareto (= max rounds) | Stagnated | BLOCKED with `metric_stagnated` |

## What Changed from v1

| Aspect | v1 (baseline protection) | v2 (Pareto detection) |
|--------|--------------------------|----------------------|
| Targets | Baselines (low bars) | Final goals |
| Comparison | Current vs fixed threshold | Current vs previous round |
| Failure action | Revert code in same worktree | BLOCKED → human decides |
| Checkpoint/revert | Required (save_checkpoint, revert_to) | Not used |
| Session reset | On metric gate fail | Not needed (BLOCKED exits loop) |
| Complexity | High (revert, feedback, session mgmt) | Low (compare two JSON objects) |

## What to Remove from v1

The following v1 mechanisms are **no longer needed in the engine develop loop**:

- `save_checkpoint()` calls before develop rounds
- `revert_to()` calls on metric gate failure
- Session reset on metric gate failure
- `_update_metric_gate_feedback()` / `_resolve_metric_gate_feedback()`
- `metric_gate_retries` counter

The WorktreeManager checkpoint/revert methods can stay (useful for other scenarios) but the engine won't call them for metric gate purposes.

## Implementation Scope

**Modify:**
- `src/shadowcoder/core/engine.py` — replace v1 metric gate logic with Pareto detection
- `src/shadowcoder/core/config.py` — update `get_metric_gate()` to parse new config format
- `src/shadowcoder/core/models.py` — replace `BLOCKED_METRIC_GATE` with `BLOCKED_METRIC_STAGNATED`
- `src/shadowcoder/core/issue_store.py` — add `save_metrics_history()` / `load_metrics_history()`

**Add:**
- `tests/core/test_metric_gate.py` — replace v1 tests with Pareto tests

**Keep unchanged:**
- `src/shadowcoder/core/worktree.py` — checkpoint/revert stays, just not called by metric gate
- Acceptance repositioning from v1 — already correct

## Design Decisions

1. **Pareto, not monotonic improvement.** We don't require ALL metrics to improve. Just: none worse + at least one better. This allows natural precision-recall tradeoffs during optimization.

2. **BLOCKED, not auto-restart.** Stagnation → BLOCKED → human decides. Auto-creating new worktrees adds complexity (multiple worktrees per issue, which to merge, cleanup). BLOCKED is simple and lets the human assess whether to continue, restart, or abandon.

3. **Tolerance for noise.** 1% minimum delta prevents random seed variation from counting as "improvement." Configurable via `improvement_threshold`.

4. **Metrics history is append-only.** No need to edit or delete history entries. Simple JSON file alongside issue metadata.

5. **Acceptance doesn't check metrics.** Metric gate owns quantitative targets. Acceptance owns functional correctness. No overlap.
