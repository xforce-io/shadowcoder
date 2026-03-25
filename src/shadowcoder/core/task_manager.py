from __future__ import annotations

import asyncio
import uuid

from shadowcoder.core.models import Issue, Task, TaskStatus
from shadowcoder.core.worktree import WorktreeManager


class TaskManager:
    def __init__(self, worktree_manager: WorktreeManager):
        self.tasks: dict[str, Task] = {}
        self.worktree_manager = worktree_manager
        self._running: dict[str, asyncio.Task] = {}

    async def create(self, issue: Issue, repo_path: str, action: str, agent_name: str) -> Task:
        task_id = str(uuid.uuid4())[:8]
        worktree_path = await self.worktree_manager.ensure(repo_path, issue.id, title=issue.title)
        task = Task(
            task_id=task_id,
            issue_id=issue.id,
            repo_path=repo_path,
            action=action,
            agent_name=agent_name,
            worktree_path=worktree_path,
        )
        self.tasks[task_id] = task
        return task

    def launch(self, task_id: str, coro) -> asyncio.Task:
        atask = asyncio.create_task(coro)
        self._running[task_id] = atask
        return atask

    def list_active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]

    async def cancel(self, task_id: str) -> None:
        if task_id in self._running:
            self._running[task_id].cancel()
        if task_id in self.tasks:
            self.tasks[task_id].status = TaskStatus.CANCELLED
