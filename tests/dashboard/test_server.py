import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from shadowcoder.dashboard.server import create_app


def _make_repo(tmp_path: Path) -> str:
    issues_dir = tmp_path / ".shadowcoder" / "issues" / "0001"
    issues_dir.mkdir(parents=True)
    (issues_dir / "issue.md").write_text(
        '---\nid: 1\ntitle: "Test Issue"\nstatus: "developing"\n'
        'priority: "medium"\ncreated: "2026-04-01T10:00:00"\n'
        'updated: "2026-04-01T11:00:00"\ntags: []\n---\n'
    )
    (issues_dir / "issue.log").write_text(
        "[2026-04-01 10:00:00] Issue 创建: Test Issue\n"
        "[2026-04-01 10:05:00] Design R1 开始\n"
    )
    (issues_dir / "feedback.json").write_text(json.dumps({
        "items": [], "proposed_tests": [],
        "acceptance_tests": [], "supplementary_tests": [],
    }))
    wt = tmp_path / ".shadowcoder" / "worktrees" / "issue-1"
    wt.mkdir(parents=True)
    return str(tmp_path)


@pytest.mark.asyncio
async def test_index_returns_dashboard(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "ShadowCoder" in resp.text
        assert "Test Issue" in resp.text


@pytest.mark.asyncio
async def test_index_with_issue_param(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/?issue=1")
        assert resp.status_code == 200
        assert "Test Issue" in resp.text


@pytest.mark.asyncio
async def test_api_issues(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Issue"


@pytest.mark.asyncio
async def test_api_issue_detail(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Issue"
        assert data["status"] == "developing"


@pytest.mark.asyncio
async def test_api_issue_not_found(tmp_path):
    repo = _make_repo(tmp_path)
    app = create_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/issues/99")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_no_issues(tmp_path):
    (tmp_path / ".shadowcoder" / "issues").mkdir(parents=True)
    app = create_app(str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "No issues found" in resp.text
