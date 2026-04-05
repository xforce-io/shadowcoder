"""Integration test: full dashboard render with realistic issue data."""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from shadowcoder.dashboard.server import create_app


def _make_full_repo(tmp_path: Path) -> str:
    d = tmp_path / ".shadowcoder" / "issues" / "0001"
    d.mkdir(parents=True)
    (d / "issue.md").write_text(
        '---\nid: 1\ntitle: "Add user authentication"\nstatus: "developing"\n'
        'priority: "high"\ncreated: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T10:50:00"\ntags: ["auth"]\nassignee: "fast-coder"\n---\n'
        '<!-- section: 需求 -->\nAdd JWT-based authentication\n<!-- /section -->\n'
    )
    (d / "issue.log").write_text(
        "[2026-04-01 10:00:00] Issue 创建: Add user authentication\n"
        "[2026-04-01 10:01:00] Preflight 评估\n"
        "Feasibility: high | Complexity: moderate | Risks: none\n"
        "[2026-04-01 10:02:00] Design R1 开始\n"
        "[2026-04-01 10:05:00] Usage: 1200+800 tokens, $0.08\n"
        "[2026-04-01 10:05:00] Design Review\n"
        "PASSED (CRITICAL=0, HIGH=0, 3 comments total)\n"
        "[2026-04-01 10:10:00] Develop R1 开始 (fast-coder)\n"
        "[2026-04-01 10:20:00] Usage: 5000+3000 tokens, $0.32\n"
        "[2026-04-01 10:20:00] Gate FAIL R1: 3 tests failed\n"
        "  AssertionError: expected 200 got 401 in test_login\n"
        "[2026-04-01 10:25:00] Develop R2 开始 (fast-coder)\n"
        "[2026-04-01 10:35:00] Usage: 4000+2500 tokens, $0.26\n"
        "[2026-04-01 10:35:00] Gate PASS R2\n"
        "[2026-04-01 10:40:00] Dev Review 开始 (claude-coder)\n"
    )
    (d / "feedback.json").write_text(json.dumps({
        "items": [
            {"id": "1", "category": "bug", "description": "Missing CSRF protection",
             "severity": "HIGH", "resolved": False, "round_introduced": 1,
             "times_raised": 1, "escalation_level": 0},
            {"id": "2", "category": "style", "description": "Inconsistent naming",
             "severity": "MEDIUM", "resolved": True, "round_introduced": 1,
             "times_raised": 1, "escalation_level": 0},
            {"id": "3", "category": "performance", "description": "Token expiry check",
             "severity": "LOW", "resolved": False, "round_introduced": 1,
             "times_raised": 1, "escalation_level": 0},
        ],
        "proposed_tests": [
            {"name": "test_login", "passed": True},
            {"name": "test_register", "passed": True},
        ],
        "acceptance_tests": [],
        "supplementary_tests": [],
        "verdict": "NOT PASSED",
    }))
    wt = tmp_path / ".shadowcoder" / "worktrees" / "issue-1"
    wt.mkdir(parents=True)
    return str(tmp_path)


@pytest.mark.asyncio
async def test_full_dashboard_render(tmp_path):
    repo = _make_full_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "ShadowCoder" in html
    assert "Add user authentication" in html
    assert "DEVELOPING" in html
    assert "fast-coder" in html
    assert "Design" in html
    assert "Develop" in html
    assert "Gate" in html
    assert "Issue 创建" in html or "Issue" in html
    assert "Gate FAIL R1" in html or "Gate FAIL" in html
    assert "Gate PASS R2" in html or "Gate PASS" in html
    assert "NOT PASSED" in html


@pytest.mark.asyncio
async def test_api_detail_has_retries(tmp_path):
    repo = _make_full_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues/1")
    data = resp.json()
    assert len(data["retries"]) == 2
    assert data["retries"][0]["round"] == "R1"
    assert data["retries"][0]["result"] == "fail"
    assert data["retries"][1]["round"] == "R2"
    assert data["retries"][1]["result"] == "pass"


@pytest.mark.asyncio
async def test_blocked_issue_shows_reason(tmp_path):
    d = tmp_path / ".shadowcoder" / "issues" / "0001"
    d.mkdir(parents=True)
    (d / "issue.md").write_text(
        '---\nid: 1\ntitle: "Blocked task"\nstatus: "blocked"\n'
        'priority: "medium"\ncreated: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T11:00:00"\ntags: []\n'
        'blocked_reason: "BLOCKED_MAX_ROUNDS"\n---\n'
    )
    (d / "issue.log").write_text("[2026-04-01 10:00:00] init\n")
    (d / "feedback.json").write_text(json.dumps({
        "items": [], "proposed_tests": [],
        "acceptance_tests": [], "supplementary_tests": [],
    }))
    app = create_app(str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert "BLOCKED_MAX_ROUNDS" in resp.text
