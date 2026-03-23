# ShadowCoder

A neural-symbolic self-evolving development system. AI agents generate code (neural), while structured review scoring, state machines, and deterministic test verification (symbolic) drive iterative improvement — until the output converges to meet requirements.

## Core Idea

Traditional software development relies on humans to iterate between writing code and verifying it. ShadowCoder automates this loop:

```
         generate              verify              feedback
  Agent ──────────→ Code ──────────→ Score ──────────→ Agent
    ↑                                                    │
    └────────────── iterate until converged ──────────────┘
```

This is structurally identical to a neural-symbolic training loop:

| Training Concept | ShadowCoder Equivalent |
|------------------|----------------------|
| Forward pass | Agent generates design/code |
| Loss function | Review score (0-100) + test exit code |
| Backpropagation | Review feedback injected into next context |
| Gradient clipping | Per-round feature capacity limit |
| Early stopping | Max rounds reached, escalate to human |
| Curriculum | Staged: preflight → design → develop → test |
| Ground truth oracle | Independent test verification (`cargo test`, `go test`) |

The key difference from model training: ShadowCoder optimizes the **output artifact** (code), not the model weights. It is a test-time compute system — improving quality through inference-time iteration rather than training.

## The Loop

```
create → preflight → design ⇄ review → develop ⇄ review → test → done
                                  ↑                          │
                                  └──── auto-route on fail ──┘
```

Each stage:

- **Preflight**: Quick feasibility assessment. Low feasibility blocks before wasting compute.
- **Design**: Agent produces architecture document. Reviewer scores it.
  - Score >= 90: pass. 70-89: conditional pass. < 70: retry with feedback.
- **Develop**: Agent writes actual code in an isolated git worktree. Reviewer scores it.
- **Test**: Agent runs tests. Then Engine independently runs the configured test command and checks the exit code. Agent's self-report is overridden if the real tests fail.
  - Failure analysis → auto-route to `develop` or `design` → re-test.

The review score is the loss signal. It decreases (improves) over rounds — a literal training curve:

```
Rust SQL Engine Design: R1=52 → R2=68 → R3=73 (converged above threshold)
```

## Symbolic Constraints

The "symbolic" half is what makes the system reliable:

- **State machine**: Issue lifecycle with validated transitions. No skipping stages.
- **Review scoring thresholds**: Deterministic pass/fail/conditional decisions from continuous scores.
- **Independent test verification**: Engine runs `cargo test` / `go test` / `pytest` itself. If the exit code is non-zero, the agent's PASS is overridden to FAIL. This is the non-negotiable ground truth oracle.
- **Budget limits**: Accumulated token cost checked after each agent call. Exceeding the limit halts the loop.
- **Retry bounds**: Max review rounds and max test retries prevent infinite loops.

These constraints cannot be circumvented by the neural component. They are the rules of the game.

## Validated Results

Built a SQL database engine (parser, query planner, executor, storage, B-tree indexes, MVCC transactions, error handling) from a requirements document:

| Language | Design | Develop | Test | Code | Functional | Performance |
|----------|--------|---------|------|------|-----------|-------------|
| Go | 3 rounds | 3 rounds | 1 round | 17K lines | Passed | Passed |
| Rust | 6 rounds | 4 rounds | 3 rounds | 10K lines | 37/37 | 7/7 (fixed via auto-route) |
| Haskell | 9 rounds (blocked) | - | - | - | - | - |

The Rust version demonstrates the full self-evolving loop: agent reported tests passing, but independent verification caught 2 failing performance benchmarks. The system automatically routed back to develop, the agent optimized the code, and all 44 tests passed on the next round.

The Haskell version demonstrates graceful failure: the system identified (via reviewer feedback) that Haskell's STM/IO interaction model made the concurrent transaction design fundamentally problematic, and escalated to human rather than spinning endlessly.

## Installation

```bash
git clone https://github.com/xforce-io/shadowcoder.git
cd shadowcoder
pip install -e ".[dev]"
```

Requires [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated.

## Configuration

`~/.shadowcoder/config.yaml`:

```yaml
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
      model: sonnet
      permission_mode: auto

reviewers:
  design: [claude-code]
  develop: [claude-code]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 3
  max_test_retries: 3
  # max_budget_usd: 10.0

build:
  test_command: "cargo test"  # or "go test ./..." or "pytest"

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

## Usage

```bash
# Create issue with requirements
python scripts/run_real.py /path/to/repo create "Feature" --from requirements.md

# Run the loop
python scripts/run_real.py /path/to/repo design 1
python scripts/run_real.py /path/to/repo develop 1
python scripts/run_real.py /path/to/repo test 1

# Human-in-the-loop controls
python scripts/run_real.py /path/to/repo approve 1    # approve blocked issue
python scripts/run_real.py /path/to/repo resume 1     # retry from blocked
python scripts/run_real.py /path/to/repo cancel 1
python scripts/run_real.py /path/to/repo cleanup 1    # remove worktree

# Query
python scripts/run_real.py /path/to/repo list
python scripts/run_real.py /path/to/repo info 1
```

Or via TUI: `shadowcoder`

## Audit Trail

Every issue maintains a complete record:

```
.shadowcoder/issues/
  0001.md          # Current state (requirements, latest design, implementation, test results)
  0001.log.md      # Chronological timeline — every action timestamped
  0001.versions/   # Archived outputs — design_r1.md, design_r2.md, develop_r1.md, ...
```

The log is append-only. Design/code sections show the latest version; previous versions are in `.versions/`. Review history is in the log. Nothing is lost.

## Agent Abstraction

Agents implement five methods with structured return types:

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput
    async def design(self, request) -> DesignOutput
    async def develop(self, request) -> DevelopOutput
    async def review(self, request) -> ReviewOutput
    async def test(self, request) -> TestOutput
```

Each agent handles its own output format constraints internally. The Engine never parses raw LLM output — it only consumes typed fields. Adding a new agent (Codex, LangChain, local models) means implementing these five methods.

## Architecture

```
src/shadowcoder/
  core/
    engine.py          # The loop: state machine + review scoring + test verification
    bus.py             # Async message bus
    issue_store.py     # Issue files, logs, version archives
    models.py          # States, transitions
    config.py          # Typed config
    task_manager.py    # Runtime tasks
    worktree.py        # Git worktree lifecycle
  agents/
    types.py           # Structured output types
    base.py            # Abstract interface + helpers
    claude_code.py     # Claude Code CLI implementation
    registry.py        # Agent discovery
  cli/tui/app.py       # Textual TUI
```

138 tests. 18 source files. ~1,700 lines of Python.

## Known Limitations

- **Cost tracking incomplete**: `AgentUsage` fields are defined but the Claude CLI JSON response parsing does not reliably extract token counts and costs. Usage summary shows `$0.0000`.
- **Go validation caveat**: The Go SQL engine was validated before independent test verification existed. Its results are based on manual `go test` runs, not the automated verification loop.
- **No graceful stop**: Killing a running agent requires `pkill`. A `stop` command is not yet implemented.
- **No checkpoint/resume**: If a long develop session is interrupted, there is no automatic recovery from partial progress.
- **Single reviewer model**: Design and code review currently use the same agent instance. Cross-model review (e.g., Opus reviewing Sonnet's output) is planned but not implemented.

## License

MIT
