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
