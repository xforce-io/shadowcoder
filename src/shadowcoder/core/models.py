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
    DONE = "done"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.CREATED: {IssueStatus.DESIGNING, IssueStatus.BLOCKED, IssueStatus.CANCELLED},
    IssueStatus.DESIGNING: {IssueStatus.DESIGN_REVIEW, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DESIGN_REVIEW: {IssueStatus.DESIGNING, IssueStatus.APPROVED, IssueStatus.BLOCKED, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.APPROVED: {IssueStatus.DEVELOPING, IssueStatus.BLOCKED, IssueStatus.CANCELLED},
    IssueStatus.DEVELOPING: {IssueStatus.DEV_REVIEW, IssueStatus.FAILED, IssueStatus.BLOCKED, IssueStatus.CANCELLED},
    IssueStatus.DEV_REVIEW: {IssueStatus.DEVELOPING, IssueStatus.DONE, IssueStatus.BLOCKED, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.DONE: {IssueStatus.APPROVED},
    IssueStatus.FAILED: {IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.DONE, IssueStatus.BLOCKED, IssueStatus.CANCELLED},
    IssueStatus.IN_PROGRESS: {IssueStatus.CREATED, IssueStatus.APPROVED, IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.FAILED, IssueStatus.CANCELLED},
    IssueStatus.BLOCKED: {IssueStatus.DESIGNING, IssueStatus.DEVELOPING, IssueStatus.APPROVED, IssueStatus.DONE, IssueStatus.CANCELLED},
    IssueStatus.CANCELLED: {IssueStatus.CREATED},
}


class InvalidTransitionError(Exception):
    def __init__(self, from_status: IssueStatus, to_status: IssueStatus):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Invalid transition: {from_status.value} → {to_status.value}")


# --- Blocked reason constants ---
BLOCKED_BUDGET = "budget_exceeded"
BLOCKED_MAX_ROUNDS = "max_review_rounds"
BLOCKED_ACCEPTANCE_WEAK = "acceptance_too_weak"
BLOCKED_ACCEPTANCE_CONFIRMED = "acceptance_confirmed"
BLOCKED_ACCEPTANCE_BUG = "acceptance_script_bug"
BLOCKED_LOW_FEASIBILITY = "low_feasibility"
BLOCKED_METRIC_STAGNATED = "metric_stagnated"
BLOCKED_METRIC_GATE = BLOCKED_METRIC_STAGNATED  # alias for engine compatibility


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
    blocked_reason: str | None = None
    blocked_from: IssueStatus | None = None


@dataclass
class Task:
    task_id: str
    issue_id: int
    repo_path: str
    action: str
    agent_name: str
    worktree_path: str | None = None
    status: TaskStatus = TaskStatus.RUNNING
