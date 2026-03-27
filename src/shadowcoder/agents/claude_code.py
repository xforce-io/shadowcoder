from __future__ import annotations

import asyncio
import json
import logging
import time

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import AgentUsage

logger = logging.getLogger(__name__)


class ClaudeCodeAgent(BaseAgent):
    """Agent that calls `claude` CLI to do actual work.

    Transport layer only — all prompt-building and output-parsing logic
    lives in BaseAgent. This subclass implements only the subprocess
    invocation via the claude CLI.

    Note: _run_claude() (text-mode helper) has been removed as it was
    dead code — no action method used it. Verified: no callers exist
    outside this class (engine.py and scripts do not reference it).
    """

    def _get_model(self) -> str:
        return self.config.get("model", "sonnet")

    def _get_permission_mode(self) -> str:
        return self.config.get("permission_mode", "auto")

    async def _run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        system_prompt: str | None = None,
        session_id: str | None = None,
        resume_id: str | None = None,
    ) -> tuple[str, AgentUsage]:
        """Call claude CLI with JSON output to capture usage stats."""
        start = time.monotonic()
        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--model", self._get_model(),
            "--permission-mode", self._get_permission_mode(),
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        resumable = self.config.get("resumable", True)
        if session_id and resumable:
            cmd.extend(["--session-id", session_id])
        elif resume_id and resumable:
            cmd.extend(["--resume", resume_id])

        last_err = None
        for attempt in range(1, 4):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._get_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode("utf-8")),
                    timeout=3600  # 60 minutes
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("claude CLI timed out after 60 minutes")

            if proc.returncode == 0:
                break

            last_err = stderr.decode().strip()
            logger.warning("claude CLI failed (rc=%d, attempt %d/3): %s",
                           proc.returncode, attempt, last_err)
            if attempt < 3:
                await asyncio.sleep(5 * attempt)
        else:
            raise RuntimeError(f"claude CLI failed after 3 attempts: {last_err}")

        duration_ms = int((time.monotonic() - start) * 1000)
        data = json.loads(stdout.decode("utf-8"))

        # Extract text result from JSON response
        result_text = data.get("result", "")

        # Extract usage
        usage_data = data.get("usage", {})
        usage = AgentUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            duration_ms=duration_ms,
            cost_usd=data.get("cost_usd") or usage_data.get("cost_usd"),
        )
        return result_text, usage
