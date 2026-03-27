# Issue Directory Structure Refactor

**Date:** 2026-03-27
**Status:** Approved

## Problem

Each issue's files are split between a `NNNN.versions/` subdirectory and several flat siblings (`NNNN.md`, `NNNN.log`, `NNNN.feedback.json`, `NNNN.acceptance.sh`). This is visually cluttered and makes it harder to move or archive a single issue.

## New Structure

```
.shadowcoder/issues/
  0001/
    issue.md          ← was 0001.md
    issue.log         ← was 0001.log
    feedback.json     ← was 0001.feedback.json
    acceptance.sh     ← was 0001.acceptance.sh
    versions/         ← was 0001.versions/
      design_r1.md
      develop_r1.md
  0002/
    ...
  last_issue          ← unchanged, stays at issues/ root
```

## Code Changes

### `src/shadowcoder/core/issue_store.py`

- `_ISSUE_GLOB`: change from `"[0-9][0-9][0-9][0-9].md"` to `"[0-9][0-9][0-9][0-9]"` (match directories)
- `_next_id()`: scan for numeric directory names instead of `.md` files
- `_log_path(id)`: `base / f"{id:04d}" / "issue.log"`
- `_feedback_path(id)`: `base / f"{id:04d}" / "feedback.json"`
- `_versions_dir(id)`: `base / f"{id:04d}" / "versions"`
- `_save(issue)`: write to `base / f"{id:04d}" / "issue.md"`
- `get(id)`: read from `base / f"{id:04d}" / "issue.md"`
- `list_all()`: glob directories, filter by name pattern

### `src/shadowcoder/core/engine.py`

- `_acceptance_script_path(id)`: `issue_store.base / f"{id:04d}" / "acceptance.sh"`

## Migration Script

`scripts/migrate_issues.py` — migrates an existing `.shadowcoder/issues/` directory.

**Usage:**
```bash
python scripts/migrate_issues.py <repo_path>           # dry-run (default)
python scripts/migrate_issues.py <repo_path> --execute # actually move files
```

**Per issue (for each `NNNN.md` found):**
1. Create `NNNN/` directory
2. Move `NNNN.md` → `NNNN/issue.md`
3. Move `NNNN.log` → `NNNN/issue.log` (if exists)
4. Move `NNNN.feedback.json` → `NNNN/feedback.json` (if exists)
5. Move `NNNN.acceptance.sh` → `NNNN/acceptance.sh` (if exists)
6. Move `NNNN.versions/` → `NNNN/versions/` (if exists)

## Testing

- Existing unit tests in `tests/` cover `IssueStore` — update path expectations
- Run `python -m pytest tests/ -v` after changes
- Run migration script in dry-run mode on a real `.shadowcoder/` before `--execute`
