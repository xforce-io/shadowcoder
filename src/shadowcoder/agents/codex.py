from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import AgentUsage

logger = logging.getLogger(__name__)


class CodexAgent(BaseAgent):
    """Agent that calls the `codex` CLI (OpenAI Codex) to do actual work.

    Transport layer only — all prompt-building and output-parsing logic
    lives in BaseAgent. This subclass implements only the subprocess
    invocation via the codex CLI.

    Known limitation (MVP): concurrent CodexAgent instances running in the
    same worktree will clobber each other's AGENTS.md. This is acceptable
    for single-issue runs. A future fix would use a per-instance lock or
    unique temp filename.

    Session resume is not supported in MVP. If resume_id is provided, a
    warning is logged and a fresh session is started.
    """

    def _get_model(self) -> str:
        return self.config.get("model", "o3")

    def _get_permission_mode(self) -> str:
        return self.config.get("permission_mode", "auto")

    def _permission_flag(self) -> str:
        """Map permission_mode config to the corresponding codex CLI flag."""
        mode = self._get_permission_mode()
        if mode == "bypass":
            return "--dangerously-bypass-approvals-and-sandbox"
        # "auto" and any unknown value default to --full-auto
        return "--full-auto"

    async def _run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        system_prompt: str | None = None,
        session_id: str | None = None,
        resume_id: str | None = None,
    ) -> tuple[str, AgentUsage]:
        """Execute prompt via codex CLI. Handles AGENTS.md injection and cleanup."""
        if resume_id:
            logger.warning(
                "CodexAgent: session resume is not supported in MVP. "
                "Running fresh session (resume_id=%s ignored).", resume_id,
            )
        if session_id:
            logger.warning(
                "CodexAgent: session_id is not supported. "
                "Running fresh session (session_id=%s ignored).", session_id,
            )

        # Inject system prompt via AGENTS.md if provided
        agents_md_path = Path(cwd) / "AGENTS.md" if cwd else None
        original_agents_md: str | None = None
        agents_md_existed = False

        if system_prompt and agents_md_path:
            if agents_md_path.exists():
                original_agents_md = agents_md_path.read_text(encoding="utf-8")
                agents_md_existed = True
                new_content = f"{system_prompt}\n\n---\n\n{original_agents_md}"
            else:
                new_content = system_prompt
            agents_md_path.write_text(new_content, encoding="utf-8")

        try:
            return await self._run_subprocess(prompt, cwd=cwd)
        finally:
            # Restore AGENTS.md to its original state — runs even on exception
            if system_prompt and agents_md_path:
                try:
                    if agents_md_existed and original_agents_md is not None:
                        agents_md_path.write_text(original_agents_md, encoding="utf-8")
                    elif agents_md_path.exists():
                        agents_md_path.unlink()
                except Exception as exc:
                    logger.warning("CodexAgent: failed to restore AGENTS.md: %s", exc)

    async def _run_subprocess(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
    ) -> tuple[str, AgentUsage]:
        """Build and run the codex subprocess, parse JSONL output."""
        start = time.monotonic()
        cmd = [
            "codex", "exec",
            "--json",
            "-m", self._get_model(),
            self._permission_flag(),
        ]
        if cwd:
            cmd.extend(["-C", cwd])
        cmd.append("-")  # read prompt from stdin

        last_err = None
        stdout_bytes = b""
        for attempt in range(1, 4):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._get_env(),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode("utf-8")),
                    timeout=3600  # 60 minutes
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("codex CLI timed out after 60 minutes")

            if proc.returncode == 0:
                break

            last_err = stderr_bytes.decode().strip()
            logger.warning("codex CLI failed (rc=%d, attempt %d/3): %s",
                           proc.returncode, attempt, last_err)
            if attempt < 3:
                await asyncio.sleep(5 * attempt)
        else:
            raise RuntimeError(f"codex CLI failed after 3 attempts: {last_err}")

        duration_ms = int((time.monotonic() - start) * 1000)
        result_text, usage = self._parse_jsonl(stdout_bytes.decode("utf-8"), duration_ms)
        return result_text, usage

    def _parse_jsonl(self, raw: str, duration_ms: int) -> tuple[str, AgentUsage]:
        """Parse JSONL output from codex CLI.

        Collects text from item.completed/agent_message events and usage from
        turn.completed events. Malformed lines are skipped with a warning.
        If no agent_message text is found, falls back to raw stdout.

        Note: cost_usd is not provided in Codex JSONL output and is set to None.
        """
        text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("CodexAgent: skipping malformed JSONL line: %r", line)
                continue

            event_type = event.get("type", "")
            if event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", "")
                    if text:
                        text_parts.append(text)
            elif event_type == "turn.completed":
                usage_data = event.get("usage", {})
                input_tokens += usage_data.get("input_tokens", 0)
                output_tokens += usage_data.get("output_tokens", 0)

        if text_parts:
            result_text = "".join(text_parts)
        else:
            # Graceful degradation: treat entire stdout as plain text
            result_text = raw

        usage = AgentUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            cost_usd=None,  # Codex JSONL does not provide cost_usd (F3)
        )
        return result_text, usage
