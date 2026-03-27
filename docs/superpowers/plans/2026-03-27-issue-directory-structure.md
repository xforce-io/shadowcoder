# Issue Directory Structure Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move each issue's files from a flat layout (`NNNN.md`, `NNNN.log`, etc.) into a per-issue subdirectory (`NNNN/issue.md`, `NNNN/issue.log`, etc.).

**Architecture:** Change path helper methods in `IssueStore` to route into `NNNN/` subdirectories, update the one reference in `engine.py`, and ship a migration script for existing repos.

**Tech Stack:** Python, pathlib, pytest

---

### Task 1: Add failing path-verification tests to `test_issue_store.py`

**Files:**
- Modify: `tests/core/test_issue_store.py`

These tests assert the new directory layout. They will FAIL against current code (files are at the top level), and PASS after Task 2.

- [ ] **Step 1: Append the four new tests**

Add to the bottom of `tests/core/test_issue_store.py`:

```python
def test_issue_files_in_subdirectory(store):
    """issue.md must live inside NNNN/ subdirectory, not at issues/ root."""
    store.create("Test")
    issue_dir = store.base / "0001"
    assert issue_dir.is_dir()
    assert (issue_dir / "issue.md").exists()


def test_log_in_subdirectory(store):
    """issue.log must live inside NNNN/ subdirectory."""
    store.create("Test")
    store.append_log(1, "entry")
    assert (store.base / "0001" / "issue.log").exists()


def test_feedback_in_subdirectory(store):
    """feedback.json must live inside NNNN/ subdirectory."""
    store.create("Test")
    store.save_feedback(1, {"items": []})
    assert (store.base / "0001" / "feedback.json").exists()


def test_versions_in_subdirectory(store):
    """versions/ must live inside NNNN/ subdirectory."""
    store.create("Test")
    store.save_version(1, "design", 1, "content")
    assert (store.base / "0001" / "versions" / "design_r1.md").exists()
```

- [ ] **Step 2: Run new tests to confirm they FAIL**

```bash
pytest tests/core/test_issue_store.py::test_issue_files_in_subdirectory \
       tests/core/test_issue_store.py::test_log_in_subdirectory \
       tests/core/test_issue_store.py::test_feedback_in_subdirectory \
       tests/core/test_issue_store.py::test_versions_in_subdirectory -v
```

Expected: 4 × FAILED (files found at root, not inside `0001/`)

- [ ] **Step 3: Commit**

```bash
git add tests/core/test_issue_store.py
git commit -m "test: add failing path-verification tests for per-issue subdirectory"
```

---

### Task 2: Refactor path methods in `issue_store.py`

**Files:**
- Modify: `src/shadowcoder/core/issue_store.py`

- [ ] **Step 1: Replace `_ISSUE_GLOB` and add `_issue_dir` helper**

Replace:
```python
_ISSUE_GLOB = "[0-9][0-9][0-9][0-9].md"
```
With:
```python
_ISSUE_GLOB = "[0-9][0-9][0-9][0-9]"

def _issue_dir(self, issue_id: int) -> Path:
    return self.base / f"{issue_id:04d}"
```

- [ ] **Step 2: Update `_next_id`**

Replace:
```python
def _next_id(self) -> int:
    existing = list(self.base.glob(self._ISSUE_GLOB))
    if not existing:
        return 1
    return max(int(f.stem) for f in existing) + 1
```
With:
```python
def _next_id(self) -> int:
    existing = [f for f in self.base.glob(self._ISSUE_GLOB) if f.is_dir()]
    if not existing:
        return 1
    return max(int(f.name) for f in existing) + 1
```

- [ ] **Step 3: Update `_log_path` and `_feedback_path`**

Replace:
```python
def _log_path(self, issue_id: int) -> Path:
    return self.base / f"{issue_id:04d}.log"

def _feedback_path(self, issue_id: int) -> Path:
    return self.base / f"{issue_id:04d}.feedback.json"
```
With:
```python
def _log_path(self, issue_id: int) -> Path:
    return self._issue_dir(issue_id) / "issue.log"

def _feedback_path(self, issue_id: int) -> Path:
    return self._issue_dir(issue_id) / "feedback.json"
```

- [ ] **Step 4: Update `_versions_dir`**

Replace:
```python
def _versions_dir(self, issue_id: int) -> Path:
    return self.base / f"{issue_id:04d}.versions"
```
With:
```python
def _versions_dir(self, issue_id: int) -> Path:
    return self._issue_dir(issue_id) / "versions"
```

- [ ] **Step 5: Update `get`**

Replace inside `get`:
```python
path = self.base / f"{issue_id:04d}.md"
if not path.exists():
    raise FileNotFoundError(f"Issue {issue_id} not found: {path}")
```
With:
```python
path = self._issue_dir(issue_id) / "issue.md"
if not path.exists():
    raise FileNotFoundError(f"Issue {issue_id} not found: {path}")
```

- [ ] **Step 6: Update `list_all`**

Replace:
```python
def list_all(self) -> list[Issue]:
    if not self.base.exists():
        return []
    return [self.get(int(f.stem)) for f in sorted(self.base.glob(self._ISSUE_GLOB))]
```
With:
```python
def list_all(self) -> list[Issue]:
    if not self.base.exists():
        return []
    dirs = sorted(
        f for f in self.base.glob(self._ISSUE_GLOB) if f.is_dir()
    )
    return [self.get(int(d.name)) for d in dirs]
```

- [ ] **Step 7: Update `_save`**

Replace inside `_save`:
```python
def _save(self, issue: Issue) -> None:
    self.base.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        ...
    )
    path = self.base / f"{issue.id:04d}.md"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
```
With:
```python
def _save(self, issue: Issue) -> None:
    issue_dir = self._issue_dir(issue.id)
    issue_dir.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        ...
    )
    path = issue_dir / "issue.md"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
```

(Keep all `frontmatter.Post(...)` arguments exactly as they are — only change the directory creation and path.)

- [ ] **Step 8: Run the full issue_store test suite**

```bash
pytest tests/core/test_issue_store.py tests/core/test_version_archive.py -v
```

Expected: all tests PASS (including the 4 new ones from Task 1)

- [ ] **Step 9: Commit**

```bash
git add src/shadowcoder/core/issue_store.py
git commit -m "refactor: move issue files into per-issue subdirectory (NNNN/)"
```

---

### Task 3: Update `_acceptance_script_path` in `engine.py`

**Files:**
- Modify: `src/shadowcoder/core/engine.py:677-679`

- [ ] **Step 1: Update the path**

Replace:
```python
def _acceptance_script_path(self, issue_id: int) -> Path:
    """Path for the acceptance test script."""
    return Path(self.issue_store.base) / f"{issue_id:04d}.acceptance.sh"
```
With:
```python
def _acceptance_script_path(self, issue_id: int) -> Path:
    """Path for the acceptance test script."""
    return Path(self.issue_store.base) / f"{issue_id:04d}" / "acceptance.sh"
```

- [ ] **Step 2: Run engine and integration tests**

```bash
pytest tests/core/test_engine.py tests/test_integration.py -v -x
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/shadowcoder/core/engine.py
git commit -m "refactor: update acceptance script path to NNNN/acceptance.sh"
```

---

### Task 4: Write migration script

**Files:**
- Create: `scripts/migrate_issues.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Migrate .shadowcoder/issues/ from flat layout to per-issue subdirectories.

Usage:
    python scripts/migrate_issues.py <repo_path>            # dry-run (safe)
    python scripts/migrate_issues.py <repo_path> --execute  # actually move files
"""
import re
import shutil
import sys
from pathlib import Path


def migrate(issues_dir: Path, execute: bool) -> None:
    if not issues_dir.exists():
        print(f"Not found: {issues_dir}")
        sys.exit(1)

    pattern = re.compile(r"^(\d{4})\.md$")
    found = sorted(f for f in issues_dir.iterdir() if pattern.match(f.name))

    if not found:
        print("No issues found to migrate.")
        return

    for md_file in found:
        num = md_file.stem  # e.g. "0001"
        issue_dir = issues_dir / num

        moves = [
            (issues_dir / f"{num}.md",              issue_dir / "issue.md"),
            (issues_dir / f"{num}.log",              issue_dir / "issue.log"),
            (issues_dir / f"{num}.feedback.json",    issue_dir / "feedback.json"),
            (issues_dir / f"{num}.acceptance.sh",    issue_dir / "acceptance.sh"),
            (issues_dir / f"{num}.versions",         issue_dir / "versions"),
        ]

        print(f"\nIssue {num}:")
        for src, dst in moves:
            if src.exists():
                print(f"  {'MOVE' if execute else 'would move'}: {src.name} → {num}/{dst.name}")
                if execute:
                    issue_dir.mkdir(exist_ok=True)
                    shutil.move(str(src), str(dst))
            else:
                print(f"  skip (not found): {src.name}")

    if not execute:
        print("\nDry-run complete. Pass --execute to apply changes.")
    else:
        print("\nMigration complete.")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    repo_path = Path(sys.argv[1])
    execute = "--execute" in sys.argv
    issues_dir = repo_path / ".shadowcoder" / "issues"
    migrate(issues_dir, execute)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run dry-run against the experiment repo**

```bash
python scripts/migrate_issues.py ~/dev/github/<your-experiment-repo>
```

Expected: printed list of files that would be moved, no files actually moved yet.

- [ ] **Step 3: Run `--execute` to migrate**

```bash
python scripts/migrate_issues.py ~/dev/github/<your-experiment-repo> --execute
```

Expected: files moved, printed confirmation. Verify with:

```bash
ls ~/dev/github/<your-experiment-repo>/.shadowcoder/issues/
# Should show: 0001/ 0002/ ... last_issue
ls ~/dev/github/<your-experiment-repo>/.shadowcoder/issues/0001/
# Should show: issue.md issue.log feedback.json acceptance.sh versions/
```

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_issues.py
git commit -m "feat: add migrate_issues.py script for flat → subdirectory layout"
```
