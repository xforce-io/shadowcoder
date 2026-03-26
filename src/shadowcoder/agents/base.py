from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from textwrap import dedent

from shadowcoder.agents.types import (
    AgentRequest, AgentUsage, DesignOutput, DevelopOutput, PreflightOutput,
    ReviewOutput, TestCase, ReviewComment, Severity,
)

logger = logging.getLogger(__name__)

# Default role instructions — used when config doesn't specify custom ones.
DEFAULT_ROLE_INSTRUCTIONS: dict[str, str] = {
    "designer": (
        "你是一位资深系统架构师。你注重模块解耦、接口清晰和可测试性。"
        "你的设计应该简洁务实，避免过度工程化。"
        "优先考虑最简方案，只在必要时引入抽象层。"
    ),
    "design_reviewer": (
        "你是一位严格的架构评审专家。你关注设计的完整性、一致性和可扩展性。"
        "你会质疑不必要的复杂性，检查边界情况是否被考虑，"
        "并确保设计文档能指导开发者正确实现。"
        "对设计缺陷要直接指出，不要客气。"
    ),
    "developer": (
        "你是一位务实的高级工程师。你的首要目标是写出能工作、能通过测试的代码。"
        "你遵循项目现有的代码风格和约定。"
        "你重视代码的可读性和可维护性，但不会为了完美而过度重构。"
        "如果有 review 反馈，你会逐条解决。"
    ),
    "code_reviewer": (
        "你是一位严格的代码评审专家。你关注逻辑正确性、边界情况和安全性。"
        "你会检查测试无法覆盖的潜在问题：竞态条件、资源泄漏、错误处理遗漏。"
        "你不会纠结于风格问题，而是聚焦于真正影响正确性和可靠性的问题。"
        "对发现的问题要给出具体的修改建议。"
    ),
}

# Map severity strings to enum
_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
}


class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    # ------------------------------------------------------------------ #
    #  Abstract methods — each subclass must implement these              #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def _run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        system_prompt: str | None = None,
        session_id: str | None = None,
        resume_id: str | None = None,
    ) -> tuple[str, AgentUsage]:
        """Execute a prompt via the underlying CLI/API. Return (text_output, usage)."""
        ...

    @abstractmethod
    def _get_model(self) -> str:
        """Return the model identifier to use."""
        ...

    @abstractmethod
    def _get_permission_mode(self) -> str:
        """Return the permission mode to use."""
        ...

    # ------------------------------------------------------------------ #
    #  Shared helpers                                                      #
    # ------------------------------------------------------------------ #

    def _get_env(self) -> dict[str, str] | None:
        """Build environment for subprocess, merging custom env vars if configured."""
        custom_env = self.config.get("env")
        if not custom_env:
            return None  # inherit parent environment
        import os
        env = os.environ.copy()
        for k, v in custom_env.items():
            env[k] = os.path.expandvars(str(v))
        return env

    def _get_role_instruction(self, role: str) -> str:
        """Get role instruction: config override > default > empty."""
        custom = self.config.get("roles", {}).get(role, {}).get("instruction")
        if custom:
            return custom
        return DEFAULT_ROLE_INSTRUCTIONS.get(role, "")

    @staticmethod
    def _extract_test_command(document: str) -> str | None:
        """Extract test_command from yaml metadata block at end of design document."""
        match = re.search(
            r'```ya?ml\s*\n.*?test_command:\s*["\']?(.+?)["\']?\s*\n.*?```',
            document, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _build_context(self, request: AgentRequest) -> str:
        """Build context string from issue sections + latest full review."""
        issue = request.issue
        parts = [f"Issue: {issue.title} (#{issue.id})"]
        gate_summary = request.context.get("gate_failure_summary", "")
        if gate_summary:
            parts.append(f"\n!!! PREVIOUS GATE FAILURES - FIX THESE FIRST !!!\n{gate_summary}")
        for section_name in ["需求", "设计", "开发步骤", "测试"]:
            content = issue.sections.get(section_name, "")
            if content:
                parts.append(f"\n--- {section_name} ---\n{content}")
        # Use full review from log (not the summary in .md)
        latest_review = request.context.get("latest_review", "")
        if latest_review:
            parts.append(f"\n--- Latest Review Feedback ---\n{latest_review}")
        # Add feedback summary if available
        feedback = request.context.get("feedback_summary", "")
        if feedback:
            parts.append(f"\n--- Feedback Status ---\n{feedback}")
        # Add acceptance tests for developer
        acc_tests = request.context.get("acceptance_tests", "")
        if acc_tests:
            parts.append(f"\n--- {acc_tests}")
        # Add unresolved items for reviewer
        unresolved = request.context.get("unresolved_feedback", "")
        if unresolved:
            parts.append(f"\n--- {unresolved}")
        # Add code diff if available
        code_diff = request.context.get("code_diff", "")
        if code_diff:
            parts.append(f"\n--- Code Diff ---\n{code_diff[:30000]}")  # cap at 30K chars
        # Add gate output for developer to diagnose test failures
        gate_output = request.context.get("gate_output", "")
        if gate_output:
            parts.append(f"\n--- Gate Output (test failures) ---\n{gate_output}")
        # Add gate failure output for reviewer to analyze
        gate_failure = request.context.get("gate_failure_output", "")
        if gate_failure:
            parts.append(f"\n--- Gate Failure Output ---\n{gate_failure}")
        return "\n".join(parts)

    def _build_review_context(self, request: AgentRequest) -> str:
        """Build context for code review, including git diff."""
        issue = request.issue
        parts = [f"Issue: {issue.title} (#{issue.id})"]
        # Include requirements
        for section_name in ["需求", "设计"]:
            content = issue.sections.get(section_name, "")
            if content:
                parts.append(f"\n--- {section_name} ---\n{content}")
        # Include code diff if available
        code_diff = request.context.get("code_diff", "")
        if code_diff:
            parts.append(f"\n--- Git Diff (Code Changes) ---\n{code_diff}")
        # Include latest review for context
        latest_review = request.context.get("latest_review", "")
        if latest_review:
            parts.append(f"\n--- Previous Review ---\n{latest_review}")
        # Add unresolved items for reviewer
        unresolved = request.context.get("unresolved_feedback", "")
        if unresolved:
            parts.append(f"\n--- {unresolved}")
        return "\n".join(parts)

    def _extract_comments_from_text(self, text: str) -> list[ReviewComment]:
        """Try to extract structured review comments from non-JSON text."""
        items = re.split(r'\n(?=\d+[\.\)]\s|\-\s)', text.strip())
        if len(items) <= 1 and not re.match(r'\d+[\.\)]\s|\-\s', text.strip()):
            return []

        severity_patterns = {
            Severity.CRITICAL: r'(?:critical|严重|致命)',
            Severity.HIGH: r'(?:high|高)',
            Severity.MEDIUM: r'(?:medium|中)',
            Severity.LOW: r'(?:low|低)',
        }
        bracket_pattern = re.compile(r'\[(CRITICAL|HIGH|MEDIUM|LOW)\]', re.IGNORECASE)
        colon_pattern = re.compile(r'(?:severity|严重性)[：:]\s*(critical|high|medium|low)', re.IGNORECASE)

        comments = []
        for item in items:
            item = item.strip()
            if not item:
                continue
            clean = re.sub(r'^\d+[\.\)]\s*', '', item)
            clean = re.sub(r'^-\s*', '', clean)
            if not clean:
                continue

            severity = Severity.MEDIUM
            bm = bracket_pattern.search(clean)
            cm = colon_pattern.search(clean)
            if bm:
                severity = _SEVERITY_MAP[bm.group(1).lower()]
                clean = bracket_pattern.sub('', clean).strip()
            elif cm:
                severity = _SEVERITY_MAP[cm.group(1).lower()]
                clean = colon_pattern.sub('', clean).strip()
                clean = clean.rstrip('。.')
            else:
                for sev, pat in severity_patterns.items():
                    if re.search(pat, clean, re.IGNORECASE):
                        severity = sev
                        break

            comments.append(ReviewComment(severity=severity, message=clean))
        return comments

    # ------------------------------------------------------------------ #
    #  Concrete action methods (shared orchestration logic)               #
    # ------------------------------------------------------------------ #

    async def preflight(self, request: AgentRequest) -> PreflightOutput:
        context = self._build_context(request)
        role_instruction = self._get_role_instruction("designer")
        system = f"{role_instruction}\n\n" if role_instruction else ""
        system += dedent("""\
            Quickly assess the feasibility of this project. Do NOT produce a full design.

            Output ONLY JSON:
            {
                "feasibility": "high" | "medium" | "low",
                "estimated_complexity": "simple" | "moderate" | "complex" | "very_complex",
                "risks": ["risk 1", "risk 2", ...],
                "tech_stack_recommendation": "optional suggestion"
            }

            Assessment criteria:
            - feasibility: can this realistically be built with the specified tech stack?
            - complexity: how many subsystems, how much integration work?
            - risks: what could go wrong or take much longer than expected?
        """)
        prompt = f"{context}\n\nAssess feasibility. Be brief and direct."
        result, usage = await self._run(prompt, cwd=request.context.get("worktree_path"), system_prompt=system)
        try:
            data = self._extract_json(result)
            return PreflightOutput(
                feasibility=data.get("feasibility", "medium"),
                estimated_complexity=data.get("estimated_complexity", "moderate"),
                risks=data.get("risks", []),
                tech_stack_recommendation=data.get("tech_stack_recommendation"),
                usage=usage)
        except Exception:
            return PreflightOutput(feasibility="medium", estimated_complexity="moderate",
                                   risks=["Could not assess — preflight parse failed"], usage=usage)

    async def design(self, request: AgentRequest) -> DesignOutput:
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        role_instruction = self._get_role_instruction("designer")
        system = f"{role_instruction}\n\n" if role_instruction else ""
        system += dedent("""\
            Produce a CONCISE technical design document (target 5,000-15,000 characters).
            Focus on: architecture decisions, component interfaces, data flow,
            error handling strategy, and TEST STRATEGY.

            TEST STRATEGY is mandatory. You MUST include:
            - The exact test command to run all tests (e.g. "make -C module test",
              "go test ./...", "pytest -v"). For monorepos, specify the full path.
            - What tests to add or modify, and how they map to acceptance criteria.

            Do NOT include implementation details (code, pseudocode, function
            bodies) — those belong in the code.
            Do NOT repeat the requirements — reference them by name.

            If there are previous review comments, address each one specifically.

            CRITICAL: You MUST output the COMPLETE design document every time,
            not just the changes or a supplement. The previous version will be
            REPLACED entirely by your output. If you only output a patch,
            the full design will be lost.

            At the END of the document, output a fenced metadata block:
            ```yaml
            test_command: "<exact shell command to run tests>"
            ```

            Output the design document in markdown format.
        """)
        prompt = f"{context}\n\nProduce the technical design for this issue."
        result, usage = await self._run(prompt, cwd=cwd, system_prompt=system)
        test_command = self._extract_test_command(result)
        return DesignOutput(document=result, test_command=test_command, usage=usage)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        cwd = request.context.get("worktree_path")
        context = self._build_context(request)

        role_instruction = self._get_role_instruction("developer")
        system = f"{role_instruction}\n\n" if role_instruction else ""
        system += dedent("""\
            Implement the code based on the design document. You MUST:
            1. Create actual source files in the working directory
            2. Write tests
            3. Make sure the code compiles/runs without errors
            4. Create a .gitignore appropriate for the project (e.g. /target for Rust, node_modules/ for JS)
            5. Never mark acceptance tests as ignored/skipped — they must run with the default test command

            If there are previous review comments or test failures,
            address each one specifically.

            After writing code, provide a COMPLETE summary of everything
            implemented so far (not just what changed this round).
            The previous summary will be REPLACED by your output.
        """)
        prompt = f"{context}\n\nImplement the code based on the design. Write actual files."
        # Session semantics: engine passes session_id or resume_id via context
        session_id = request.context.get("session_id")
        resume_id = request.context.get("resume_id")
        result, usage = await self._run(
            prompt, cwd=cwd, system_prompt=system,
            session_id=session_id, resume_id=resume_id)
        files_changed = await self._get_files_changed(cwd or "")
        return DevelopOutput(summary=result, files_changed=files_changed, usage=usage)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        cwd = request.context.get("worktree_path")
        # Use diff-aware context if code_diff provided (develop review),
        # otherwise use standard context (design review)
        if request.context.get("code_diff"):
            context = self._build_review_context(request)
        else:
            context = self._build_context(request)

        # Different prompt for design review vs develop review
        is_develop_review = bool(request.context.get("code_diff"))

        if is_develop_review:
            role_instruction = self._get_role_instruction("code_reviewer")
            system = f"{role_instruction}\n\n" if role_instruction else ""
            system += dedent("""\
            You are reviewing a code change. The git diff is provided below.
            The code has already passed build and all tests via the gate.
            If gate failure output is provided, analyze why tests are failing.
            Focus on: logic correctness, design quality, potential issues that tests don't catch.
            Do NOT check whether source files exist — that is the gate's job.""")
        else:
            role_instruction = self._get_role_instruction("design_reviewer")
            system = f"{role_instruction}\n\n" if role_instruction else ""
            system += dedent("""\
            You are reviewing a DESIGN DOCUMENT, not code.
            Evaluate the design for: completeness, architectural soundness,
            interface clarity, error handling strategy, and testability.
            Do NOT check whether source files or code exist — implementation
            happens in a later phase. Focus only on the design quality.

            CRITICAL review item: The design MUST include a test strategy section with:
            - An exact test command (e.g. "make -C module test", "go test ./...")
            - A yaml metadata block at the end with test_command field
            If either is missing, flag as HIGH severity.""")

        system += dedent("""
            Focus on: logic correctness, design quality, potential issues that tests don't catch.

            For each issue found, classify its severity:
            - critical: breaks core functionality, security vulnerability, data corruption
            - high: missing required feature, significant logic bug
            - medium: code quality, minor missing feature, style
            - low: naming, minor improvement

            Also check if previously unresolved feedback items are now addressed.
            Propose 1-3 new test cases if you find issues worth testing.

            Output ONLY JSON:
            {
                "comments": [{"severity": "...", "message": "...", "location": "..."}],
                "resolved_item_ids": ["F1", "F3"],
                "proposed_tests": [{"name": "test_name", "description": "what to test", "expected_behavior": "expected result"}]
            }
        """)
        prompt = f"{context}\n\nReview the current design/implementation against requirements."

        result, usage = await self._run(prompt, cwd=cwd, system_prompt=system)

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
                    message=c.get("message") or c.get("description") or c.get("issue") or str(c),
                    location=c.get("location"),
                ))
            return ReviewOutput(
                comments=comments,
                resolved_item_ids=data.get("resolved_item_ids", []),
                proposed_tests=[TestCase(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    expected_behavior=t.get("expected_behavior", ""),
                ) for t in data.get("proposed_tests", [])],
                reviewer=self.config.get("type", "unknown"),
                usage=usage,
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Failed to parse review JSON: %s", e)
            comments = self._extract_comments_from_text(result)
            if not comments:
                comments = [ReviewComment(
                    severity=Severity.HIGH,
                    message=f"Review output could not be parsed:\n{result}",
                )]
            return ReviewOutput(comments=comments, reviewer=self.config.get("type", "unknown"), usage=usage)

    # ------------------------------------------------------------------ #
    #  Utilities                                                           #
    # ------------------------------------------------------------------ #

    async def _get_files_changed(self, worktree_path: str) -> list[str]:
        if not worktree_path:
            return []

        async def _git_cmd(args):
            # Named _git_cmd (not _run) to avoid shadowing the abstract _run method
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
            return [f for f in stdout.decode().strip().splitlines() if f]

        changed = await _git_cmd(["git", "diff", "--name-only", "HEAD"])
        untracked = await _git_cmd(["git", "ls-files", "--others", "--exclude-standard"])
        return sorted(set(changed + untracked))

    def _extract_json(self, raw: str) -> dict:
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
