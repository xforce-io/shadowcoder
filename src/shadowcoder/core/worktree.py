from __future__ import annotations

import asyncio
from pathlib import Path


class WorktreeManager:
    def __init__(self, base_dir: str = ".shadowcoder/worktrees"):
        self.base_dir = base_dir

    async def _run_git(self, repo_path: str, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {stderr.decode().strip()}")
        return stdout.decode()

    async def create(self, repo_path: str, issue_id: int) -> str:
        branch = f"shadowcoder/issue-{issue_id}"
        wt_path = str(Path(repo_path) / self.base_dir / f"issue-{issue_id}")
        # If worktree already exists (e.g., resume from BLOCKED), reuse it
        if Path(wt_path).exists():
            return wt_path
        # Try creating with new branch; if branch exists, use existing branch
        try:
            await self._run_git(repo_path, "worktree", "add", "-b", branch, wt_path)
        except RuntimeError:
            await self._run_git(repo_path, "worktree", "add", wt_path, branch)
        return wt_path

    async def remove(self, repo_path: str, issue_id: int) -> None:
        wt_path = str(Path(repo_path) / self.base_dir / f"issue-{issue_id}")
        await self._run_git(repo_path, "worktree", "remove", wt_path)

    async def list(self, repo_path: str) -> list[str]:
        output = await self._run_git(repo_path, "worktree", "list", "--porcelain")
        return [
            line.split(maxsplit=1)[1]
            for line in output.splitlines()
            if line.startswith("worktree ")
        ]
