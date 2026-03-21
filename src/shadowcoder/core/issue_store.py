from __future__ import annotations

from datetime import datetime
from pathlib import Path

import frontmatter

from shadowcoder.core.config import Config
from shadowcoder.core.models import (
    InvalidTransitionError,
    Issue,
    IssueStatus,
    ReviewResult,
    Severity,
    VALID_TRANSITIONS,
)


class IssueStore:
    def __init__(self, repo_path: str, config: Config):
        self.base = Path(repo_path) / config.get_issue_dir()

    def _next_id(self) -> int:
        existing = list(self.base.glob("*.md"))
        if not existing:
            return 1
        return max(int(f.stem) for f in existing) + 1

    def create(self, title: str, priority: str = "medium", tags: list[str] | None = None) -> Issue:
        issue = Issue(
            id=self._next_id(),
            title=title,
            status=IssueStatus.CREATED,
            priority=priority,
            created=datetime.now(),
            updated=datetime.now(),
            tags=tags or [],
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
        return [self.get(int(f.stem)) for f in sorted(self.base.glob("*.md"))]

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

    def append_review(self, issue_id: int, section: str, review: ReviewResult) -> None:
        formatted = self._format_review(review)
        issue = self.get(issue_id)
        existing = issue.sections.get(section, "")
        if existing:
            issue.sections[section] = existing + "\n\n" + formatted
        else:
            issue.sections[section] = formatted
        self._save(issue)

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
    def _format_review(review: ReviewResult) -> str:
        lines = [f"**Reviewer: {review.reviewer}** — {'PASSED' if review.passed else 'NOT PASSED'}"]
        for c in review.comments:
            loc = f" ({c.location})" if c.location else ""
            lines.append(f"- [{c.severity.value.upper()}]{loc} {c.message}")
        return "\n".join(lines)

    @staticmethod
    def _sections_to_markdown(sections: dict[str, str]) -> str:
        if not sections:
            return ""
        return "\n\n".join(f"## {k}\n{v}" for k, v in sections.items())

    @staticmethod
    def _markdown_to_sections(content: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current_key: str | None = None
        lines: list[str] = []
        for line in content.split("\n"):
            if line.startswith("## "):
                if current_key:
                    sections[current_key] = "\n".join(lines).strip()
                current_key = line[3:].strip()
                lines = []
            else:
                lines.append(line)
        if current_key:
            sections[current_key] = "\n".join(lines).strip()
        return sections
