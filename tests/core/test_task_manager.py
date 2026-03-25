import asyncio
import pytest
from unittest.mock import AsyncMock
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import Issue, IssueStatus, TaskStatus
from datetime import datetime


@pytest.fixture
def mock_worktree():
    wt = AsyncMock()
    wt.ensure = AsyncMock(return_value="/tmp/worktree/issue-1")
    return wt


@pytest.fixture
def manager(mock_worktree):
    return TaskManager(mock_worktree)


@pytest.fixture
def sample_issue():
    return Issue(
        id=1, title="Test", status=IssueStatus.CREATED,
        priority="medium", created=datetime.now(), updated=datetime.now(),
    )


async def test_create_task(manager, sample_issue):
    task = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    assert task.issue_id == 1
    assert task.action == "design"
    assert task.agent_name == "claude-code"
    assert task.status == TaskStatus.RUNNING
    assert task.worktree_path == "/tmp/worktree/issue-1"


async def test_create_calls_worktree(manager, sample_issue, mock_worktree):
    await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    mock_worktree.ensure.assert_called_once_with("/tmp/repo", 1, title="Test")


async def test_list_active(manager, sample_issue):
    t1 = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    active = manager.list_active()
    assert len(active) == 1
    assert active[0].task_id == t1.task_id


async def test_list_active_excludes_completed(manager, sample_issue):
    t1 = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    t1.status = TaskStatus.COMPLETED
    assert manager.list_active() == []


async def test_cancel(manager, sample_issue):
    task = await manager.create(sample_issue, "/tmp/repo", "design", "claude-code")
    async def long_running():
        await asyncio.sleep(100)
    manager.launch(task.task_id, long_running())
    await manager.cancel(task.task_id)
    assert task.status == TaskStatus.CANCELLED
