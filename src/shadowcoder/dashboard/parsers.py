"""Parsers for issue log, feedback, and worktree data."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2})\] (.*)$")

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("error", re.compile(r"Gate FAIL|FAIL|failed|error", re.IGNORECASE)),
    ("warning", re.compile(r"回滚|revert|升级|escalat|⚠|BLOCKED", re.IGNORECASE)),
    ("success", re.compile(r"Gate PASS|PASS|DONE|✓|通过", re.IGNORECASE)),
    ("active", re.compile(r"开始|started|running|Develop R\d|Design R\d", re.IGNORECASE)),
]


@dataclass
class LogEntry:
    timestamp: str
    text: str
    category: str
    continuation: list[str] = field(default_factory=list)


def _classify(text: str) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return "info"


class LogParser:
    def __init__(self) -> None:
        self._offset: int = 0

    @staticmethod
    def parse_all(raw: str) -> list[LogEntry]:
        if not raw.strip():
            return []
        entries: list[LogEntry] = []
        current: LogEntry | None = None
        for line in raw.splitlines():
            m = _TS_RE.match(line)
            if m:
                if current is not None:
                    entries.append(current)
                current = LogEntry(
                    timestamp=m.group(2),
                    text=m.group(3),
                    category=_classify(m.group(3)),
                )
            elif current is not None:
                current.continuation.append(line)
        if current is not None:
            entries.append(current)
        return entries

    def parse_tail(self, raw: str) -> list[LogEntry]:
        new_content = raw[self._offset:]
        self._offset = len(raw)
        return self.parse_all(new_content)


@dataclass
class FeedbackSummary:
    verdict: str | None = None
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0

    @property
    def passed(self) -> bool | None:
        if self.verdict is None:
            return None
        return self.verdict == "PASSED"


class FeedbackParser:
    @staticmethod
    def summarize(data: dict) -> FeedbackSummary:
        summary = FeedbackSummary(verdict=data.get("verdict"))
        for item in data.get("items", []):
            severity = item.get("severity")
            if not severity or item.get("resolved"):
                continue
            summary.total += 1
            sev = severity.upper()
            if sev == "CRITICAL":
                summary.critical += 1
            elif sev == "HIGH":
                summary.high += 1
            elif sev == "MEDIUM":
                summary.medium += 1
            elif sev == "LOW":
                summary.low += 1
        return summary


@dataclass
class ChangedFile:
    status: str
    path: str
    stat: str = ""


class WorktreeParser:
    @staticmethod
    def parse_name_status(output: str) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        for line in output.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                files.append(ChangedFile(status=parts[0].strip(), path=parts[1].strip()))
        return files

    @staticmethod
    def parse_stat(output: str) -> dict[str, str]:
        stats: dict[str, str] = {}
        for line in output.strip().splitlines():
            if "|" not in line:
                continue
            parts = line.split("|", 1)
            if len(parts) == 2:
                path = parts[0].strip()
                stat = parts[1].strip()
                stats[path] = stat
        return stats

    @staticmethod
    def get_changed_files(worktree_path: str) -> list[ChangedFile]:
        try:
            ns = subprocess.run(
                ["git", "diff", "--name-status", "HEAD~1"],
                cwd=worktree_path, capture_output=True, text=True, timeout=10,
            )
            st = subprocess.run(
                ["git", "diff", "--stat", "HEAD~1"],
                cwd=worktree_path, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        files = WorktreeParser.parse_name_status(ns.stdout)
        stats = WorktreeParser.parse_stat(st.stdout)
        for f in files:
            f.stat = stats.get(f.path, "")
        return files
