from datetime import datetime
from shadowcoder.core.models import (
    IssueStatus, Severity, TaskStatus,
    ReviewComment, ReviewResult, Issue, Task,
    InvalidTransitionError, VALID_TRANSITIONS,
)


def test_issue_status_values():
    assert IssueStatus.CREATED.value == "created"
    assert IssueStatus.BLOCKED.value == "blocked"
    assert IssueStatus.CANCELLED.value == "cancelled"


def test_task_status_values():
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.COMPLETED.value == "completed"


def test_issue_defaults():
    issue = Issue(
        id=1, title="test", status=IssueStatus.CREATED,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )
    assert issue.tags == []
    assert issue.assignee is None
    assert issue.sections == {}


def test_task_default_status():
    task = Task(
        task_id="abc", issue_id=1, repo_path="/tmp",
        action="design", agent_name="claude-code",
    )
    assert task.status == TaskStatus.RUNNING


def test_review_result():
    r = ReviewResult(
        passed=False,
        comments=[
            ReviewComment(severity=Severity.HIGH, message="bad design"),
            ReviewComment(severity=Severity.LOW, message="minor style"),
        ],
        reviewer="claude-code",
    )
    assert not r.passed
    assert len(r.comments) == 2


def test_valid_transitions_designing():
    assert IssueStatus.DESIGN_REVIEW in VALID_TRANSITIONS[IssueStatus.DESIGNING]
    assert IssueStatus.FAILED in VALID_TRANSITIONS[IssueStatus.DESIGNING]


def test_valid_transitions_blocked():
    blocked = VALID_TRANSITIONS[IssueStatus.BLOCKED]
    assert IssueStatus.DESIGNING in blocked
    assert IssueStatus.DEVELOPING in blocked
    assert IssueStatus.APPROVED in blocked
    assert IssueStatus.CANCELLED in blocked


def test_invalid_transition_error():
    err = InvalidTransitionError(IssueStatus.CREATED, IssueStatus.DONE)
    assert "created" in str(err)
    assert "done" in str(err)
