# Web Dashboard Design

Real-time web-based observation dashboard for monitoring ShadowCoder task execution.

## Problem

Observing ShadowCoder execution currently requires tailing log files and running CLI commands (`info`, `list`). There is no visual overview of pipeline progress, no real-time streaming of activity, and no way to see retry history at a glance.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Layout | Progress bar + dual column (方案 C) | ShadowCoder is a linear pipeline with one active agent at a time. No need for topology graphs or multi-agent panels. Minimal UI answers the three core questions: where am I, what's happening, what went wrong. |
| Frontend | HTMX + Jinja2 templates | Zero build step, stays in Python ecosystem, HTMX handles SSE-driven partial DOM updates natively. |
| Backend | FastAPI | Async-native, SSE support, consistent with existing Python codebase. |
| Real-time updates | SSE (Server-Sent Events) | Dashboard is read-only observation. SSE is simpler than WebSocket for unidirectional push. Operations (if added later) use plain HTTP POST. |
| Data source | Direct file reads (watchdog) | Dashboard reads `.shadowcoder/issues/` directly. No engine code changes needed. Log is append-only, issue state is in frontmatter. Fully decoupled from the engine process. |
| Multi-issue | Issue list + detail view | Left/top issue selector allows switching between issues. Defaults to the most recently active issue. |
| Launch | Standalone command | `python scripts/run_real.py <repo> dashboard`. Independent of the engine process — can be started/stopped at any time without affecting execution. |

## Page Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ ShadowCoder  │  ~/dev/github/myproject       [Issue #1 ▼]      │
├─────────────────────────────────────────────────────────────────┤
│ DEVELOPING  R1  │  fast-coder  │  $0.42  │  3m 24s             │
├─────────────────────────────────────────────────────────────────┤
│ Design ✓ │ Review ✓ │ ▼ Develop R1 │ Gate │ Review │ Accept    │
│ ████████   ████████   ▓▓▓▓▓▓░░░░░   ░░░░   ░░░░░░   ░░░░░░   │
│                    retry: Gate R1 ✗ → Gate R2 ✗ → Develop R3   │
├────────────────────────────┬────────────────────────────────────┤
│ LIVE ACTIVITY              │ CHANGED FILES              5      │
│                            │ A src/auth/handler.py     +142    │
│ 11:42 Design R1 开始       │ A src/auth/middleware.py    +89   │
│ 11:45 Review PASSED        │ M src/main.py          +12 -3    │
│ 11:46 Develop R1 开始      │ A tests/test_auth.py       +78   │
│ 11:48 Gate FAIL R1         │                                   │
│   > AssertionError: ...    │ REVIEW SUMMARY                    │
│ 11:48 Develop R2 开始      │ PASSED  C:0 H:0 M:3 L:4          │
│ 11:50 Gate PASS R2         │                                   │
│ 11:50 Dev Review 开始      │ GATE OUTPUT                       │
│ ●                          │ pytest tests/ -v                  │
│                            │ 12 passed in 2.3s                 │
└────────────────────────────┴────────────────────────────────────┘
```

### Top Bar

- Project name and repo path
- Issue selector dropdown (all issues from issue store, sorted by most recent activity)

### Status Bar

- Current issue status badge (`DESIGNING`, `DEVELOPING`, `BLOCKED`, etc.)
- Current round number
- Active agent name (from dispatch config)
- Accumulated cost for this issue
- Elapsed time since issue creation

### Pipeline Progress Bar

A horizontal segmented bar representing the full pipeline:

```
Design → Design Review → Develop → Gate → Dev Review → Acceptance
```

Each segment is colored by state:
- Green (`#238636`): completed
- Blue (`#1f6feb`): active (with pulse animation)
- Gray (`#21262d`): pending
- Red (`#f85149`): failed (test gate)
- Yellow (`#d29922`): reverted (metric gate)

Below the bar: a retry timeline showing the history of attempts in the current phase.

### Dual Column (Bottom)

**Left column — Live Activity:**
- Real-time streaming of issue log entries
- Parsed from the append-only `.log` file via watchdog + SSE
- Log entries color-coded: green for success, red for failure, blue for active, gray for info
- Gate failure errors shown inline with indentation
- Auto-scrolls to bottom; scroll-up pauses auto-scroll

**Right column — three panels stacked:**

1. **Changed Files** — `git diff --stat` from the worktree. Shows added/modified/deleted files with line counts. File count badge in header.

2. **Review Summary** — Latest review verdict (PASSED/NOT PASSED), severity counts (CRITICAL/HIGH/MEDIUM/LOW). Parsed from feedback.json.

3. **Gate Output** — Last gate command and its result. Truncated with expand toggle for full output. Shows test pass/fail counts.

## Gate Retry Display

Two gate failure modes are visually distinguished:

### Test Failure (red, rounds R1/R2/R3)

Code is preserved, agent continues fixing.

- Progress bar: the Develop⇄Gate zone splits into segments per round. Red = failed round, blue = current round.
- Retry timeline: `R1 ✗ (3 tests failed) → R2 ✗ (1 test failed) → R3 ●`
- Consecutive 2 failures: amber warning in log indicating reviewer escalation.
- Log shows key error message inline (indented) after each gate failure.

### Metric Gate Failure (yellow, Attempt 1/2/3)

Code is reverted to checkpoint, session reset, fresh develop.

- Progress bar: reverted attempts shown at reduced opacity with strikethrough.
- Retry timeline uses "Attempt" naming (not "R") to emphasize full restart.
- ⏪ revert icon on reverted attempts.
- Dedicated metric comparison table: metric name, threshold, actual value, pass/fail status.
- Log shows revert and session reset entries in yellow.

### Terminal States

- `BLOCKED`: progress bar grays out, status badge turns red, blocked reason displayed prominently.
- `DONE`: all segments green, summary stats shown (total cost, total time, rounds).
- `FAILED`/`CANCELLED`: progress bar shows how far it got, status badge indicates terminal state.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────┐
│  .shadowcoder/  │     │  FastAPI Server   │     │   Browser     │
│  issues/        │────▶│                   │────▶│               │
│  NNNN.md        │     │  watchdog watcher │ SSE │  HTMX +       │
│  NNNN.log       │     │  file parsers     │     │  Jinja2       │
│  feedback.json  │     │  SSE endpoints    │     │  templates    │
│  worktrees/     │     │  Jinja2 renderer  │     │               │
└─────────────────┘     └──────────────────┘     └───────────────┘
```

### Backend Components

**FileWatcher** — Uses `watchdog` to monitor `.shadowcoder/issues/` for file changes. On change, parses the affected file and pushes updates via SSE.

**Parsers:**
- `IssueParser` — reads issue `.md` frontmatter for status, title, timestamps. Reuses existing `IssueStore.load()` logic where possible.
- `LogParser` — tails the `.log` file, parses `[YYYY-MM-DD HH:MM:SS] ...` entries. Lines starting with `[` begin a new entry; subsequent lines without the timestamp prefix are continuation lines belonging to the previous entry. Detects entry types: status change, gate result, usage, review, error context.
- `FeedbackParser` — reads `feedback.json` for review comments, proposed tests, acceptance tests.
- `WorktreeParser` — runs `git diff --stat` and `git diff --name-status` in the worktree directory.

**SSE Endpoints:**
- `GET /sse/issues` — pushes issue list updates (status changes)
- `GET /sse/issue/{id}` — pushes all updates for a specific issue (log entries, status, files, review, gate)

**REST Endpoints:**
- `GET /` — main page, renders full dashboard with Jinja2
- `GET /api/issues` — list all issues (JSON)
- `GET /api/issues/{id}` — issue detail (JSON)
- `GET /api/issues/{id}/log` — full log (JSON array)
- `GET /api/issues/{id}/files` — changed files from worktree (JSON)

**Static assets:** CSS and minimal JS served from a `static/` directory within the dashboard package. No build step.

### Frontend

**HTMX integration:**
- SSE connection via `hx-sse="connect:/sse/issue/{id}"` on the main content area
- Each SSE event type triggers a partial DOM swap of the relevant panel
- Issue selector change triggers full page reload via `hx-get`

**Templates (Jinja2):**
- `base.html` — page shell, CSS, HTMX script tag
- `dashboard.html` — full dashboard layout
- `partials/pipeline.html` — progress bar + retry timeline
- `partials/status_bar.html` — status/agent/cost/time bar
- `partials/log.html` — live activity entries
- `partials/files.html` — changed files list
- `partials/review.html` — review summary
- `partials/gate.html` — gate output
- `partials/metrics.html` — metric gate comparison table

### File Structure

```
src/shadowcoder/dashboard/
├── __init__.py
├── server.py          # FastAPI app, SSE endpoints, startup
├── watcher.py         # watchdog file monitoring
├── parsers.py         # log, issue, feedback, worktree parsers
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   └── partials/
│       ├── pipeline.html
│       ├── status_bar.html
│       ├── log.html
│       ├── files.html
│       ├── review.html
│       ├── gate.html
│       └── metrics.html
└── static/
    ├── style.css
    └── dashboard.js   # auto-scroll, SSE reconnect, minor UI logic
```

### CLI Integration

New subcommand in `scripts/run_real.py`:

```
python scripts/run_real.py <repo> dashboard [--port 8420] [--host 127.0.0.1]
```

Default port: 8420. Opens browser automatically on start.

### Dependencies

Add to `pyproject.toml` extras or optional dependencies:

- `fastapi` — web framework
- `uvicorn` — ASGI server
- `watchdog` — filesystem monitoring
- `jinja2` — templating (likely already a FastAPI dependency)
- `htmx` — loaded via CDN in templates, no Python package needed

## Non-Goals

- No write operations from the dashboard (no approve/cancel/resume buttons). Dashboard is read-only observation.
- No authentication. Dashboard runs on localhost only.
- No persistent storage beyond what `.shadowcoder/issues/` already provides.
- No mobile responsiveness in v1.
- No multi-repo support. One dashboard instance per repo.
