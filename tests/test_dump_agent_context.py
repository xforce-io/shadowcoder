"""Tests for the dump_agent_context debug feature."""
import re
import subprocess
from datetime import datetime
from pathlib import Path

import yaml
import pytest

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import AgentRequest, AgentUsage, PreparedCall
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import MessageBus
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import Issue, IssueStatus
from shadowcoder.core.task_manager import TaskManager


class StubAgent(BaseAgent):
    """Minimal agent for testing dump_agent_context."""

    def _get_model(self) -> str:
        return "test-model"

    def _get_permission_mode(self) -> str:
        return "auto"

    async def _run(self, prompt, *, cwd=None, system_prompt=None,
                   session_id=None, resume_id=None):
        return ("stub output", AgentUsage())


def _make_issue(issue_id=1, title="test", status=IssueStatus.DESIGNING):
    now = datetime.now()
    return Issue(id=issue_id, title=title, status=status,
                 priority="medium", created=now, updated=now, sections={})


@pytest.fixture
def repo_path(tmp_path):
    """Create a minimal git repo."""
    subprocess.run(["git", "init", str(tmp_path)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
                   check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def agent():
    return StubAgent({"type": "claude_code", "_roles_dirs": []})


def _make_engine(repo_path, dump_enabled=False, max_chars=None):
    """Create an Engine with optional dump config."""
    config_data = Config._default_data()
    config_data["dispatch"]["design"] = "test-agent"
    config_data["dispatch"]["develop"] = "test-agent"
    config_data["dispatch"]["design_review"] = ["test-agent"]
    config_data["dispatch"]["develop_review"] = ["test-agent"]
    config_data["agents"]["test-agent"] = {"type": "claude_code", "model": "sonnet"}

    logging_conf = {}
    if dump_enabled:
        logging_conf["dump_agent_context"] = True
    if max_chars is not None:
        logging_conf["dump_agent_context_max_chars"] = max_chars
    if logging_conf:
        config_data["logging"] = logging_conf

    config = Config.__new__(Config)
    config._data = config_data
    config._roles_dirs = []

    bus = MessageBus()
    return Engine(bus, None, None, None, config, str(repo_path))


def _find_dump(prompts_dir: Path, pattern: str) -> Path:
    """Find a single dump file matching a glob pattern."""
    files = list(prompts_dir.glob(pattern))
    assert len(files) == 1, f"Expected 1 file matching {pattern}, found {len(files)}: {files}"
    return files[0]


def _parse_dump(path: Path) -> tuple[dict, str, str]:
    """Parse a dump markdown file. Returns (frontmatter_dict, system_prompt, prompt)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r'^---\n(.+?)\n---\n', text, re.DOTALL)
    assert m, "No YAML frontmatter found"
    meta = yaml.safe_load(m.group(1))
    body = text[m.end():]
    parts = re.split(r'^## ', body, flags=re.MULTILINE)
    sys_prompt = prompt = ""
    for part in parts:
        if part.startswith("System Prompt\n"):
            sys_prompt = part[len("System Prompt\n"):].strip()
        elif part.startswith("Prompt\n"):
            prompt = part[len("Prompt\n"):].strip()
    return meta, sys_prompt, prompt


class TestDumpDisabled:
    def test_no_files_when_disabled(self, repo_path, agent):
        engine = _make_engine(repo_path, dump_enabled=False)
        issue = _make_issue()

        call = agent.prepare_design(AgentRequest(
            action="design", issue=issue,
            context={"worktree_path": str(repo_path)}))

        engine._dump_agent_context(
            issue.id, "design", 1, "design", "test-agent", agent, call)

        prompts_dir = Path(repo_path) / ".shadowcoder" / "issues" / f"{issue.id:04d}" / "prompts"
        assert not prompts_dir.exists()


class TestDumpEnabled:
    def test_writes_md_file(self, repo_path, agent):
        engine = _make_engine(repo_path, dump_enabled=True)
        issue = _make_issue()

        call = agent.prepare_design(AgentRequest(
            action="design", issue=issue,
            context={"worktree_path": str(repo_path)}))

        engine._dump_agent_context(
            issue.id, "design", 1, "design", "test-agent", agent, call)

        prompts_dir = Path(repo_path) / ".shadowcoder" / "issues" / f"{issue.id:04d}" / "prompts"
        assert prompts_dir.exists()
        files = list(prompts_dir.glob("*.md"))
        assert len(files) == 1
        assert files[0].name.startswith("design_r1_test-agent_")

    def test_file_contains_required_fields(self, repo_path, agent):
        engine = _make_engine(repo_path, dump_enabled=True)
        issue = _make_issue()

        request = AgentRequest(
            action="design", issue=issue,
            context={"worktree_path": str(repo_path)})
        call = agent.prepare_design(request)

        engine._dump_agent_context(
            issue.id, "design", 2, "design", "test-agent", agent, call)

        prompts_dir = (Path(repo_path) / ".shadowcoder" / "issues"
                    / f"{issue.id:04d}" / "prompts")
        out_path = _find_dump(prompts_dir, "design_r2_test-agent_*.md")
        meta, sys_prompt, prompt = _parse_dump(out_path)

        assert meta["issue_id"] == issue.id
        assert meta["phase"] == "design"
        assert meta["round"] == 2
        assert meta["action"] == "design"
        assert meta["agent_name"] == "test-agent"
        assert meta["agent_type"] == "claude_code"
        assert meta["model"] == "test-model"
        assert meta["cwd"] == str(repo_path)
        assert meta["permission_mode"] == "auto"
        assert meta["session_id"] is None
        assert meta["resume_id"] is None
        assert "timestamp" in meta
        assert isinstance(meta["system_prompt_chars"], int)
        assert isinstance(meta["prompt_chars"], int)
        assert meta["prompt_chars"] == len(call.prompt)
        assert "Issue: test (#1)" in prompt

    def test_develop_includes_session_id(self, repo_path, agent):
        engine = _make_engine(repo_path, dump_enabled=True)
        issue = _make_issue(status=IssueStatus.DEVELOPING)

        request = AgentRequest(
            action="develop", issue=issue,
            context={
                "worktree_path": str(repo_path),
                "session_id": "sess-123",
            })
        call = agent.prepare_develop(request)

        engine._dump_agent_context(
            issue.id, "develop", 1, "develop", "test-agent", agent, call)

        prompts_dir = (Path(repo_path) / ".shadowcoder" / "issues"
                    / f"{issue.id:04d}" / "prompts")
        out_path = _find_dump(prompts_dir, "develop_r1_test-agent_*.md")
        meta, _, _ = _parse_dump(out_path)
        assert meta["session_id"] == "sess-123"
        assert meta["resume_id"] is None

    def test_review_action_naming(self, repo_path, agent):
        engine = _make_engine(repo_path, dump_enabled=True)
        issue = _make_issue()

        request = AgentRequest(
            action="review", issue=issue,
            context={"worktree_path": str(repo_path)})
        call = agent.prepare_review(request)

        engine._dump_agent_context(
            issue.id, "design_review", 1, "design_review",
            "test-agent", agent, call)

        prompts_dir = (Path(repo_path) / ".shadowcoder" / "issues"
                    / f"{issue.id:04d}" / "prompts")
        out_path = _find_dump(prompts_dir, "design_review_r1_test-agent_*.md")
        assert out_path.exists()


class TestTruncation:
    def test_long_prompt_truncated(self, repo_path, agent):
        engine = _make_engine(repo_path, dump_enabled=True, max_chars=100)
        issue = _make_issue()

        call = PreparedCall(
            action="design",
            system_prompt="S" * 200,
            prompt="P" * 500,
            cwd=str(repo_path),
        )

        engine._dump_agent_context(
            issue.id, "design", 1, "design", "test-agent", agent, call)

        prompts_dir = (Path(repo_path) / ".shadowcoder" / "issues"
                    / f"{issue.id:04d}" / "prompts")
        out_path = _find_dump(prompts_dir, "design_r1_test-agent_*.md")
        meta, sys_prompt, prompt = _parse_dump(out_path)

        # Content is truncated but char counts reflect original
        assert len(sys_prompt) == 100
        assert len(prompt) == 100
        assert meta["system_prompt_chars"] == 200
        assert meta["prompt_chars"] == 500


class TestPreparedCall:
    def test_prepare_design(self, agent):
        issue = _make_issue()
        request = AgentRequest(action="design", issue=issue,
                               context={"worktree_path": "/tmp"})
        call = agent.prepare_design(request)
        assert call.action == "design"
        assert "Issue: test (#1)" in call.prompt
        assert call.cwd == "/tmp"
        assert call.session_id is None

    def test_prepare_develop_with_resume(self, agent):
        issue = _make_issue(status=IssueStatus.DEVELOPING)
        request = AgentRequest(action="develop", issue=issue,
                               context={"worktree_path": "/tmp", "resume_id": "r-1"})
        call = agent.prepare_develop(request)
        assert call.action == "develop"
        assert call.resume_id == "r-1"
        assert call.session_id is None

    def test_prepare_review_design(self, agent):
        issue = _make_issue(status=IssueStatus.DESIGN_REVIEW)
        request = AgentRequest(action="review", issue=issue,
                               context={"worktree_path": "/tmp"})
        call = agent.prepare_review(request)
        assert call.action == "review"

    def test_prepare_review_code(self, agent):
        issue = _make_issue(status=IssueStatus.DEV_REVIEW)
        request = AgentRequest(action="review", issue=issue,
                               context={"worktree_path": "/tmp", "code_diff": "diff..."})
        call = agent.prepare_review(request)
        assert call.action == "review"
        assert "diff..." in call.prompt

    def test_prepare_write_acceptance_script(self, agent):
        issue = _make_issue(status=IssueStatus.APPROVED)
        request = AgentRequest(action="write_acceptance_script", issue=issue,
                               context={"worktree_path": "/tmp"})
        call = agent.prepare_write_acceptance_script(request)
        assert call.action == "write_acceptance_script"
        assert "acceptance test script" in call.prompt.lower()
