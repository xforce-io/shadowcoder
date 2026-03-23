from shadowcoder.agents.types import (
    Severity, ReviewComment, AgentUsage,
    DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput,
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
    o = ReviewOutput(comments=[
        ReviewComment(severity=Severity.HIGH, message="fix this")
    ], reviewer="claude")
    assert len(o.comments) == 1
    assert o.reviewer == "claude"


def test_review_output_defaults():
    o = ReviewOutput()
    assert o.comments == []
    assert o.reviewer == ""
    assert o.resolved_item_ids == []
    assert o.proposed_tests == []


def test_review_output_no_score():
    """ReviewOutput should NOT have score or passed fields."""
    o = ReviewOutput(comments=[], reviewer="test")
    assert not hasattr(o, "score")
    assert not hasattr(o, "passed")


def test_agent_request():
    issue = Issue(id=1, title="t", status=IssueStatus.CREATED, priority="medium",
                  created=datetime.now(), updated=datetime.now())
    r = AgentRequest(action="design", issue=issue, context={"worktree_path": "/tmp"})
    assert r.action == "design"
    assert r.prompt_override is None


def test_preflight_output():
    o = PreflightOutput(feasibility="high", estimated_complexity="complex",
                        risks=["risk1", "risk2"])
    assert o.feasibility == "high"
    assert len(o.risks) == 2
    assert o.tech_stack_recommendation is None


def test_preflight_output_defaults():
    o = PreflightOutput(feasibility="medium", estimated_complexity="simple")
    assert o.risks == []


def test_agent_action_failed():
    e = AgentActionFailed("could not complete", partial_output="partial result")
    assert str(e) == "could not complete"
    assert e.partial_output == "partial result"


def test_agent_action_failed_no_partial():
    e = AgentActionFailed("failed")
    assert e.partial_output == ""
