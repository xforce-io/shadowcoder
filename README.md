# ShadowCoder

An agent-based issue management and development system. Define requirements, and let AI agents design, implement, review, test, and iterate — automatically.

## What It Does

ShadowCoder manages the full development lifecycle for an issue:

```
create → preflight → design ⇄ review → develop ⇄ review → test → done
                                  ↑                          |
                                  └──── auto-route on fail ──┘
```

Each stage is executed by a pluggable AI agent (currently Claude Code via CLI). Reviewers are also agents. The system iterates until the work meets quality standards or escalates to a human.

Key behaviors:
- **Automated review loops** with scoring (0-100). Score >= 90 passes, 70-89 passes conditionally, < 70 retries.
- **Test failure routing**: when tests fail, the agent analyzes the cause and recommends going back to `develop` or `design`.
- **Independent test verification**: Engine runs your test command (`cargo test`, `go test ./...`, etc.) and overrides the agent's self-report if tests actually fail.
- **Preflight check**: before design begins, a quick feasibility assessment flags risks early.
- **Full audit trail**: every action is logged to a separate `.log.md` timeline file, and each round's output is archived in `.versions/`.

## Installation

```bash
git clone https://github.com/xforce-io/shadowcoder.git
cd shadowcoder
pip install -e ".[dev]"
```

## Configuration

Create `~/.shadowcoder/config.yaml`:

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
  # max_budget_usd: 10.0  # optional spending limit

build:
  test_command: "cargo test"  # or "go test ./..." or "pytest"

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

Requires the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) to be installed and authenticated.

## Usage

### Via script

```bash
# Create an issue with requirements
python scripts/run_real.py /path/to/your/repo create "Feature Name" --from requirements.md

# Run design (includes preflight check)
python scripts/run_real.py /path/to/your/repo design 1

# Run development
python scripts/run_real.py /path/to/your/repo develop 1

# Run tests (auto-routes to develop/design on failure)
python scripts/run_real.py /path/to/your/repo test 1

# Other commands
python scripts/run_real.py /path/to/your/repo list
python scripts/run_real.py /path/to/your/repo info 1
python scripts/run_real.py /path/to/your/repo approve 1    # approve a BLOCKED issue
python scripts/run_real.py /path/to/your/repo resume 1     # resume from BLOCKED
python scripts/run_real.py /path/to/your/repo cancel 1
python scripts/run_real.py /path/to/your/repo cleanup 1    # remove worktree
```

### Via TUI

```bash
shadowcoder
# Then type commands: create, design #1, develop #1, test #1, list, info #1, etc.
```

## How It Works

### Issue files

Each issue is stored as markdown with YAML frontmatter in your repo:

```
your-repo/.shadowcoder/issues/
  0001.md          # current state (requirements, design, implementation summary, test results)
  0001.log.md      # chronological timeline (every action timestamped)
  0001.versions/   # archived outputs (design_r1.md, design_r2.md, develop_r1.md, ...)
```

### Git worktrees

Each issue gets its own git worktree and branch (`shadowcoder/issue-N`), isolating concurrent work. The worktree is created on first `design` and persists through `develop` and `test`. After the issue is done, use `cleanup` to remove it.

### Agent abstraction

Agents implement four methods with structured return types:

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput: ...
    async def design(self, request) -> DesignOutput: ...
    async def develop(self, request) -> DevelopOutput: ...
    async def review(self, request) -> ReviewOutput: ...
    async def test(self, request) -> TestOutput: ...
```

Each agent implementation handles its own output format constraints. For example, `ClaudeCodeAgent` calls the `claude` CLI and parses the response. Adding a new agent (Codex, LangChain, etc.) means implementing these five methods.

### Review scoring

Reviews return a score from 0 to 100:

| Score | Decision |
|-------|----------|
| 90-100 | Pass |
| 70-89 | Conditional pass (issues deferred to next stage) |
| < 70 | Fail, retry |

After `max_review_rounds` failures, the issue moves to BLOCKED and waits for human intervention (`approve` or `resume`).

## Validated Results

ShadowCoder has been end-to-end validated on a real task: building a SQL database engine (parser, query planner, executor, storage engine, B-tree indexes, MVCC transactions, error handling).

| Language | Design rounds | Develop rounds | Code output | Functional tests | Performance tests |
|----------|--------------|----------------|-------------|-----------------|------------------|
| Go | 3 | 3 | 52 files, 17K lines | Passed | Passed |
| Rust | 6 | 3 | 24 files, 10K lines | 23/23 passed | 5/7 passed |
| Haskell | 9 (blocked) | not reached | - | - | - |

## Development

```bash
# Run tests
pytest tests/ -v

# Current: 138 tests (unit + integration + e2e)
```

## Architecture

```
src/shadowcoder/
  cli/tui/app.py        # Textual TUI
  core/
    bus.py               # Async message bus (commands/events)
    engine.py            # Orchestrator (state machine, review loops, test routing)
    config.py            # YAML config with typed accessors
    issue_store.py       # Issue CRUD, sections, log, versions
    models.py            # IssueStatus, TaskStatus, state transitions
    task_manager.py      # Runtime task management
    worktree.py          # Git worktree lifecycle (ensure/cleanup/exists)
  agents/
    types.py             # Structured output types (DesignOutput, ReviewOutput, etc.)
    base.py              # Abstract agent interface
    claude_code.py       # Claude Code CLI implementation
    registry.py          # Agent discovery and caching
```

## License

MIT
