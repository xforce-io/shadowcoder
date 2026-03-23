import pytest
import json
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.config import Config
from shadowcoder.agents.types import (
    FeedbackItem, TestCase, ReviewOutput, ReviewComment, Severity,
)
# ReviewComment and Severity imported for potential future use


@pytest.fixture
def store(tmp_repo, tmp_config):
    config = Config(str(tmp_config))
    return IssueStore(str(tmp_repo), config)


def test_feedback_item_dataclass():
    item = FeedbackItem(id="F1", category="error_handling",
                        description="missing NULL handling", round_introduced=1)
    assert item.times_raised == 1
    assert item.resolved is False
    assert item.escalation_level == 1


def test_test_case_dataclass():
    tc = TestCase(name="test_null", description="NULL semantics",
                  expected_behavior="UNKNOWN")
    assert tc.category == "acceptance"


def test_review_output_new_fields_defaults():
    r = ReviewOutput(reviewer="test")
    assert r.resolved_item_ids == []
    assert r.proposed_tests == []


def test_review_output_with_new_fields():
    r = ReviewOutput(
        reviewer="test",
        resolved_item_ids=["F1", "F2"],
        proposed_tests=[TestCase(name="test_x", description="d", expected_behavior="e")])
    assert len(r.resolved_item_ids) == 2
    assert len(r.proposed_tests) == 1


def test_load_save_feedback(store):
    store.create("Test")
    fb = store.load_feedback(1)
    assert fb == {"items": [], "proposed_tests": []}

    fb["items"].append({"id": "F1", "category": "high", "description": "bug",
                        "round_introduced": 1, "times_raised": 1,
                        "resolved": False, "escalation_level": 1})
    store.save_feedback(1, fb)

    loaded = store.load_feedback(1)
    assert len(loaded["items"]) == 1
    assert loaded["items"][0]["id"] == "F1"


def test_escalate_feedback_text():
    item1 = {"description": "bug", "times_raised": 1}
    item2 = {"description": "bug", "times_raised": 2}
    item3 = {"description": "bug", "times_raised": 3}
    item4 = {"description": "bug", "times_raised": 4}

    assert "CRITICAL" not in Engine._escalate_feedback_text(item1)
    assert "修改方向" in Engine._escalate_feedback_text(item2)
    assert "代码修改" in Engine._escalate_feedback_text(item3)
    assert "CRITICAL" in Engine._escalate_feedback_text(item4)
