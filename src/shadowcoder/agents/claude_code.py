from __future__ import annotations

import asyncio
import json
import logging
from textwrap import dedent

from shadowcoder.agents.base import AgentRequest, AgentResponse, AgentStream, BaseAgent
from shadowcoder.core.models import ReviewComment, ReviewResult, Severity

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

    async def _run_claude_json(self, prompt: str, cwd: str | None = None,
                               system_prompt: str | None = None) -> dict:
        """Call claude CLI in print mode with JSON output."""
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

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise RuntimeError(f"claude CLI failed: {err}")

        return json.loads(stdout.decode("utf-8"))

    def _build_context(self, request: AgentRequest) -> str:
        """Build context string from issue sections."""
        issue = request.issue
        parts = [f"Issue: {issue.title} (#{issue.id})"]
        for section_name in ["需求", "设计", "Design Review", "开发步骤", "Dev Review", "测试"]:
            content = issue.sections.get(section_name, "")
            if content:
                parts.append(f"\n--- {section_name} ---\n{content}")
        return "\n".join(parts)

    async def execute(self, request: AgentRequest) -> AgentResponse:
        issue = request.issue
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        if request.action == "design":
            return await self._do_design(issue, context, cwd)
        elif request.action == "develop":
            return await self._do_develop(issue, context, cwd)
        elif request.action == "test":
            return await self._do_test(issue, context, cwd)
        else:
            result = await self._run_claude(
                f"{context}\n\nAction: {request.action}", cwd=cwd)
            return AgentResponse(content=result, success=True)

    async def _do_design(self, issue, context, cwd) -> AgentResponse:
        system = dedent("""\
            You are a senior software architect. Produce a detailed technical
            design document. Include: architecture, components, data structures,
            interfaces, error handling, and testing strategy.

            If there are previous review comments, address each one specifically.

            Output ONLY the design document in markdown format.
        """)
        prompt = f"{context}\n\nProduce the technical design for this issue."

        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)
        return AgentResponse(content=result, success=True)

    async def _do_develop(self, issue, context, cwd) -> AgentResponse:
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

        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)
        return AgentResponse(content=result, success=True)

    async def _do_test(self, issue, context, cwd) -> AgentResponse:
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

        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)

        # Parse the RESULT line
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

        metadata = {}
        if recommendation:
            metadata["recommendation"] = recommendation

        return AgentResponse(content=result, success=success, metadata=metadata or None)

    async def review(self, request: AgentRequest) -> ReviewResult:
        issue = request.issue
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

        result = await self._run_claude(prompt, cwd=cwd, system_prompt=system)

        # Parse JSON from the response
        try:
            # Find JSON in the response (might be wrapped in markdown code blocks)
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
            return ReviewResult(
                passed=data.get("passed", False),
                comments=comments,
                reviewer="claude-code",
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Failed to parse review JSON, treating as not passed: %s", e)
            return ReviewResult(
                passed=False,
                comments=[ReviewComment(
                    severity=Severity.MEDIUM,
                    message=f"Review output could not be parsed: {result[:200]}",
                )],
                reviewer="claude-code",
            )

    async def stream(self, request: AgentRequest) -> AgentStream:
        raise NotImplementedError("Streaming not yet implemented")
