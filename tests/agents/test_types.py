from shadowcoder.agents.types import (
    Severity, ReviewComment, AgentUsage,
    DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
    AgentRequest, AgentActionFailed,
)
from shadowcoder.core.models import Issue, IssueStatus
from datetime import datetime


def test_severity_values():
    assert Severity.CRITICAL.value == "critical"
    assert Severity.LOW.value == "low"


def test_review_comment():
    c = ReviewComment(severity=Severity.HIGH, message="bad", location="file.py:10")
    assert c.severity == Severity.HIGH
    assert c.location == "file.py:10"


def test_agent_usage_defaults():
    u = AgentUsage()
    assert u.input_tokens == 0
    assert u.cost_usd is None


def test_design_output():
    o = DesignOutput(document="design doc")
    assert o.document == "design doc"
    assert o.usage is None


def test_develop_output_defaults():
    o = DevelopOutput(summary="implemented X")
    assert o.files_changed == []


def test_develop_output_with_files():
    o = DevelopOutput(summary="s", files_changed=["a.py", "b.py"])
    assert len(o.files_changed) == 2


def test_review_output():
    o = ReviewOutput(passed=False, score=40, comments=[
        ReviewComment(severity=Severity.HIGH, message="fix this")
    ], reviewer="claude")
    assert not o.passed
    assert o.score == 40
    assert len(o.comments) == 1


def test_review_output_defaults():
    o = ReviewOutput(passed=True)
    assert o.comments == []
    assert o.reviewer == ""
    assert o.score == 50  # default score


def test_review_output_score_explicit():
    o = ReviewOutput(passed=True, score=95)
    assert o.score == 95

    o2 = ReviewOutput(passed=False, score=40)
    assert o2.score == 40


def test_test_output():
    o = TestOutput(report="all pass", success=True, passed_count=10, total_count=10)
    assert o.success
    assert o.recommendation is None


def test_test_output_with_recommendation():
    o = TestOutput(report="fail", success=False, recommendation="develop",
                   passed_count=5, total_count=10)
    assert not o.success
    assert o.recommendation == "develop"


def test_agent_request():
    issue = Issue(id=1, title="t", status=IssueStatus.CREATED, priority="medium",
                  created=datetime.now(), updated=datetime.now())
    r = AgentRequest(action="design", issue=issue, context={"worktree_path": "/tmp"})
    assert r.action == "design"
    assert r.prompt_override is None


def test_agent_action_failed():
    e = AgentActionFailed("could not complete", partial_output="partial result")
    assert str(e) == "could not complete"
    assert e.partial_output == "partial result"


def test_agent_action_failed_no_partial():
    e = AgentActionFailed("failed")
    assert e.partial_output == ""
