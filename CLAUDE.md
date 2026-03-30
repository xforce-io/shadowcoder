# ShadowCoder

Automated coding agent orchestrator: design → develop → gate → review loop.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Config (optional — works without it if Claude Code CLI is installed)
~/.shadowcoder/config.yaml
```

## Running an Experiment

```bash
# 1. Create target repo
mkdir ~/dev/github/<name> && cd ~/dev/github/<name> && git init && git commit --allow-empty -m "init"

# 2. Run full loop (create + design + develop)
python scripts/run_real.py ~/dev/github/<name> run "<title>" --from <requirements.md>

# 3. Run from a GitHub issue
python scripts/run_real.py ~/dev/github/<name> run --from https://github.com/owner/repo/issues/42

# 4. Or run stages individually
python scripts/run_real.py ~/dev/github/<name> create "<title>" --from <requirements.md>
python scripts/run_real.py ~/dev/github/<name> design 1
python scripts/run_real.py ~/dev/github/<name> develop 1

# 5. Resume last issue
python scripts/run_real.py ~/dev/github/<name> run
```

## Monitoring

```bash
# Issue status
python scripts/run_real.py ~/dev/github/<name> info 1

# Log milestones
grep -E "Gate|Review|PASS|FAIL|总计|开始" ~/dev/github/<name>/.shadowcoder/issues/0001.log

# Token usage
grep "Usage:" ~/dev/github/<name>/.shadowcoder/issues/0001.log

# Worktree (actual code)
ls ~/dev/github/<name>/.shadowcoder/worktrees/issue-1/
```

## Managing Issues

```bash
# List all issues
python scripts/run_real.py ~/dev/github/<name> list

# Resume an interrupted issue (process crash recovery, not for BLOCKED)
python scripts/run_real.py ~/dev/github/<name> resume 1

# Approve a BLOCKED issue (accept current state, close it)
python scripts/run_real.py ~/dev/github/<name> approve 1

# Unblock a BLOCKED issue (fix the blocker and continue)
python scripts/run_real.py ~/dev/github/<name> unblock 1 "fixed acceptance script"

# Iterate on a DONE issue (append requirements, re-enter develop)
python scripts/run_real.py ~/dev/github/<name> iterate 1 "Add pagination"
python scripts/run_real.py ~/dev/github/<name> iterate 1 --from new-requirements.md

# Cancel
python scripts/run_real.py ~/dev/github/<name> cancel 1

# Setup & cleanup
python scripts/run_real.py ~/dev/github/<name> init
python scripts/run_real.py ~/dev/github/<name> cleanup 1
python scripts/run_real.py ~/dev/github/<name> cleanup 1 --delete-branch
```

## Architecture

```
Engine._run_design_cycle:  [preflight] → design → review → retry or approved
Engine._run_develop_cycle: develop → gate → review → [pass] → acceptance → DONE
                              ↑        │               │
                              └────────┘               └── acceptance fail → reset session → develop
                         (gate fail → retry develop)
                         (2 consecutive gate fails → escalate to reviewer)
                         (metric gate fail → revert to checkpoint → retry develop)

States: CREATED → DESIGNING ⇄ DESIGN_REVIEW → APPROVED → DEVELOPING ⇄ DEV_REVIEW → DONE
        Any state → BLOCKED (human intervention) / FAILED / CANCELLED
        DONE → APPROVED (via iterate)
```

Key files:
- `src/shadowcoder/core/engine.py` — main loop, gate logic, feedback management
- `src/shadowcoder/agents/base.py` — abstract interface, prompt assembly, output parsing
- `src/shadowcoder/agents/claude_code.py` — Claude Code CLI transport
- `src/shadowcoder/agents/codex.py` — OpenAI Codex CLI transport
- `src/shadowcoder/core/issue_store.py` — issue state, logs, version archives
- `src/shadowcoder/core/config.py` — typed config access
- `src/shadowcoder/core/language.py` — language detection and test profiles
- `src/shadowcoder/core/worktree.py` — worktree management, checkpoints, revert
- `src/shadowcoder/agents/types.py` — AgentRequest, ReviewOutput, AcceptanceOutput, etc.
- `data/roles/` — default role prompts (soul.md + instructions.md per role)

## Multi-Model Support

Config is structured in sections: `clouds`, `models`, `agents`, `dispatch`, `review_policy`, and optional `build`/`gate`/`metric_gate`.

```yaml
# ~/.shadowcoder/config.yaml
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
    type: claude_code        # or "codex"
    model: sonnet
  fast-coder:
    type: claude_code
    model: deepseek-v3
    resumable: false             # recommended for third-party models
    permission_mode: acceptEdits # recommended for third-party models

dispatch:
  design: fast-coder
  develop: fast-coder
  acceptance: fast-coder     # optional, falls back to develop agent
  utility: fast-coder        # optional, for error extraction; falls back to develop agent
  design_review: [claude-coder]
  develop_review: [claude-coder]

review_policy:
  pass_threshold: no_critical    # or "no_high_or_critical"
  max_review_rounds: 3
  max_test_retries: 3
  max_metric_retries: 3          # optional, default 3
  # max_budget_usd: 10.0

# build:
#   test_command: "cargo test 2>&1"  # auto-detected if omitted

# metric_gate:                   # optional; enable metric-based gate checks
#   recall: ">= 0.50"
#   precision: ">= 0.20"
```

Mix agents freely: one for develop, another for review. Agent types: `claude_code` and `codex`.

## Gate Behavior

The gate has two failure modes with different recovery strategies:

**pytest / test suite failure** (preserve code, continue fixing):
- Runs `cargo test` / `pytest` / `go test` / `npm test` (auto-detected via `language.py` or config `build.test_command`)
- Verifies each acceptance test in `proposed_tests` was executed and passed
- Skipped/ignored tests are detected and reported as gate failure
- Falls back to running individual tests with force-include flags if heuristic is ambiguous
- Gate output uses head+tail truncation (not blind tail-only) to preserve compile errors
- 2 consecutive gate failures escalate to code reviewer for analysis
- Gate failure output is processed by utility agent (LLM) to extract root-cause errors
- Same error detected in consecutive rounds triggers forced reviewer escalation

**Metric gate failure** (revert code, retry with different approach):
- After pytest passes, reads `metrics.json` from the worktree root
- Compares each metric against thresholds configured in `metric_gate` (e.g. `recall: ">= 0.50"`)
- Below baseline → revert worktree to the pre-develop checkpoint, reset session, retry develop
- Missing `metrics.json` → treated as normal gate failure (no revert)
- `max_metric_retries` exhausted → BLOCKED with `metric_gate_exhausted`
- Checkpoint is saved (and `metrics.json` deleted for freshness) before each develop round

**Acceptance** (final verification, runs after review passes):
- Acceptance script (`NNNN/acceptance.sh`) runs once as the final step before DONE
- Acceptance fail → reset session + reset resolved feedback → back to develop

## Conventions

- Issue files: `.shadowcoder/issues/NNNN/issue.md` (current state) + `issue.log` (append-only timeline) + `feedback.json` (feedback tracking) + `acceptance.sh` (test script) + `versions/` (snapshots)
- Role prompts: `data/roles/<role>/soul.md` + `instructions.md`, customizable at `.shadowcoder/roles/` (project) or `~/.shadowcoder/roles/` (user)
- One `.shadowcoder` per git root — never nested
- repo_path must be a git root directory
