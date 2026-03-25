# Zero-Config, GitHub Issue URL, README Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zero-config onboarding, GitHub issue URL as task source, README that leads with simplicity.

**Architecture:** Config falls back to built-in defaults when no file exists. Engine extracts title from GitHub issue content when title is omitted. README restructured to lead with quick start.

**Tech Stack:** Python, pytest, gh CLI

---

### Task 1: Zero-Config — Config Defaults

**Files:**
- Modify: `src/shadowcoder/core/config.py`
- Modify: `tests/core/test_config.py`

- [ ] **Step 1: Update test — missing config should return defaults, not raise**

In `tests/core/test_config.py`, replace `test_missing_config_file` and add new tests:

```python
def test_missing_config_uses_defaults():
    """No config file → built-in defaults, no error."""
    config = Config("/nonexistent/config.yaml")
    assert config.get_agent_for_phase("design") == "default"
    assert config.get_agent_for_phase("design_review") == ["default"]
    ac = config.get_agent_config("default")
    assert ac["type"] == "claude_code"
    assert ac["model"] == "sonnet"
    assert "env" not in ac or ac.get("env") is None


def test_missing_config_getter_defaults():
    """All getters return sensible defaults with no config file."""
    config = Config("/nonexistent/config.yaml")
    assert config.get_max_review_rounds() == 3
    assert config.get_max_test_retries() == 3
    assert config.get_max_budget_usd() is None
    assert config.get_issue_dir() == ".shadowcoder/issues"
    assert config.get_worktree_dir() == ".shadowcoder/worktrees"
    assert config.get_pass_threshold() == "no_critical"
    assert config.get_gate_mode() == "standard"
    assert config.get_test_command() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_config.py::test_missing_config_uses_defaults tests/core/test_config.py::test_missing_config_getter_defaults -v`
Expected: FAIL — `FileNotFoundError` raised

- [ ] **Step 3: Implement default config fallback**

In `src/shadowcoder/core/config.py`, modify `__init__` and add `_default_data`:

```python
def __init__(self, path: str = "~/.shadowcoder/config.yaml"):
    resolved = Path(path).expanduser()
    if not resolved.exists():
        self._data = self._default_data()
    else:
        with open(resolved) as f:
            self._data: dict = yaml.safe_load(f) or {}
        self._validate()

@staticmethod
def _default_data() -> dict:
    return {
        "clouds": {},
        "models": {
            "sonnet": {"model": "sonnet"},
        },
        "agents": {
            "default": {"type": "claude_code", "model": "sonnet"},
        },
        "dispatch": {
            "design": "default",
            "develop": "default",
            "design_review": ["default"],
            "develop_review": ["default"],
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/shadowcoder/core/config.py tests/core/test_config.py
git commit -m "feat: zero-config startup with built-in defaults"
```

---

### Task 2: GitHub Issue URL — Extract Title from Content

**Files:**
- Modify: `src/shadowcoder/core/engine.py` (method `_on_create`)
- Modify: `scripts/run_real.py` (commands `create` and `run`)
- Modify: `tests/core/test_engine.py`

- [ ] **Step 1: Write test for title extraction from GitHub issue URL**

In `tests/core/test_engine.py`, add a test that verifies when title is empty and description is a GitHub issue URL, the title is extracted from fetched content:

```python
@pytest.mark.asyncio
async def test_create_from_github_issue_url_extracts_title(engine, bus, issue_store, monkeypatch):
    """When --from is a GitHub issue URL and no title given, extract title from content."""
    def fake_fetch(url):
        return "# Add user authentication\n\nImplement OAuth2 login flow."
    monkeypatch.setattr(Engine, "_fetch_url_content", staticmethod(fake_fetch))

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "",
        "description": "https://github.com/owner/repo/issues/42",
    }))
    issues = issue_store.list_all()
    assert len(issues) == 1
    assert issues[0].title == "Add user authentication"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_engine.py::test_create_from_github_issue_url_extracts_title -v`
Expected: FAIL — title is empty string

- [ ] **Step 3: Implement title extraction in `_on_create`**

In `src/shadowcoder/core/engine.py`, modify `_on_create`:

```python
async def _on_create(self, msg):
    title = msg.payload.get("title", "")
    priority = msg.payload.get("priority", "medium")
    tags = msg.payload.get("tags")
    description = msg.payload.get("description")
    if description and Path(description).is_file():
        description = Path(description).read_text(encoding="utf-8")
    elif description and description.startswith(("http://", "https://")):
        description = self._fetch_url_content(description)
    # Extract title from fetched content if not provided
    if not title and description and description.startswith("# "):
        first_line = description.split("\n", 1)[0]
        title = first_line.removeprefix("# ").strip()
    if not title:
        title = "Untitled"
    issue = self.issue_store.create(title, priority=priority, tags=tags,
                                    description=description)
    self._log(issue.id, f"Issue 创建: {title}")
    await self.bus.publish(Message(MessageType.EVT_ISSUE_CREATED,
        {"issue_id": issue.id, "title": issue.title}))
```

- [ ] **Step 4: Update `run_real.py` — allow `--from` without title**

In `scripts/run_real.py`, modify both `create` and `run` command handlers to allow empty title when `--from` is provided:

For `create` (around line 77):
```python
elif command == "create":
    title_parts = []
    description = None
    i = 0
    while i < len(args):
        if args[i] == "--from" and i + 1 < len(args):
            source = args[i + 1]
            if source.startswith(("http://", "https://")):
                description = source
            else:
                desc_path = Path(repo_path) / source
                if not desc_path.exists():
                    desc_path = Path(source)
                description = str(desc_path)
            i += 2
        else:
            title_parts.append(args[i])
            i += 1
    payload = {"title": " ".join(title_parts)}  # may be empty string
    if description:
        payload["description"] = description
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, payload))
```

For `run` (around line 106), same change — allow title_parts to be empty when description exists:
```python
# In the else branch (title_parts parsing):
payload = {"title": " ".join(title_parts)}
if description:
    payload["description"] = description
# Only go through create path if title or description exists
if payload.get("title") or payload.get("description"):
    await bus.publish(Message(MessageType.CMD_RUN, payload))
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/shadowcoder/core/engine.py scripts/run_real.py tests/core/test_engine.py
git commit -m "feat: extract title from GitHub issue URL when --from used without title"
```

---

### Task 3: README Rewrite (EN)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README.md**

New structure:
1. One-liner + language switcher
2. Quick Start (install + one command, zero config)
3. What It Does (loop diagram, brief explanation)
4. Validated Results
5. Configuration (zero-config default → advanced multi-model)
6. Usage (full CLI, including `--from <github-issue-url>`)
7. How It Works (neural-symbolic theory, architecture, audit trail)
8. Known Limitations + Roadmap
9. License

Key changes:
- Open with: "Point it at a repo. Give it a task. It codes until it works."
- Quick Start shows `--from` with GitHub issue URL as primary example
- Configuration section: "No config needed" first, then advanced
- Move neural-symbolic table, agent abstraction, architecture to "How It Works"

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README to lead with simplicity and zero-config"
```

---

### Task 4: README Rewrite (CN)

**Files:**
- Modify: `README_CN.md`

- [ ] **Step 1: Rewrite README_CN.md**

Mirror the EN structure exactly. Translate new sections. Keep technical terms (Claude Code, GitHub, CLI, etc.) in English.

- [ ] **Step 2: Commit**

```bash
git add README_CN.md
git commit -m "docs: rewrite Chinese README to match English version"
```
