"""Tests for creating issues with description/requirements."""
import pytest
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def config(tmp_config):
    return Config(str(tmp_config))


@pytest.fixture
def store(tmp_repo, config):
    return IssueStore(str(tmp_repo), config)


@pytest.fixture
def mock_worktree():
    wt = AsyncMock()
    wt.create = AsyncMock(return_value="/tmp/wt")
    return wt


@pytest.fixture
def task_mgr(mock_worktree):
    return TaskManager(mock_worktree)


def make_engine(bus, store, task_mgr, config, repo_path="/tmp/repo"):
    reg = MagicMock()
    return Engine(bus, store, task_mgr, reg, config, repo_path)


# --- IssueStore.create with description ---

def test_create_with_description(store):
    issue = store.create("SQL Engine", description="Build a SQL database engine.")
    assert issue.sections["需求"] == "Build a SQL database engine."


def test_create_without_description(store):
    issue = store.create("Simple task")
    assert "需求" not in issue.sections


def test_create_description_persists(store):
    store.create("SQL Engine", description="Detailed requirements here.")
    issue = store.get(1)
    assert issue.sections["需求"] == "Detailed requirements here."


def test_create_multiline_description(store):
    desc = """## Goal
Build a SQL database engine with:
- SELECT, INSERT, UPDATE, DELETE
- JOIN support
- Transaction isolation

## Acceptance Criteria
| Query | Expected |
|-------|----------|
| SELECT 1+1 | 2 |
"""
    issue = store.create("SQL Engine", description=desc)
    reloaded = store.get(issue.id)
    assert "JOIN support" in reloaded.sections["需求"]
    assert "Acceptance Criteria" in reloaded.sections["需求"]
    assert "SELECT 1+1" in reloaded.sections["需求"]


# --- Engine._on_create with description ---

async def test_engine_create_with_inline_description(bus, store, task_mgr, config):
    engine = make_engine(bus, store, task_mgr, config)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "SQL Engine",
        "description": "Build a full SQL engine with JOINs.",
    }))

    issue = store.get(1)
    assert issue.title == "SQL Engine"
    assert issue.sections["需求"] == "Build a full SQL engine with JOINs."


async def test_engine_create_with_file_description(bus, store, task_mgr, config, tmp_path):
    """When description is a file path, read its content."""
    req_file = tmp_path / "requirements.md"
    req_file.write_text("## Requirements\nBuild a compiler.\n", encoding="utf-8")

    engine = make_engine(bus, store, task_mgr, config)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "Compiler",
        "description": str(req_file),
    }))

    issue = store.get(1)
    assert "Build a compiler." in issue.sections["需求"]


async def test_engine_create_no_description(bus, store, task_mgr, config):
    engine = make_engine(bus, store, task_mgr, config)

    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "Simple task",
    }))

    issue = store.get(1)
    assert "需求" not in issue.sections
