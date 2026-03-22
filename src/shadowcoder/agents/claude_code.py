from __future__ import annotations

import asyncio
import json
import logging
import time
from textwrap import dedent

from shadowcoder.agents.base import BaseAgent
from shadowcoder.agents.types import (
    AgentRequest, AgentUsage, DesignOutput, DevelopOutput, ReviewOutput, TestOutput,
    ReviewComment, Severity,
)

logger = logging.getLogger(__name__)

# Map severity strings to enum
_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
}


class ClaudeCodeAgent(BaseAgent):
    """Real agent that calls `claude` CLI to do actual work."""

    def _get_model(self) -> str:
        return self.config.get("model", "sonnet")

    def _get_permission_mode(self) -> str:
        return self.config.get("permission_mode", "auto")

    async def _run_claude(self, prompt: str, cwd: str | None = None,
                          system_prompt: str | None = None) -> str:
        """Call claude CLI in print mode, return the text output."""
        cmd = [
            "claude", "-p",
            "--output-format", "text",
            "--model", self._get_model(),
            "--permission-mode", self._get_permission_mode(),
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate(input=prompt.encode("utf-8"))

        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.error("claude CLI failed (rc=%d): %s", proc.returncode, err)
            raise RuntimeError(f"claude CLI failed: {err}")

        return stdout.decode("utf-8")

    async def _run_claude_with_usage(self, prompt: str, cwd: str | None = None,
                                      system_prompt: str | None = None) -> tuple[str, AgentUsage]:
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

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate(input=prompt.encode("utf-8"))
        duration_ms = int((time.monotonic() - start) * 1000)

        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {stderr.decode().strip()}")

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

    def _build_context(self, request: AgentRequest) -> str:
        """Build context string from issue sections."""
        issue = request.issue
        parts = [f"Issue: {issue.title} (#{issue.id})"]
        for section_name in ["需求", "设计", "Design Review", "开发步骤", "Dev Review", "测试"]:
            content = issue.sections.get(section_name, "")
            if content:
                parts.append(f"\n--- {section_name} ---\n{content}")
        return "\n".join(parts)

    async def design(self, request: AgentRequest) -> DesignOutput:
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        system = dedent("""\
            You are a senior software architect. Produce a detailed technical
            design document. Include: architecture, components, data structures,
            interfaces, error handling, and testing strategy.

            If there are previous review comments, address each one specifically.

            Output ONLY the design document in markdown format.
        """)
        prompt = f"{context}\n\nProduce the technical design for this issue."
        result, usage = await self._run_claude_with_usage(prompt, cwd=cwd, system_prompt=system)
        return DesignOutput(document=result, usage=usage)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        system = dedent("""\
            You are a senior software engineer. Implement the code based on
            the design document. You MUST:
            1. Create actual source files in the working directory
            2. Write tests
            3. Make sure the code compiles/runs without errors

            If there are previous review comments or test failures,
            address each one specifically.

            After writing code, provide a summary of what you implemented
            and what files you created/modified.
        """)
        prompt = f"{context}\n\nImplement the code based on the design. Write actual files."
        result, usage = await self._run_claude_with_usage(prompt, cwd=cwd, system_prompt=system)
        files_changed = await self._get_files_changed(cwd or "")
        return DevelopOutput(summary=result, files_changed=files_changed, usage=usage)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        system = dedent("""\
            You are a code reviewer. Review the design or implementation
            against the requirements.

            For each issue found, classify its severity:
            - critical: breaks core functionality or security
            - high: missing required feature or significant bug
            - medium: code quality issue or minor missing feature
            - low: style, naming, or minor improvement

            Output your review in this exact JSON format (nothing else):
            {
              "passed": true/false,
              "comments": [
                {"severity": "high", "message": "description", "location": "file:line or section"}
              ]
            }

            Pass only if there are no critical or high severity issues.
        """)
        prompt = f"{context}\n\nReview the current design/implementation against requirements."

        result, usage = await self._run_claude_with_usage(prompt, cwd=cwd, system_prompt=system)

        # Parse JSON from the response
        try:
            json_str = result
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0]

            data = json.loads(json_str.strip())
            comments = []
            for c in data.get("comments", []):
                severity = _SEVERITY_MAP.get(c.get("severity", "medium"), Severity.MEDIUM)
                comments.append(ReviewComment(
                    severity=severity,
                    message=c.get("message", ""),
                    location=c.get("location"),
                ))
            return ReviewOutput(
                passed=data.get("passed", False),
                comments=comments,
                reviewer="claude-code",
                usage=usage,
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Failed to parse review JSON, treating as not passed: %s", e)
            return ReviewOutput(
                passed=False,
                comments=[ReviewComment(
                    severity=Severity.MEDIUM,
                    message=f"Review output could not be parsed: {result[:200]}",
                )],
                reviewer="claude-code",
                usage=usage,
            )

    async def test(self, request: AgentRequest) -> TestOutput:
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        system = dedent("""\
            You are a QA engineer. Run the tests and benchmarks for this project.

            1. First, look at the project structure and find test files
            2. Run the tests (pytest or appropriate test runner)
            3. If there are benchmark/acceptance criteria in the requirements,
               verify them
            4. Report results with pass/fail counts

            If tests fail, analyze the root cause and provide a recommendation:
            - If the failure is due to a code bug: set recommendation to "develop"
            - If the failure is due to a missing feature not in the design:
              set recommendation to "design"

            End your output with a line in this exact format:
            RESULT: PASS  (if all tests/benchmarks pass)
            or
            RESULT: FAIL recommendation=develop  (or recommendation=design)
        """)
        prompt = f"{context}\n\nRun all tests and benchmarks. Report results."
        result, usage = await self._run_claude_with_usage(prompt, cwd=cwd, system_prompt=system)

        success = False
        recommendation = None
        for line in reversed(result.strip().splitlines()):
            line = line.strip()
            if line.startswith("RESULT:"):
                if "PASS" in line:
                    success = True
                else:
                    success = False
                    if "recommendation=" in line:
                        recommendation = line.split("recommendation=")[1].strip()
                break

        return TestOutput(report=result, success=success, recommendation=recommendation, usage=usage)
