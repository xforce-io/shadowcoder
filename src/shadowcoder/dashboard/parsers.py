"""Parsers for issue log, feedback, and worktree data."""
from __future__ import annotations

import re
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
