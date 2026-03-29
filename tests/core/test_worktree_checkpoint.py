"""Tests for WorktreeManager checkpoint/revert methods."""
import pytest
from pathlib import Path
from shadowcoder.core.worktree import WorktreeManager


@pytest.fixture
def wt_manager():
    return WorktreeManager()


@pytest.fixture
async def worktree(tmp_repo, wt_manager):
    """Create a real worktree for testing."""
    wt_path = await wt_manager.ensure(str(tmp_repo), 99, "test-checkpoint")
    yield wt_path
    try:
        await wt_manager.cleanup(str(tmp_repo), 99)
    except Exception:
        pass


async def test_save_checkpoint_returns_hash(wt_manager, worktree):
    checkpoint = await wt_manager.save_checkpoint(worktree, "test")
    assert len(checkpoint) == 40  # git SHA-1 hash


async def test_save_checkpoint_deletes_metrics_json(wt_manager, worktree):
    metrics_path = Path(worktree) / "metrics.json"
    metrics_path.write_text('{"recall": 0.5}')
    await wt_manager.save_checkpoint(worktree, "test")
    assert not metrics_path.exists()


async def test_revert_to_restores_modified_file(wt_manager, worktree):
    test_file = Path(worktree) / "file.txt"
    test_file.write_text("original")
    checkpoint = await wt_manager.save_checkpoint(worktree, "before-develop")

    test_file.write_text("modified by develop agent")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True)

    ok = await wt_manager.revert_to(worktree, checkpoint)
    assert ok is True
    assert test_file.read_text() == "original"


async def test_revert_to_removes_untracked_files(wt_manager, worktree):
    checkpoint = await wt_manager.save_checkpoint(worktree, "before-develop")

    new_file = Path(worktree) / "new_file.py"
    new_file.write_text("created by develop")

    ok = await wt_manager.revert_to(worktree, checkpoint)
    assert ok is True
    assert not new_file.exists()


async def test_revert_to_removes_staged_new_files(wt_manager, worktree):
    checkpoint = await wt_manager.save_checkpoint(worktree, "before-develop")

    new_file = Path(worktree) / "staged.py"
    new_file.write_text("staged content")
    import subprocess
    subprocess.run(["git", "add", "staged.py"], cwd=worktree, capture_output=True)

    ok = await wt_manager.revert_to(worktree, checkpoint)
    assert ok is True
    assert not new_file.exists()


async def test_current_head(wt_manager, worktree):
    head = await wt_manager.current_head(worktree)
    assert len(head) == 40


async def test_checkpoint_does_not_include_metrics_json(wt_manager, worktree):
    """metrics.json deleted before checkpoint, so revert doesn't resurrect it."""
    metrics_path = Path(worktree) / "metrics.json"
    metrics_path.write_text('{"old": 1}')
    checkpoint = await wt_manager.save_checkpoint(worktree, "clean")

    metrics_path.write_text('{"new": 2}')

    await wt_manager.revert_to(worktree, checkpoint)
    assert not metrics_path.exists()
