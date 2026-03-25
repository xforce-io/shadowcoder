from __future__ import annotations

from datetime import datetime
from pathlib import Path

import frontmatter

from shadowcoder.core.config import Config
from shadowcoder.core.models import (
    InvalidTransitionError,
    Issue,
    IssueStatus,
    VALID_TRANSITIONS,
)
from shadowcoder.agents.types import ReviewComment, ReviewOutput, Severity


class IssueStore:
    _ISSUE_GLOB = "[0-9][0-9][0-9][0-9].md"

    def __init__(self, repo_path: str, config: Config):
        self.base = Path(repo_path) / config.get_issue_dir()

    def _next_id(self) -> int:
        existing = list(self.base.glob(self._ISSUE_GLOB))
        if not existing:
            return 1
        return max(int(f.stem) for f in existing) + 1

    def _log_path(self, issue_id: int) -> Path:
        return self.base / f"{issue_id:04d}.log.md"

    def _feedback_path(self, issue_id: int) -> Path:
        return self.base / f"{issue_id:04d}.feedback.json"

    def load_feedback(self, issue_id: int) -> dict:
        path = self._feedback_path(issue_id)
        if not path.exists():
            return {"items": [], "proposed_tests": [],
                    "acceptance_tests": [], "supplementary_tests": []}
        import json
        fb = json.loads(path.read_text(encoding="utf-8"))
        # Migrate: ensure new keys exist
        fb.setdefault("acceptance_tests", [])
        fb.setdefault("supplementary_tests", [])
        return fb

    def save_feedback(self, issue_id: int, feedback: dict) -> None:
        path = self._feedback_path(issue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8")

    def _versions_dir(self, issue_id: int) -> Path:
        return self.base / f"{issue_id:04d}.versions"

    def save_version(self, issue_id: int, action: str, round_num: int, content: str) -> str:
        """Save a versioned snapshot of agent output. Returns the filename."""
        vdir = self._versions_dir(issue_id)
        vdir.mkdir(parents=True, exist_ok=True)
        filename = f"{action}_r{round_num}.md"
        path = vdir / filename
        path.write_text(content, encoding="utf-8")
        return filename

    def create(self, title: str, priority: str = "medium",
               tags: list[str] | None = None,
               description: str | None = None) -> Issue:
        sections: dict[str, str] = {}
        if description:
            sections["需求"] = description
        issue = Issue(
            id=self._next_id(),
            title=title,
            status=IssueStatus.CREATED,
            priority=priority,
            created=datetime.now(),
            updated=datetime.now(),
            tags=tags or [],
            sections=sections,
        )
        self._save(issue)
        return issue

    def get(self, issue_id: int) -> Issue:
        path = self.base / f"{issue_id:04d}.md"
        if not path.exists():
            raise FileNotFoundError(f"Issue {issue_id} not found: {path}")
        post = frontmatter.load(str(path))
        return Issue(
            id=post["id"],
            title=post["title"],
            status=IssueStatus(post["status"]),
            priority=post["priority"],
            created=datetime.fromisoformat(post["created"]),
            updated=datetime.fromisoformat(post["updated"]),
            tags=post.get("tags", []),
            assignee=post.get("assignee"),
            sections=self._markdown_to_sections(post.content),
        )

    def list_all(self) -> list[Issue]:
        if not self.base.exists():
            return []
        return [self.get(int(f.stem)) for f in sorted(self.base.glob(self._ISSUE_GLOB))]

    def list_by_status(self, status: IssueStatus) -> list[Issue]:
        return [i for i in self.list_all() if i.status == status]

    def list_by_tag(self, tag: str) -> list[Issue]:
        return [i for i in self.list_all() if tag in i.tags]

    def transition_status(self, issue_id: int, new_status: IssueStatus) -> None:
        issue = self.get(issue_id)
        if new_status not in VALID_TRANSITIONS[issue.status]:
            raise InvalidTransitionError(issue.status, new_status)
        issue.status = new_status
        self._save(issue)

    def update_section(self, issue_id: int, section: str, content: str) -> None:
        issue = self.get(issue_id)
        issue.sections[section] = content
        self._save(issue)

    def append_review(self, issue_id: int, section: str, review: ReviewOutput) -> None:
        # .md: only latest review summary (overwrite)
        from shadowcoder.core.models import IssueStatus  # avoid circular at module level
        critical = sum(1 for c in review.comments if c.severity.value == "critical")
        high = sum(1 for c in review.comments if c.severity.value == "high")
        if critical > 0:
            verdict = "NOT PASSED"
        elif high == 0:
            verdict = "PASSED"
        elif high <= 2:
            verdict = "PASSED (conditional)"
        else:
            verdict = "NOT PASSED"
        summary = f"{verdict} (CRITICAL={critical}, HIGH={high}, {len(review.comments)} comments total)"
        issue = self.get(issue_id)
        issue.sections[section] = summary
        self._save(issue)

        # .log.md: full review content (append) — include verdict for traceability
        formatted = self._format_review(review)
        self.append_log(issue_id, f"{section}\n{summary}\n{formatted}")

    def append_log(self, issue_id: int, entry: str) -> None:
        path = self._log_path(issue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"\n\n## [{ts}] {entry}"
        with open(path, "a", encoding="utf-8") as f:
            f.write(log_entry)

    def get_log(self, issue_id: int) -> str:
        path = self._log_path(issue_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def assign(self, issue_id: int, agent_name: str) -> None:
        issue = self.get(issue_id)
        issue.assignee = agent_name
        self._save(issue)

    def _save(self, issue: Issue) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(
            content=self._sections_to_markdown(issue.sections),
            id=issue.id,
            title=issue.title,
            status=issue.status.value,
            priority=issue.priority,
            created=issue.created.isoformat(),
            updated=datetime.now().isoformat(),
            tags=issue.tags,
            assignee=issue.assignee,
        )
        path = self.base / f"{issue.id:04d}.md"
        path.write_text(frontmatter.dumps(post), encoding="utf-8")

    @staticmethod
    def _format_review(review: ReviewOutput) -> str:
        critical = sum(1 for c in review.comments if c.severity.value == "critical")
        high = sum(1 for c in review.comments if c.severity.value == "high")
        lines = [f"**Reviewer: {review.reviewer}** — CRITICAL={critical}, HIGH={high}"]
        for c in review.comments:
            loc = f" ({c.location})" if c.location else ""
            lines.append(f"- [{c.severity.value.upper()}]{loc} {c.message}")
        return "\n".join(lines)

    _SECTION_PREFIX = "<!-- section: "
    _SECTION_SUFFIX = " -->"

    @classmethod
    def _sections_to_markdown(cls, sections: dict[str, str]) -> str:
        if not sections:
            return ""
        # Use HTML comments as section delimiters — they won't conflict
        # with any markdown content the agent produces.
        parts = []
        for k, v in sections.items():
            parts.append(f"{cls._SECTION_PREFIX}{k}{cls._SECTION_SUFFIX}\n{v}")
        return "\n\n".join(parts)

    @classmethod
    def _markdown_to_sections(cls, content: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current_key: str | None = None
        lines: list[str] = []
        for line in content.split("\n"):
            if line.startswith(cls._SECTION_PREFIX) and line.endswith(cls._SECTION_SUFFIX):
                if current_key:
                    sections[current_key] = "\n".join(lines).strip()
                current_key = line[len(cls._SECTION_PREFIX):-len(cls._SECTION_SUFFIX)]
                lines = []
            else:
                lines.append(line)
        if current_key:
            sections[current_key] = "\n".join(lines).strip()
        return sections
