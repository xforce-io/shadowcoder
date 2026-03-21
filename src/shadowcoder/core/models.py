from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class IssueStatus(Enum):
    CREATED = "created"
    DESIGNING = "designing"
    DESIGN_REVIEW = "design_review"
    APPROVED = "approved"
    DEVELOPING = "developing"
    DEV_REVIEW = "dev_review"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.CREATED: {IssueStatus.DESIGNING, IssueStatus.CANCELLED},
    IssueStatus.DESIGNING: {IssueStatus.DESIGN_REVIEW, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DESIGN_REVIEW: {IssueStatus.DESIGNING, IssueStatus.APPROVED, IssueStatus.BLOCKED, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.APPROVED: {IssueStatus.DEVELOPING, IssueStatus.CANCELLED},
    IssueStatus.DEVELOPING: {IssueStatus.DEV_REVIEW, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DEV_REVIEW: {IssueStatus.DEVELOPING, IssueStatus.TESTING, IssueStatus.BLOCKED, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.TESTING: {IssueStatus.DONE, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DONE: set(),
    IssueStatus.FAILED: {IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.TESTING, IssueStatus.CANCELLED},
    IssueStatus.BLOCKED: {IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.APPROVED, IssueStatus.TESTING, IssueStatus.CANCELLED},
    IssueStatus.CANCELLED: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, from_status: IssueStatus, to_status: IssueStatus):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Invalid transition: {from_status.value} → {to_status.value}")


@dataclass
class ReviewComment:
    severity: Severity
    message: str
    location: str | None = None


@dataclass
class ReviewResult:
    passed: bool
    comments: list[ReviewComment]
    reviewer: str


@dataclass
class Issue:
    id: int
    title: str
    status: IssueStatus
    priority: str
    created: datetime
    updated: datetime
    tags: list[str] = field(default_factory=list)
    assignee: str | None = None
    sections: dict[str, str] = field(default_factory=dict)


@dataclass
class Task:
    task_id: str
    issue_id: int
    repo_path: str
    action: str
    agent_name: str
    worktree_path: str | None = None
    status: TaskStatus = TaskStatus.RUNNING
