import pytest
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import IssueStatus, InvalidTransitionError
from shadowcoder.agents.types import Severity, ReviewComment, ReviewOutput
from shadowcoder.core.config import Config


@pytest.fixture
def store(tmp_repo, tmp_config):
    config = Config(str(tmp_config))
    return IssueStore(str(tmp_repo), config)


def test_create_issue(store):
    issue = store.create("Login feature", priority="high", tags=["auth"])
    assert issue.id == 1
    assert issue.title == "Login feature"
    assert issue.status == IssueStatus.CREATED
    assert issue.priority == "high"
    assert issue.tags == ["auth"]


def test_create_auto_increment(store):
    i1 = store.create("First")
    i2 = store.create("Second")
    assert i1.id == 1
    assert i2.id == 2


def test_get_issue(store):
    created = store.create("Test issue")
    loaded = store.get(created.id)
    assert loaded.id == created.id
    assert loaded.title == "Test issue"
    assert loaded.status == IssueStatus.CREATED


def test_get_nonexistent(store):
    with pytest.raises(FileNotFoundError):
        store.get(999)


def test_list_all(store):
    store.create("A")
    store.create("B")
    issues = store.list_all()
    assert len(issues) == 2
    assert issues[0].id == 1
    assert issues[1].id == 2


def test_list_all_empty(store):
    assert store.list_all() == []


def test_list_by_status(store):
    store.create("A")
    store.create("B")
    store.transition_status(1, IssueStatus.DESIGNING)
    result = store.list_by_status(IssueStatus.DESIGNING)
    assert len(result) == 1
    assert result[0].id == 1


def test_list_by_tag(store):
    store.create("A", tags=["backend"])
    store.create("B", tags=["frontend"])
    store.create("C", tags=["backend", "api"])
    result = store.list_by_tag("backend")
    assert len(result) == 2


def test_transition_status_valid(store):
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    issue = store.get(1)
    assert issue.status == IssueStatus.DESIGNING


def test_transition_status_invalid(store):
    store.create("Test")
    with pytest.raises(InvalidTransitionError):
        store.transition_status(1, IssueStatus.DONE)


def test_update_section(store):
    store.create("Test")
    store.update_section(1, "设计", "Design content here")
    issue = store.get(1)
    assert issue.sections["设计"] == "Design content here"


def test_update_section_overwrites(store):
    store.create("Test")
    store.update_section(1, "设计", "v1")
    store.update_section(1, "设计", "v2")
    issue = store.get(1)
    assert issue.sections["设计"] == "v2"


def test_append_review(store):
    store.create("Test")
    review = ReviewOutput(
        passed=False,
        comments=[
            ReviewComment(severity=Severity.HIGH, message="Fix this"),
            ReviewComment(severity=Severity.LOW, message="Nit"),
        ],
        reviewer="claude-code",
    )
    store.append_review(1, "Design Review", review)
    issue = store.get(1)
    content = issue.sections["Design Review"]
    # After the split: .md only has summary, not full review details
    assert "NOT PASSED" in content
    assert "2 comments" in content


def test_append_log_creates_file(store):
    store.create("Test")
    store.append_log(1, "Design R1 开始")
    log = store.get_log(1)
    assert "Design R1 开始" in log
    # Main issue file should NOT have 航海日志 section
    issue = store.get(1)
    assert "航海日志" not in issue.sections


def test_append_log_accumulates(store):
    store.create("Test")
    store.append_log(1, "Entry 1")
    store.append_log(1, "Entry 2")
    log = store.get_log(1)
    assert "Entry 1" in log
    assert "Entry 2" in log


def test_get_log_nonexistent(store):
    store.create("Test")
    assert store.get_log(1) == ""


def test_append_review_splits(store):
    from shadowcoder.agents.types import ReviewOutput, ReviewComment, Severity
    store.create("Test")
    review = ReviewOutput(
        passed=False,
        comments=[
            ReviewComment(severity=Severity.HIGH, message="Fix this"),
            ReviewComment(severity=Severity.LOW, message="Nit"),
        ],
        reviewer="claude-code",
    )
    store.append_review(1, "Design Review", review)

    # .md has summary only
    issue = store.get(1)
    assert "NOT PASSED" in issue.sections["Design Review"]
    assert "2 comments" in issue.sections["Design Review"]
    # Full review content should NOT be in .md
    assert "Fix this" not in issue.sections["Design Review"]

    # .log.md has full content
    log = store.get_log(1)
    assert "Fix this" in log
    assert "claude-code" in log


def test_list_all_ignores_log_files(store):
    store.create("A")
    store.append_log(1, "some log")
    issues = store.list_all()
    assert len(issues) == 1  # should not count .log.md as an issue


def test_next_id_ignores_log_files(store):
    store.create("A")
    store.append_log(1, "some log")
    store.create("B")
    assert store.get(2).title == "B"  # should be ID 2, not crash


def test_assign(store):
    store.create("Test")
    store.assign(1, "codex")
    issue = store.get(1)
    assert issue.assignee == "codex"


def test_sections_roundtrip(store):
    store.create("Test")
    store.update_section(1, "需求分析", "Analysis content")
    store.update_section(1, "设计", "Design content")
    issue = store.get(1)
    assert issue.sections["需求分析"] == "Analysis content"
    assert issue.sections["设计"] == "Design content"
