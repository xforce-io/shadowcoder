[English](README.md) | [中文](README_CN.md)

# ShadowCoder

Point it at a repo. Give it a task. It codes until it works.

```
         generate              verify              feedback
  Agent ──────────→ Code ──────────→ Score ──────────→ Agent
    ↑                                                    │
    └────────────── iterate until converged ──────────────┘
```

## Quick Start

```bash
pip install -e ".[dev]"

# Run — no config needed if you have Claude Code CLI installed
python scripts/run_real.py /path/to/repo run "Add user authentication" --from requirements.md

# Or point it at a GitHub issue
python scripts/run_real.py /path/to/repo run --from https://github.com/owner/repo/issues/42
```

That's it. ShadowCoder creates a design, writes the code in an isolated worktree, runs tests, reviews the output, and iterates until everything passes.

## What It Does

```
create → preflight → design ⇄ review → develop ⇄ gate ⇄ review → done
                                            ↑       │
                                            └───────┘
                                         fail: retry develop
```

- **Preflight**: Quick feasibility check. Low feasibility blocks early.
- **Design**: Agent produces architecture doc. Reviewer evaluates it.
- **Develop**: Agent writes code in an isolated git worktree.
- **Gate**: Engine independently runs tests (`cargo test`, `pytest`, `go test`) and verifies acceptance tests passed. Gate failure routes back to develop.
- **Review**: Reviewer evaluates code diff. Pass → done.

Review severity counts are the loss signal — they decrease over rounds:

```
Gomoku Design: R1=CRITICAL:2,HIGH:4 → R2=CRITICAL:1,HIGH:1 → R3=CRITICAL:0,HIGH:0 (converged)
```

## Validated Results

### SQL Database Engine

Built from a requirements document (parser, query planner, executor, storage, B-tree indexes, MVCC transactions):

| Language | Design | Develop | Tests | Code |
|----------|--------|---------|-------|------|
| Go | 3 rounds | 3 rounds | 1 round | 17K lines |
| Rust | 6 rounds | 4 rounds | 3 rounds | 10K lines |
| Haskell | 9 rounds (blocked) | - | - | - |

The Rust version demonstrates the full loop: agent reported tests passing, but independent verification caught 2 failing benchmarks. The system routed back to develop, the agent optimized the code, and all 44 tests passed.

### Gomoku AI (Rust, Claude Sonnet)

| Phase | Rounds | Notes |
|-------|--------|-------|
| Design | 3 | R1: 2 CRITICAL. R3: passed |
| Develop | 4 | R1-R3: gate failures. R4: all tests pass |

AI (depth=4) vs baseline: >90% win rate over 100 games.

### Multi-Model: LRU Cache (Python, DeepSeek-v3 via Volcengine)

| Phase | Rounds | Notes |
|-------|--------|-------|
| Design | 2 | R1: 1 CRITICAL, 3 HIGH. R2: conditional pass |
| Develop | 1 | Gate pass on first attempt. 26 tests |

Any model reachable via an Anthropic-compatible API can drive the full loop.

## Configuration

**Zero config**: If you have [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated, ShadowCoder works out of the box — no config file needed. It uses your local Claude Code with the default model.

**Advanced**: Create `~/.shadowcoder/config.yaml` to customize models, use third-party APIs, or mix agents:

```yaml
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

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 5
  max_test_retries: 3
  # max_budget_usd: 10.0
```

Mix agents freely: one for develop, another for review.

## Usage

```bash
# Full loop — from title + requirements file
python scripts/run_real.py /path/to/repo run "Feature Name" --from requirements.md

# Full loop — from GitHub issue (title auto-extracted)
python scripts/run_real.py /path/to/repo run --from https://github.com/owner/repo/issues/42

# Resume last issue
python scripts/run_real.py /path/to/repo run

# Run individual stages
python scripts/run_real.py /path/to/repo design 1
python scripts/run_real.py /path/to/repo develop 1

# Human-in-the-loop controls
python scripts/run_real.py /path/to/repo approve 1    # approve blocked issue
python scripts/run_real.py /path/to/repo resume 1     # retry from blocked
python scripts/run_real.py /path/to/repo cancel 1

# Query
python scripts/run_real.py /path/to/repo list
python scripts/run_real.py /path/to/repo info 1

# Cleanup
python scripts/run_real.py /path/to/repo cleanup 1
python scripts/run_real.py /path/to/repo cleanup 1 --delete-branch
```

## How It Works

ShadowCoder automates the human development loop: write code → verify → fix → repeat. This is structurally identical to a neural-symbolic training loop:

| Training Concept | ShadowCoder Equivalent |
|------------------|----------------------|
| Forward pass | Agent generates design/code |
| Loss function | Review severity counts (CRITICAL/HIGH) + test exit code |
| Backpropagation | Review feedback injected into next context |
| Gradient clipping | Per-round feature capacity limit |
| Early stopping | Max rounds reached, escalate to human |
| Ground truth oracle | Independent test verification |

The key difference: ShadowCoder optimizes the **output artifact** (code), not the model weights. It is a test-time compute system.

### Symbolic Constraints

The "symbolic" half ensures reliability:

- **State machine**: Issue lifecycle with validated transitions. No skipping stages.
- **Review thresholds**: Deterministic pass/fail based on CRITICAL/HIGH/MEDIUM/LOW counts.
- **Independent test verification**: Engine runs tests itself. Non-zero exit code overrides agent's PASS to FAIL.
- **Budget limits**: Token cost checked after each agent call.
- **Retry bounds**: Max review rounds and test retries prevent infinite loops.

### Architecture

```
src/shadowcoder/
  core/
    engine.py          # The loop: state machine + review scoring + test verification
    bus.py             # Async message bus
    issue_store.py     # Issue files, logs, version archives
    models.py          # States, transitions
    config.py          # Typed config with zero-config defaults
    task_manager.py    # Runtime tasks
    worktree.py        # Git worktree lifecycle
  agents/
    types.py           # Structured output types
    base.py            # Abstract interface + helpers
    claude_code.py     # Claude Code CLI implementation
    registry.py        # Agent discovery
```

### Agent Abstraction

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput
    async def design(self, request) -> DesignOutput
    async def develop(self, request) -> DevelopOutput
    async def review(self, request) -> ReviewOutput
```

Testing is handled by the Engine's gate — not the agent. Adding a new agent means implementing these four methods.

### Audit Trail

```
.shadowcoder/issues/
  0001.md          # Current state (requirements, design, implementation, test results)
  0001.log.md      # Chronological timeline — every action timestamped
  0001.versions/   # Archived outputs — design_r1.md, design_r2.md, develop_r1.md, ...
```

The log is append-only. Nothing is lost.

## Known Limitations

- **Cost tracking incomplete**: Token counts and costs not reliably extracted from Claude CLI responses.
- **No graceful stop**: Killing a running agent requires `pkill`.
- **No checkpoint/resume**: Interrupted develop sessions don't auto-recover from partial progress.
- **Single repo per process**: Use separate processes for concurrent work on the same repo.

## Roadmap

- **Context compression**: Structured summaries (via fast model) to replace head+tail truncation.
- **Prompt audit**: Auto-evaluate context efficiency after each run.
- **Parallel issues**: Concurrent issue execution with proper locking.

## License

MIT
