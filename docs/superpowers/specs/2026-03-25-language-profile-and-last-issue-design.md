# LanguageProfile & Last Issue Pointer

Two independent refactors from dolphin experiment observations.

## 1. LanguageProfile — Gate Language Abstraction

### Problem

Language-specific if-else chains in `engine.py`: `_detect_test_command`, `_run_individual_test`, `_detect_stray_files`, `_extract_gate_failure_summary`, `_PASS_PATTERNS`, `_SKIP_PATTERNS`. Adding a new language requires touching 4+ methods.

### Solution

New file `src/shadowcoder/core/language.py`:

```python
@dataclass(frozen=True)
class LanguageProfile:
    name: str
    marker_files: tuple[str, ...]       # ("Cargo.toml",)
    test_command: str                    # "cargo test 2>&1"
    individual_test_cmd: str             # "cargo test {name} -- --include-ignored 2>&1"
    source_extensions: tuple[str, ...]   # (".rs",)
    pass_patterns: tuple[str, ...]       # (" ... ok", " PASSED")
    skip_patterns: tuple[str, ...]       # (" ... ignored",)
    gate_failure_patterns: tuple[str, ...] # (r"panicked at",)

PROFILES: list[LanguageProfile] = [
    LanguageProfile(name="rust", marker_files=("Cargo.toml",), ...),
    LanguageProfile(name="go", marker_files=("go.mod",), ...),
    LanguageProfile(name="node", marker_files=("package.json",), ...),
    LanguageProfile(name="python", marker_files=("pyproject.toml", "setup.py"), ...),
    LanguageProfile(name="make", marker_files=("Makefile",), ...),
]

def detect_language(worktree_path: str) -> LanguageProfile | None:
    """Check marker files in order, return first match or None."""
```

### Changes to engine.py

Replace `_detect_test_command`, `_run_individual_test`, `_detect_stray_files`, `_extract_gate_failure_summary`, `_PASS_PATTERNS`, `_SKIP_PATTERNS` with calls to a cached profile:

```python
from shadowcoder.core.language import detect_language

# In _gate_check:
profile = detect_language(worktree_path)
if not profile:
    raise RuntimeError(...)
test_cmd = profile.test_command

# In acceptance test checking:
if any(f"{name}{pat}" in output for pat in profile.skip_patterns): ...
if not any(f"{name}{pat}" in output for pat in profile.pass_patterns): ...

# Individual test:
cmd = profile.individual_test_cmd.format(name=test_name)

# Stray files:
stray = [f for f in untracked if "/" not in f
         and any(f.endswith(ext) for ext in profile.source_extensions)]

# Gate failure summary:
for pat in profile.gate_failure_patterns:
    if _re.search(pat, stripped): summary_parts.append(stripped)
```

Remove: `_detect_test_command`, `_run_individual_test` methods entirely. Inline the profile-based logic in `_gate_check` and `_extract_gate_failure_summary`. Keep `_PASS_PATTERNS`/`_SKIP_PATTERNS` as fallback only if profile detection fails.

---

## 2. Last Issue Pointer — `run` without arguments

### Problem

Each `run "title"` creates a new issue. Retrying the same requirement creates #1, #2, #3... User must `list` to find the ID, then `run <id>`.

### Solution

**Storage:** `.shadowcoder/last_issue` file containing one integer (the issue ID).

**IssueStore changes:**
```python
def save_last(self, issue_id: int) -> None:
    (Path(self.repo_path) / ".shadowcoder" / "last_issue").write_text(str(issue_id))

def get_last(self) -> int | None:
    p = Path(self.repo_path) / ".shadowcoder" / "last_issue"
    if p.exists():
        return int(p.read_text().strip())
    return None
```

**Engine `_on_run` changes:**

1. After creating or loading an issue, call `save_last(issue_id)`.
2. Support `run` with no issue_id and no title: read `get_last()`.

**run_real.py changes:**

```python
elif command == "run":
    if args and args[0].isdigit():
        payload = {"issue_id": int(args[0])}
    elif not args:
        # No arguments: resume last issue
        payload = {"resume_last": True}
    else:
        # Title given: create new issue
        ...
```

**Engine `_on_run`:**
```python
if msg.payload.get("resume_last"):
    issue_id = self.issue_store.get_last()
    if issue_id is None:
        raise RuntimeError("No previous issue to resume")
```

**Terminal state rerun:**

Extend the recovery block to handle CANCELLED and DONE:

```python
if issue.status in (IssueStatus.IN_PROGRESS, IssueStatus.FAILED):
    # existing recovery...
elif issue.status == IssueStatus.CANCELLED:
    self._log(issue_id, "run 恢复: cancelled → 重跑 design")
    issue.status = IssueStatus.CREATED
    self.issue_store.save(issue)
elif issue.status == IssueStatus.DONE:
    self._log(issue_id, "run 恢复: done → 重跑 develop")
    issue.status = IssueStatus.APPROVED
    self.issue_store.save(issue)
```

DONE → APPROVED is already a valid transition (see models.py line 36).
CANCELLED needs a new valid transition to CREATED.

---

## File Change Summary

| File | Changes |
|------|---------|
| `src/shadowcoder/core/language.py` | New: LanguageProfile, PROFILES, detect_language |
| `src/shadowcoder/core/engine.py` | Remove 4 methods, use profile; last_issue save; CANCELLED/DONE recovery |
| `src/shadowcoder/core/issue_store.py` | save_last, get_last methods |
| `src/shadowcoder/core/models.py` | CANCELLED → CREATED transition |
| `scripts/run_real.py` | `run` with no args reads last_issue |
| `tests/core/test_language.py` | New: profile detection, patterns |
| `tests/core/test_engine.py` | Gate tests updated for profile |
| `tests/test_integration.py` | last_issue + CANCELLED/DONE recovery tests |
