## Goal

Refactor the agent abstraction layer and add Codex CLI (`codex`) as a second agent type, so that shadowcoder can dispatch work to either Claude Code or OpenAI Codex.

## Tech Stack

- Python 3.10+, asyncio
- pytest for testing
- Codex CLI (`codex` binary, installed separately via `npm i -g @openai/codex`)

## Overview

Currently `ClaudeCodeAgent` contains both **shared orchestration logic** (prompt building, output parsing, role instructions) and **Claude-specific transport** (CLI invocation). This makes adding a second agent type require duplicating ~400 lines of code.

The work has two phases:
1. **Refactor**: lift shared logic into `BaseAgent`, reduce `ClaudeCodeAgent` to a thin transport wrapper
2. **Add**: implement `CodexAgent` as a second transport wrapper

## Phase 1: Refactor BaseAgent

### What moves from `ClaudeCodeAgent` to `BaseAgent`

These methods and data are agent-agnostic and should live in `BaseAgent`:

- `DEFAULT_ROLE_INSTRUCTIONS` dict (the 4 role instruction strings)
- `_get_role_instruction(role)` — config override > default > empty
- `_build_context(request)` — builds prompt from issue sections, review feedback, gate output, etc.
- `_build_review_context(request)` — builds diff-aware context for code review
- `_extract_test_command(document)` — regex extraction of test_command from yaml metadata
- `_extract_comments_from_text(text)` — fallback review comment parser
- `preflight()`, `design()`, `develop()`, `review()` — become **concrete** implementations in BaseAgent

### New abstract method

BaseAgent gets one new abstract method that subclasses must implement:

```python
@abstractmethod
async def _run(self, prompt: str, *,
               cwd: str | None = None,
               system_prompt: str | None = None,
               session_id: str | None = None,
               resume_id: str | None = None,
               ) -> tuple[str, AgentUsage]:
    """Execute a prompt via the underlying CLI/API. Return (text_output, usage)."""
    ...
```

The existing 4 action methods call `self._run(...)` instead of `self._run_claude_with_usage(...)`.

### What stays in `ClaudeCodeAgent`

Only the transport layer:
- `_run()` implementation — builds `claude -p` command, runs subprocess, parses JSON response, retries
- `_get_env()` — environment variable merging (this is also useful for Codex, consider moving to BaseAgent)

### Constraints

- `_get_env()` should also move to BaseAgent (both agents need env merging from cloud config)
- `_get_model()` and `_get_permission_mode()` stay in each subclass (different defaults, different semantics)
- The `reviewer` field in `ReviewOutput` should use `self.config.get("type", "unknown")` instead of hardcoded `"claude-code"`
- All existing tests in `tests/agents/test_claude_code.py` must continue to pass with minimal changes (mock target may change from `_run_claude_with_usage` to `_run`)

## Phase 2: Add CodexAgent

### CLI interface mapping

| Capability | Claude Code | Codex CLI |
|---|---|---|
| Non-interactive run | `claude -p` (stdin) | `codex exec "prompt"` or `codex exec -` (stdin) |
| JSON output | `--output-format json` → single JSON | `--json` → JSONL stream (one event per line) |
| Model | `--model sonnet` | `-m o3` |
| Working directory | uses cwd | `-C /path/to/repo` |
| Auto-approve | `--permission-mode auto` | `--full-auto` (sandboxed auto) |
| System prompt | `--system-prompt "..."` | **No CLI flag** — must write `AGENTS.md` file in worktree |
| Session resume | `--resume <id>` | `codex resume <id>` (separate subcommand) |
| Last message | parsed from JSON `.result` | `-o <file>` writes last message to file, or parse from JSONL |

### CodexAgent._run() implementation

1. **System prompt injection**: If `system_prompt` is provided and `cwd` is set, write it to `{cwd}/AGENTS.md` before execution. Track the file so it can be cleaned up or preserved as needed. If an existing `AGENTS.md` exists, prepend the system prompt content with a clear separator, and restore the original after execution.

2. **Command construction**:
   ```
   codex exec --json --full-auto -m <model> -C <cwd> -
   ```
   Prompt is passed via stdin (using `-` as the prompt argument).

3. **JSONL output parsing**: Parse line by line:
   - Collect text from `{"type":"item.completed","item":{"type":"agent_message","text":"..."}}` events
   - Extract usage from `{"type":"turn.completed","usage":{"input_tokens":N,"output_tokens":N}}` events
   - Concatenate all agent_message texts as the result

4. **Permission mode mapping**:
   - Config `permission_mode: "auto"` → `--full-auto`
   - Config `permission_mode: "bypass"` → `--dangerously-bypass-approvals-and-sandbox`
   - Default: `--full-auto`

5. **Session resume**: MVP does NOT implement session resume. If `resume_id` is provided, log a warning and run a fresh session. This can be added later.

6. **Retry logic**: Same as ClaudeCodeAgent — 3 attempts with exponential backoff on non-zero exit.

7. **Timeout**: Same 3600s (60 min) timeout.

### Registration

In `src/shadowcoder/agents/__init__.py`:
```python
from shadowcoder.agents.codex import CodexAgent
AgentRegistry.register("codex", CodexAgent)
```

### Configuration example

```yaml
clouds:
  openai:
    env: {}  # codex uses its own auth via `codex login`

models:
  o3:
    cloud: openai
    model: o3

agents:
  codex-coder:
    type: codex
    model: o3

dispatch:
  develop: codex-coder
  develop_review: [claude-coder]
```

## Acceptance Criteria

### Refactor correctness

1. All existing tests in `tests/agents/test_claude_code.py` pass (mock target updated if needed)
2. All existing tests in `tests/agents/test_registry.py` pass
3. `BaseAgent` is no longer abstract for the 4 action methods — only `_run()` is abstract
4. `ClaudeCodeAgent` does NOT duplicate any prompt-building or output-parsing logic

### CodexAgent unit tests

5. `CodexAgent._run()` correctly builds `codex exec` command with proper flags
6. `CodexAgent._run()` parses JSONL output to extract text and usage
7. `CodexAgent._run()` writes and cleans up `AGENTS.md` for system prompt injection
8. `CodexAgent` works through the standard `preflight/design/develop/review` methods (inherited from BaseAgent) — test by mocking `_run()`
9. Permission mode mapping: `auto` → `--full-auto`, `bypass` → `--dangerously-bypass-approvals-and-sandbox`

### Integration

10. `AgentRegistry.register("codex", CodexAgent)` works and agents can be instantiated from config
11. Full `pytest tests/ -v` passes with no regressions

## Files to modify

- `src/shadowcoder/agents/base.py` — lift shared logic here
- `src/shadowcoder/agents/claude_code.py` — reduce to transport only
- `src/shadowcoder/agents/__init__.py` — register codex
- `tests/agents/test_claude_code.py` — update mock targets if needed
- `tests/agents/test_registry.py` — no changes expected

## Files to create

- `src/shadowcoder/agents/codex.py` — CodexAgent implementation
- `tests/agents/test_codex.py` — CodexAgent tests
