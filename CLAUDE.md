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

# 3. Or run stages individually
python scripts/run_real.py ~/dev/github/<name> create "<title>" --from <requirements.md>
python scripts/run_real.py ~/dev/github/<name> design 1
python scripts/run_real.py ~/dev/github/<name> develop 1
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

# Cancel
python scripts/run_real.py ~/dev/github/<name> cancel 1

# Cleanup worktree after done
python scripts/run_real.py ~/dev/github/<name> cleanup 1
```

## Architecture

```
Engine._run_design_cycle:  design → review → retry or approved
Engine._run_develop_cycle: develop → gate → review → retry or done
                                      ↑      │
                                      └──────┘  (gate fail → retry develop)
                                      (2 consecutive fails → escalate to reviewer)
```

Key files:
- `src/shadowcoder/core/engine.py` — main loop, gate logic, feedback management
- `src/shadowcoder/agents/claude_code.py` — agent implementation (prompts, CLI invocation)
- `src/shadowcoder/core/issue_store.py` — issue state, logs, version archives
- `src/shadowcoder/core/config.py` — typed config access
- `src/shadowcoder/agents/types.py` — AgentRequest, ReviewOutput, etc.

## Multi-Model Support

Config is structured in four sections: `clouds` (API endpoints/keys), `models` (model aliases), `agents` (agent definitions), and `dispatch` (which agent runs each role).

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
    type: claude_code
    model: sonnet
  fast-coder:
    type: claude_code
    model: deepseek-v3

dispatch:
  design: fast-coder
  develop: fast-coder
  design_review: [claude-coder]
  develop_review: [claude-coder]
```

Mix agents freely: one for develop, another for review.

## Gate Behavior

- Runs `cargo test` / `pytest` / `go test` (auto-detected from project files)
- Verifies each acceptance test in `proposed_tests` was executed and passed
- Skipped/ignored tests are detected and reported as gate failure
- Falls back to running individual tests with force-include flags if heuristic is ambiguous
- Gate output uses head+tail truncation (not blind tail-only) to preserve compile errors

## Conventions

- Issue files: `.shadowcoder/issues/NNNN.md` (current state) + `.log` (append-only timeline) + `.versions/` (snapshots)
- One `.shadowcoder` per git root — never nested
- repo_path must be a git root directory
