import json
import os
from pathlib import Path

from shadowcoder.dashboard.state import DashboardState, PipelineStage


def _make_issue_dir(tmp_path: Path, issue_id: int = 1) -> Path:
    d = tmp_path / ".shadowcoder" / "issues" / f"{issue_id:04d}"
    d.mkdir(parents=True)
    (d / "issue.md").write_text(
        '---\n'
        f'id: {issue_id}\n'
        'title: "Test Issue"\n'
        'status: "developing"\n'
        'priority: "medium"\n'
        'created: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T11:00:00"\n'
        'tags: []\n'
        '---\n'
    )
    (d / "issue.log").write_text(
        "[2026-04-01 10:00:00] Issue 创建: Test Issue\n"
        "[2026-04-01 10:05:00] Design R1 开始\n"
        "[2026-04-01 10:10:00] Design Review\n"
        "PASSED (CRITICAL=0, HIGH=0)\n"
        "[2026-04-01 10:15:00] Develop R1 开始\n"
    )
    (d / "feedback.json").write_text(json.dumps({
        "items": [{"id": "1", "category": "bug", "description": "fix",
                   "severity": "MEDIUM", "resolved": False}],
        "proposed_tests": [], "acceptance_tests": [], "supplementary_tests": [],
    }))
    wt = tmp_path / ".shadowcoder" / "worktrees" / f"issue-{issue_id}"
    wt.mkdir(parents=True)
    return tmp_path


class TestDashboardState:
    def test_load_issues(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        issues = state.get_issue_list()
        assert len(issues) == 1
        assert issues[0]["id"] == 1
        assert issues[0]["title"] == "Test Issue"
        assert issues[0]["status"] == "developing"

    def test_load_issue_detail(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(1)
        assert detail["title"] == "Test Issue"
        assert detail["status"] == "developing"
        assert len(detail["log_entries"]) == 4
        assert detail["feedback"]["total"] == 1

    def test_pipeline_stages(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(1)
        stages = detail["pipeline"]
        assert stages[0].state == "completed"  # Design
        assert stages[1].state == "completed"  # Design Review
        assert stages[2].state == "active"     # Develop
        assert stages[3].state == "pending"    # Gate
        assert stages[4].state == "pending"    # Dev Review
        assert stages[5].state == "pending"    # Acceptance

    def test_nonexistent_issue(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(99)
        assert detail is None

    def test_retry_timeline_from_log(self, tmp_path):
        repo = _make_issue_dir(tmp_path)
        d = repo / ".shadowcoder" / "issues" / "0001"
        (d / "issue.log").write_text(
            "[2026-04-01 10:00:00] Design R1 开始\n"
            "[2026-04-01 10:05:00] Design Review\n"
            "PASSED\n"
            "[2026-04-01 10:10:00] Develop R1 开始\n"
            "[2026-04-01 10:15:00] Gate FAIL R1: 3 tests failed\n"
            "[2026-04-01 10:20:00] Develop R2 开始\n"
            "[2026-04-01 10:25:00] Gate PASS R2\n"
        )
        state = DashboardState(str(repo))
        detail = state.get_issue_detail(1)
        retries = detail["retries"]
        assert len(retries) == 2
        assert retries[0]["round"] == "R1"
        assert retries[0]["result"] == "fail"
        assert retries[1]["round"] == "R2"
        assert retries[1]["result"] == "pass"
