# ShadowCoder Architecture

## Overview

ShadowCoder is an automated coding agent orchestrator that runs a
design → develop → gate → review loop. It coordinates multiple AI agents
(developer, reviewer, acceptance writer) through a message bus, with
structured issue tracking and human intervention points.

## Core Loop

```
Engine._run_design_cycle:  [preflight] → design → review → retry or approved
Engine._run_develop_cycle: [acceptance] → develop → gate → review → retry or done
                                            ↑        │
                                            └────────┘  (gate fail → retry develop)
                                            (2 consecutive fails → escalate to reviewer)

                           develop → acceptance check ──FAIL──→ retry develop
                                                        │
                                              (same error 2x)
                                                        │
                                                        ▼
                                              escalation reviewer  ← dedicated prompt (planned)
                                                   │         │
                                    [TARGET:acceptance_script] │    (normal feedback)
                                                   │         │
                                                   ▼         ▼
                                          BLOCKED      continue develop
                                       (acceptance_bug)
```

## State Machine

```
States: CREATED → DESIGNING ⇄ DESIGN_REVIEW → APPROVED → DEVELOPING ⇄ DEV_REVIEW → DONE
        Any state → BLOCKED (with reason + from_status) / FAILED / CANCELLED
        DONE → APPROVED (via iterate)

BLOCKED reasons: budget_exceeded | max_review_rounds | acceptance_too_weak
                 | acceptance_confirmed | acceptance_script_bug | low_feasibility

Recovery:
  unblock [msg] → restore blocked_from → auto-execute cycle
  approve       → DONE or APPROVED (close the issue)
  cancel        → CANCELLED
  resume        → only for non-BLOCKED active states (crash recovery)
```

BLOCKED carries structured metadata:
- `blocked_reason`: why the issue was blocked (one of the reason constants)
- `blocked_from`: the status to restore on unblock (e.g., DEVELOPING)

Both fields are set atomically by `Engine._block_issue()` and cleared on
unblock/approve.

## Key Files

| File | Responsibility |
|------|---------------|
| `src/shadowcoder/core/engine.py` | Main loop, gate logic, escalation, feedback management |
| `src/shadowcoder/core/models.py` | Issue/Task dataclasses, state machine, blocked reason constants |
| `src/shadowcoder/core/issue_store.py` | Issue persistence (YAML frontmatter), logs, version archives |
| `src/shadowcoder/core/bus.py` | Message bus (command/event dispatch) |
| `src/shadowcoder/core/config.py` | Typed config access |
| `src/shadowcoder/core/language.py` | Language detection and test profiles |
| `src/shadowcoder/agents/base.py` | Abstract agent interface, prompt assembly, output parsing |
| `src/shadowcoder/agents/claude_code.py` | Claude Code CLI transport |
| `src/shadowcoder/agents/codex.py` | OpenAI Codex CLI transport |
| `src/shadowcoder/agents/types.py` | AgentRequest, ReviewOutput, AcceptanceOutput, etc. |
| `data/roles/` | Default role prompts (soul.md + instructions.md per role) |
| `scripts/run_real.py` | CLI entry point |

## Agent Roles and Prompt Assembly

Each agent action has a role with two prompt files:
- `soul.md` — personality and approach (system-level framing)
- `instructions.md` — task-specific instructions and output format

Roles are loaded from `data/roles/<role>/` with overrides from
`.shadowcoder/roles/` (project) or `~/.shadowcoder/roles/` (user).

### Prompt assembly per action

| Action | Role | System Prompt | User Prompt Builder |
|--------|------|---------------|-------------------|
| design | `developer` | soul + instructions | `_build_context()` |
| develop | `developer` | soul + instructions | `_build_context()` |
| review (design) | `design_reviewer` | soul + instructions | `_build_context()` |
| review (code) | `code_reviewer` | soul + instructions | `_build_review_context()` |
| write_acceptance | `acceptance_writer` | soul + instructions | `_build_context()` |
| escalation review | `escalation_reviewer` | soul + instructions | `_build_review_context()` |

### Review context (`_build_review_context`)

For code reviews (including escalation), the prompt includes:
- Requirements (需求)
- Design summary (truncated)
- Git diff
- Previous review
- Unresolved feedback items
- Failure output (gate or acceptance)
- Acceptance script content (when provided)

### Escalation review

When acceptance or gate fails repeatedly with the same error, the engine
escalates to the `escalation_reviewer` via `_escalate_to_reviewer()`.

This uses a dedicated prompt (`data/roles/escalation_reviewer/`) that frames
the task as "failure root cause adjudication" — binary judgment of whether
the bug is in the code or the acceptance script. Key differences from the
standard `code_reviewer`:

- **Soul**: "失败根因裁判" not "代码评审专家" — must give a verdict, no hedging
- **Task framing**: "Determine the root cause" not "Review the implementation"
- **No conflicting signals**: does not claim code passed gate
- **No conservative bias**: repeated failure is treated as evidence the script may be wrong
- **Prompt dump**: `_escalate_to_reviewer()` calls `_dump_agent_context()` for debuggability

If the reviewer identifies the acceptance script as the problem, it uses the
`[TARGET:acceptance_script]` marker. The engine detects this and transitions
to BLOCKED with `acceptance_script_bug` reason.

## Gate Behavior

- Runs `cargo test` / `pytest` / `go test` / `npm test` (auto-detected
  via `language.py` or config `build.test_command`)
- Runs acceptance script (`NNNN/acceptance.sh`) — must pass after develop
- Verifies each acceptance test in `proposed_tests` was executed and passed
- Skipped/ignored tests are detected and reported as gate failure
- Falls back to running individual tests with force-include flags if
  heuristic is ambiguous
- Gate output uses head+tail truncation (not blind tail-only) to preserve
  compile errors
- 2 consecutive gate failures escalate to code reviewer for analysis
- Gate/acceptance failure output is processed by utility agent (LLM) to
  extract root-cause errors
- Same error detected in consecutive rounds triggers forced reviewer
  escalation

## Issue Storage

```
.shadowcoder/issues/
  0001/
    issue.md          # YAML frontmatter (status, blocked_reason, blocked_from) + markdown sections
    issue.log         # Append-only timeline
    feedback.json     # Structured feedback tracking
    acceptance.sh     # Acceptance test script (generated, not in worktree)
    versions/         # Snapshots of agent output per round
    prompts/          # Prompt dumps for debugging
  worktrees/
    issue-1/          # Git worktree (actual code)
```

## Message Bus

Commands and events flow through `MessageBus`:

```
Commands: CMD_CREATE_ISSUE, CMD_DESIGN, CMD_DEVELOP, CMD_RUN,
          CMD_RESUME, CMD_APPROVE, CMD_UNBLOCK, CMD_CANCEL,
          CMD_CLEANUP, CMD_ITERATE, CMD_LIST, CMD_INFO

Events:   EVT_ISSUE_CREATED, EVT_STATUS_CHANGED, EVT_AGENT_OUTPUT,
          EVT_REVIEW_RESULT, EVT_TASK_STARTED, EVT_TASK_COMPLETED,
          EVT_TASK_FAILED, EVT_ISSUE_LIST, EVT_ISSUE_INFO, EVT_ERROR
```

## Multi-Model Support

Agents are configured independently with different models and transports:

```yaml
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
  acceptance: fast-coder
  design_review: [claude-coder]
  develop_review: [claude-coder]
```
