import pytest
from pathlib import Path
from shadowcoder.core.worktree import WorktreeManager


@pytest.fixture
def wt_manager():
    return WorktreeManager()


async def test_create_worktree(tmp_repo, wt_manager):
    wt_path = await wt_manager.create(str(tmp_repo), 1)
    assert Path(wt_path).exists()
    assert "issue-1" in wt_path


async def test_create_worktree_branch(tmp_repo, wt_manager):
    import subprocess
    await wt_manager.create(str(tmp_repo), 1)
    result = subprocess.run(
        ["git", "branch", "--list", "shadowcoder/issue-1"],
        cwd=str(tmp_repo), capture_output=True, text=True,
    )
    assert "shadowcoder/issue-1" in result.stdout


async def test_remove_worktree(tmp_repo, wt_manager):
    wt_path = await wt_manager.create(str(tmp_repo), 1)
    assert Path(wt_path).exists()
    await wt_manager.remove(str(tmp_repo), 1)
    assert not Path(wt_path).exists()


async def test_list_worktrees(tmp_repo, wt_manager):
    await wt_manager.create(str(tmp_repo), 1)
    await wt_manager.create(str(tmp_repo), 2)
    wts = await wt_manager.list(str(tmp_repo))
    assert len(wts) >= 2


async def test_create_duplicate_fails(tmp_repo, wt_manager):
    await wt_manager.create(str(tmp_repo), 1)
    with pytest.raises(RuntimeError):
        await wt_manager.create(str(tmp_repo), 1)
