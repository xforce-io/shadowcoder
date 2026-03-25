from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageProfile:
    """Language-specific configuration for gate checks."""
    name: str
    marker_files: tuple[str, ...]
    test_command: str
    individual_test_cmd: str
    source_extensions: tuple[str, ...]
    pass_patterns: tuple[str, ...]
    skip_patterns: tuple[str, ...]
    gate_failure_patterns: tuple[str, ...]


PROFILES: list[LanguageProfile] = [
    LanguageProfile(
        name="rust",
        marker_files=("Cargo.toml",),
        test_command="cargo test 2>&1",
        individual_test_cmd="cargo test {name} -- --include-ignored 2>&1",
        source_extensions=(".rs",),
        pass_patterns=(" ... ok", " PASSED", " passed"),
        skip_patterns=(" ... ignored",),
        gate_failure_patterns=(r"panicked at",),
    ),
    LanguageProfile(
        name="go",
        marker_files=("go.mod",),
        test_command="go test ./... 2>&1",
        individual_test_cmd="go test -run {name} -v ./... 2>&1",
        source_extensions=(".go",),
        pass_patterns=(" PASSED", " passed", "PASS: "),
        skip_patterns=(" SKIPPED", " skipped"),
        gate_failure_patterns=(r"^--- FAIL:",),
    ),
    LanguageProfile(
        name="node",
        marker_files=("package.json",),
        test_command="npm test 2>&1",
        individual_test_cmd="npx jest -t {name} 2>&1",
        source_extensions=(".js", ".ts"),
        pass_patterns=(" PASSED", " passed", " ✓"),
        skip_patterns=(" SKIPPED", " skipped"),
        gate_failure_patterns=(r"^FAIL ",),
    ),
    LanguageProfile(
        name="python",
        marker_files=("pyproject.toml", "setup.py"),
        test_command="python -m pytest -v 2>&1",
        individual_test_cmd="python -m pytest -k {name} -v 2>&1",
        source_extensions=(".py",),
        pass_patterns=(" PASSED", " passed"),
        skip_patterns=(" SKIPPED", " skipped"),
        gate_failure_patterns=(r"^FAILED ", r" FAILED$", r"^E\s+\w*Error:"),
    ),
    LanguageProfile(
        name="make",
        marker_files=("Makefile",),
        test_command="make test 2>&1",
        individual_test_cmd="make test TEST={name} 2>&1",
        source_extensions=(),
        pass_patterns=(" PASSED", " passed", "PASS"),
        skip_patterns=(" SKIPPED", " skipped"),
        gate_failure_patterns=(),
    ),
]


def detect_language(worktree_path: str) -> LanguageProfile | None:
    """Detect project language from marker files. Returns first match or None."""
    p = Path(worktree_path)
    for profile in PROFILES:
        for marker in profile.marker_files:
            if (p / marker).exists():
                return profile
    return None
