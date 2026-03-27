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

# Resume a BLOCKED issue (after human review)
python scripts/run_real.py ~/dev/github/<name> resume 1

# Approve a BLOCKED issue (skip remaining review)
python scripts/run_real.py ~/dev/github/<name> approve 1

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
Engine._run_develop_cycle: [acceptance] → develop → gate → review → retry or done
                                            ↑        │
                                            └────────┘  (gate fail → retry develop)
                                            (2 consecutive fails → escalate to reviewer)

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
- `src/shadowcoder/agents/types.py` — AgentRequest, ReviewOutput, AcceptanceOutput, etc.
- `data/roles/` — default role prompts (soul.md + instructions.md per role)

## Multi-Model Support

Config is structured in sections: `clouds`, `models`, `agents`, `dispatch`, `review_policy`, and optional `build`/`gate`.

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

dispatch:
  design: fast-coder
  develop: fast-coder
  acceptance: fast-coder     # optional, falls back to develop agent
  design_review: [claude-coder]
  develop_review: [claude-coder]

review_policy:
  pass_threshold: no_critical    # or "no_high_or_critical"
  max_review_rounds: 3
  max_test_retries: 3
  # max_budget_usd: 10.0

# build:
#   test_command: "cargo test 2>&1"  # auto-detected if omitted
```

Mix agents freely: one for develop, another for review. Agent types: `claude_code` and `codex`.

## Gate Behavior

- Runs `cargo test` / `pytest` / `go test` / `npm test` (auto-detected via `language.py` or config `build.test_command`)
- Runs acceptance script (`NNNN/acceptance.sh`) — must pass after develop
- Verifies each acceptance test in `proposed_tests` was executed and passed
- Skipped/ignored tests are detected and reported as gate failure
- Falls back to running individual tests with force-include flags if heuristic is ambiguous
- Gate output uses head+tail truncation (not blind tail-only) to preserve compile errors
- 2 consecutive gate failures escalate to code reviewer for analysis

## Conventions

- Issue files: `.shadowcoder/issues/NNNN/issue.md` (current state) + `issue.log` (append-only timeline) + `feedback.json` (feedback tracking) + `acceptance.sh` (test script) + `versions/` (snapshots)
- Role prompts: `data/roles/<role>/soul.md` + `instructions.md`, customizable at `.shadowcoder/roles/` (project) or `~/.shadowcoder/roles/` (user)
- One `.shadowcoder` per git root — never nested
- repo_path must be a git root directory
