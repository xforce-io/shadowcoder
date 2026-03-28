from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

from shadowcoder.agents.types import (
    AcceptanceOutput, AgentRequest, AgentUsage, DesignOutput, DevelopOutput,
    PreparedCall, PreflightOutput, ReviewOutput, TestCase, ReviewComment, Severity,
)

logger = logging.getLogger(__name__)

# Context size limits (characters)
MAX_CODE_DIFF_CHARS = 30_000
MAX_DESIGN_SUMMARY_CHARS = 1_500

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

    def _load_system_prompt(self, role: str) -> str:
        """Load system prompt for a role from instructions files.

        Search order:
        1. <project>/.shadowcoder/roles/<role>/*.md  (project-level)
        2. ~/.shadowcoder/roles/<role>/*.md           (user-level)

        Within each directory, all .md files are sorted by name and concatenated.
        The first directory that contains any .md files wins (no merging across levels).
        """
        for d in self.config.get("_roles_dirs", []):
            role_dir = Path(d) / role
            if not role_dir.is_dir():
                continue
            md_files = sorted(role_dir.glob("*.md"),
                              key=lambda f: (f.name != "soul.md", f.name))
            if md_files:
                return "\n\n".join(
                    f.read_text(encoding="utf-8").strip() for f in md_files
                )

        return ""

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
            parts.append(f"\n--- Code Diff ---\n{code_diff[:MAX_CODE_DIFF_CHARS]}")
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
        req_content = issue.sections.get("需求", "")
        if req_content:
            parts.append(f"\n--- 需求 ---\n{req_content}")
        # Include design as REFERENCE ONLY — truncated to key decisions.
        # Full design doc causes reviewers to critique the doc wording
        # instead of focusing on code correctness.
        design_content = issue.sections.get("设计", "")
        if design_content:
            truncated = design_content[:MAX_DESIGN_SUMMARY_CHARS]
            if len(design_content) > MAX_DESIGN_SUMMARY_CHARS:
                truncated += "\n... (design truncated — review the CODE, not this document)"
            parts.append(
                f"\n--- 设计概要 (reference only — do NOT review this document) ---\n"
                f"{truncated}"
            )
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
        # Include gate/acceptance failure output for escalation analysis
        gate_failure = request.context.get("gate_failure_output", "")
        if gate_failure:
            parts.append(f"\n--- Failure Output ---\n{gate_failure}")
        # Include acceptance script if provided (for escalation analysis)
        acceptance_script = request.context.get("acceptance_script", "")
        if acceptance_script:
            parts.append(
                f"\n--- Acceptance Script (验收脚本 — judge whether the SCRIPT or the CODE is wrong) ---\n"
                f"{acceptance_script}")
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
    #  Prepare methods — build the full call without executing            #
    # ------------------------------------------------------------------ #

    def prepare_preflight(self, request: AgentRequest) -> PreparedCall:
        context = self._build_context(request)
        system = self._load_system_prompt("preflight")
        prompt = f"{context}\n\nAssess feasibility. Be brief and direct."
        return PreparedCall(
            action="preflight", system_prompt=system, prompt=prompt,
            cwd=request.context.get("worktree_path"))

    def prepare_design(self, request: AgentRequest) -> PreparedCall:
        context = self._build_context(request)
        system = self._load_system_prompt("designer")
        prompt = f"{context}\n\nProduce the technical design for this issue."
        return PreparedCall(
            action="design", system_prompt=system, prompt=prompt,
            cwd=request.context.get("worktree_path"))

    def prepare_develop(self, request: AgentRequest) -> PreparedCall:
        context = self._build_context(request)
        system = self._load_system_prompt("developer")
        prompt = f"{context}\n\nImplement the code based on the design. Write actual files."
        return PreparedCall(
            action="develop", system_prompt=system, prompt=prompt,
            cwd=request.context.get("worktree_path"),
            session_id=request.context.get("session_id"),
            resume_id=request.context.get("resume_id"))

    def prepare_review(self, request: AgentRequest) -> PreparedCall:
        is_escalation = bool(request.context.get("escalation"))
        is_develop_review = bool(request.context.get("code_diff"))
        if is_escalation:
            context = self._build_review_context(request)
            system = self._load_system_prompt("escalation_reviewer")
            prompt = (
                f"{context}\n\n"
                f"Determine the root cause of the repeated failure: "
                f"is the bug in the CODE or in the ACCEPTANCE SCRIPT?"
            )
        elif is_develop_review:
            context = self._build_review_context(request)
            system = self._load_system_prompt("code_reviewer")
            prompt = f"{context}\n\nReview the current design/implementation against requirements."
        else:
            context = self._build_context(request)
            system = self._load_system_prompt("design_reviewer")
            prompt = f"{context}\n\nReview the current design/implementation against requirements."
        return PreparedCall(
            action="review", system_prompt=system, prompt=prompt,
            cwd=request.context.get("worktree_path"))

    def prepare_write_acceptance_script(self, request: AgentRequest) -> PreparedCall:
        context = self._build_context(request)
        system = self._load_system_prompt("acceptance_writer")
        pre_gate_failure = request.context.get("pre_gate_failure", "")
        if pre_gate_failure:
            prompt = (
                f"{context}\n\n"
                f"IMPORTANT — your previous acceptance script PASSED on unchanged code:\n"
                f"{pre_gate_failure}\n\n"
                f"Analyze why it passed and write a STRONGER script."
            )
        else:
            prompt = (
                f"{context}\n\n"
                f"Write an acceptance test script (bash) for this issue. "
                f"The script must FAIL on the current code and PASS after the fix/feature."
            )
        return PreparedCall(
            action="write_acceptance_script", system_prompt=system, prompt=prompt,
            cwd=request.context.get("worktree_path"))

    # ------------------------------------------------------------------ #
    #  Concrete action methods (shared orchestration logic)               #
    # ------------------------------------------------------------------ #

    async def preflight(self, request: AgentRequest) -> PreflightOutput:
        call = self.prepare_preflight(request)
        result, usage = await self._run(call.prompt, cwd=call.cwd, system_prompt=call.system_prompt)
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
        call = self.prepare_design(request)
        result, usage = await self._run(call.prompt, cwd=call.cwd, system_prompt=call.system_prompt)
        test_command = self._extract_test_command(result)
        return DesignOutput(document=result, test_command=test_command, usage=usage)

    async def develop(self, request: AgentRequest) -> DevelopOutput:
        call = self.prepare_develop(request)
        result, usage = await self._run(
            call.prompt, cwd=call.cwd, system_prompt=call.system_prompt,
            session_id=call.session_id, resume_id=call.resume_id)
        files_changed = await self._get_files_changed(call.cwd or "")
        return DevelopOutput(summary=result, files_changed=files_changed, usage=usage)

    async def review(self, request: AgentRequest) -> ReviewOutput:
        call = self.prepare_review(request)
        result, usage = await self._run(call.prompt, cwd=call.cwd, system_prompt=call.system_prompt)

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

    async def write_acceptance_script(self, request: AgentRequest) -> AcceptanceOutput:
        call = self.prepare_write_acceptance_script(request)
        result, usage = await self._run(call.prompt, cwd=call.cwd, system_prompt=call.system_prompt)

        # Agent mode: the model may write a script file instead of returning
        # the script as text.  Check the worktree for script files first.
        script = self._find_written_script(call.cwd)
        if not script:
            script = self._extract_bash_script(result)
        return AcceptanceOutput(script=script, usage=usage)

    @staticmethod
    def _find_written_script(cwd: str | None) -> str | None:
        """Look for a bash script the agent wrote into the worktree."""
        if not cwd:
            return None
        from pathlib import Path
        candidates = [
            "acceptance_test.sh",
            "acceptance.sh",
            "test_acceptance.sh",
        ]
        for name in candidates:
            p = Path(cwd) / name
            if p.exists():
                content = p.read_text(encoding="utf-8")
                if content.strip().startswith("#!"):
                    # Clean up — remove the file so it doesn't pollute the worktree
                    p.unlink()
                    return content.strip()
        return None

    @staticmethod
    def _extract_bash_script(text: str) -> str:
        """Extract a bash script from model output that may contain commentary."""
        import re
        # Try to find a fenced code block (```bash, ```sh, or bare ```)
        # that contains a bash script.  Pick the longest match so we grab
        # the real script, not a one-liner example.
        pattern = r"```(?:bash|sh)?\s*\n(.*?)```"
        blocks = re.findall(pattern, text, re.DOTALL)
        if blocks:
            # Pick the longest code block — most likely the full script
            script = max(blocks, key=len).strip()
        else:
            # No fenced block — treat the whole output as the script
            script = text.strip()
        if not script.startswith("#!"):
            script = "#!/bin/bash\nset -euo pipefail\n\n" + script
        return script

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
