from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from shadowcoder.agents.types import (
    AgentRequest, DesignOutput, DevelopOutput, PreflightOutput, ReviewOutput, TestOutput,
)


class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def preflight(self, request: AgentRequest) -> PreflightOutput:
        ...

    @abstractmethod
    async def design(self, request: AgentRequest) -> DesignOutput:
        ...

    @abstractmethod
    async def develop(self, request: AgentRequest) -> DevelopOutput:
        ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewOutput:
        ...

    @abstractmethod
    async def test(self, request: AgentRequest) -> TestOutput:
        ...

    async def _get_files_changed(self, worktree_path: str) -> list[str]:
        if not worktree_path:
            return []

        async def _run(args):
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            return [f for f in stdout.decode().strip().splitlines() if f]

        changed = await _run(["git", "diff", "--name-only", "HEAD"])
        untracked = await _run(["git", "ls-files", "--others", "--exclude-standard"])
        return sorted(set(changed + untracked))

    def _extract_json(self, raw: str) -> dict:
        import json
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
