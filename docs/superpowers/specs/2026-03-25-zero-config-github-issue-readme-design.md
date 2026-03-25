# Zero-Config, GitHub Issue URL, README Rewrite

Date: 2026-03-25

## Problem

1. Users must create `~/.shadowcoder/config.yaml` before first run — unnecessary friction when local Claude Code CLI is already installed and authenticated.
2. When requirements live in a GitHub issue, users must manually copy-paste into a file. `--from` already supports URLs but doesn't extract the issue title — users still need to provide it separately.
3. README leads with theory (neural-symbolic) instead of the core value prop: point at a repo, give it a task, it codes until it works.

## Changes

### 1. Zero-Config Startup

**Goal**: `pip install shadowcoder && shadowcoder /path/to/repo run "Add auth"` works with no config file.

**Config.__init__**: When `~/.shadowcoder/config.yaml` doesn't exist, use built-in defaults instead of raising `FileNotFoundError`.

Default config equivalent:

```yaml
clouds: {}
models:
  sonnet:
    model: sonnet
agents:
  default:
    type: claude_code
    model: sonnet
dispatch:
  design: default
  develop: default
  design_review: [default]
  develop_review: [default]
```

This uses the local Claude Code CLI with model "sonnet", no custom env vars. All other settings already have defaults in their getter methods.

**Implementation**:
- `Config.__init__`: if file not found, set `self._data = {}` and populate with `_default_data()`.
- `_default_data()`: returns the dict above.
- `_validate()`: skip validation when using defaults (no cross-references to break).
- No changes to any other file — all consumers already handle the defaults from getter methods.

### 2. GitHub Issue URL as Task Source

**Goal**: `shadowcoder /repo run --from https://github.com/owner/repo/issues/42` — no title needed.

Currently `_fetch_url_content` already fetches GitHub issue title + body. But the CLI requires a separate title argument. When `--from` is a GitHub issue URL and no title is given, we should extract the title from the fetched content.

**Flow change in run_real.py**:
- `run` and `create` commands: when `--from` is a GitHub issue URL and no title provided, pass `description` without `title`.
- Engine `_on_create`: when `title` is empty/missing but `description` is a GitHub issue URL, fetch the issue and extract title from the response.

**Flow change in engine.py `_on_create`**:
- After fetching URL content, if title is empty and content starts with `# <title>` (which `_fetch_url_content` already produces for GitHub issues), extract the first line as title.

**Branch naming**: Already works — `WorktreeManager._branch_name` uses the issue title to generate `fix/{id}-{slug}`. The GitHub issue title flows through naturally.

### 3. README Rewrite

**Goal**: 30-second understanding of what it does and how to use it. Theory moves down.

**New structure** (both EN and CN):

```
1. One-liner description
2. Quick Start (3 lines: install, run, done)
3. What It Does (the loop diagram, brief)
4. Validated Results (proof it works)
5. Configuration (zero-config default, advanced config for multi-model)
6. Usage (full CLI reference)
7. How It Works (neural-symbolic theory, architecture)
8. Known Limitations
9. License
```

Key messaging changes:
- Lead: "Point it at a repo. Give it a task. It codes until it works."
- Quick Start shows zero-config path first, config file only for advanced use
- GitHub issue URL shown as a primary usage pattern
- Theory/architecture moved to "How It Works" section near bottom

## Files Changed

| File | Change |
|------|--------|
| `src/shadowcoder/core/config.py` | Default config when no file exists |
| `src/shadowcoder/core/engine.py` | Extract title from GitHub issue URL when title is empty |
| `scripts/run_real.py` | Allow `--from` without title for GitHub issue URLs |
| `README.md` | Rewrite (EN) |
| `README_CN.md` | Rewrite (CN) |
| `tests/core/test_config.py` | Test zero-config behavior |

## Not In Scope

- Auto-closing GitHub issues after completion
- Storing GitHub issue URL as metadata on the shadowcoder issue
- `shadowcoder` CLI entry point (keep `python scripts/run_real.py` for now)
