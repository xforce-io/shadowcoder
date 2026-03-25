from __future__ import annotations

import asyncio
import dataclasses
import logging
import re as _re
import uuid
from pathlib import Path

from shadowcoder.agents.types import AgentRequest, AgentActionFailed, AgentUsage
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import Issue, IssueStatus, TaskStatus
from shadowcoder.core.task_manager import TaskManager

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, bus, issue_store, task_manager, agent_registry, config, repo_path):
        self.bus = bus
        self.issue_store = issue_store
        self.task_manager = task_manager
        self.agents = agent_registry
        self.config = config
        self.repo_path = repo_path
        self._usage_by_issue: dict[int, list[AgentUsage]] = {}
        self._bind_commands()

    def _bind_commands(self):
        self.bus.subscribe(MessageType.CMD_CREATE_ISSUE, self._on_create)
        self.bus.subscribe(MessageType.CMD_DESIGN, self._on_design)
        self.bus.subscribe(MessageType.CMD_DEVELOP, self._on_develop)
        self.bus.subscribe(MessageType.CMD_RUN, self._on_run)
        self.bus.subscribe(MessageType.CMD_RESUME, self._on_resume)
        self.bus.subscribe(MessageType.CMD_APPROVE, self._on_approve)
        self.bus.subscribe(MessageType.CMD_CANCEL, self._on_cancel)
        self.bus.subscribe(MessageType.CMD_LIST, self._on_list)
        self.bus.subscribe(MessageType.CMD_INFO, self._on_info)
        self.bus.subscribe(MessageType.CMD_CLEANUP, self._on_cleanup)
        self.bus.subscribe(MessageType.CMD_ITERATE, self._on_iterate)

    def _track_usage(self, issue_id: int, usage: AgentUsage | None,
                     phase: str = "", round_num: int = 0):
        """Accumulate usage for an issue, stamping phase metadata."""
        if usage is None:
            return
        stamped = dataclasses.replace(usage, phase=phase, round_num=round_num)
        self._usage_by_issue.setdefault(issue_id, []).append(stamped)

    def _total_cost(self, issue_id: int) -> float:
        """Get total cost for an issue."""
        usages = self._usage_by_issue.get(issue_id, [])
        return sum(u.cost_usd or 0 for u in usages)

    def _total_tokens(self, issue_id: int) -> tuple[int, int]:
        """Get total (input_tokens, output_tokens) for an issue."""
        usages = self._usage_by_issue.get(issue_id, [])
        return (sum(u.input_tokens for u in usages),
                sum(u.output_tokens for u in usages))

    def _usage_summary(self, issue_id: int) -> str:
        """Format usage summary with per-phase breakdown."""
        usages = self._usage_by_issue.get(issue_id, [])
        if not usages:
            return "No usage data"
        input_t, output_t = self._total_tokens(issue_id)
        cost = self._total_cost(issue_id)
        total_duration = sum(u.duration_ms for u in usages) / 1000
        lines = [
            f"Calls: {len(usages)} | "
            f"Tokens: {input_t:,} in + {output_t:,} out | "
            f"Cost: ${cost:.4f} | "
            f"Time: {total_duration:.1f}s"
        ]

        # Per-phase breakdown (only if phases are recorded)
        phases: dict[str, list] = {}
        for u in usages:
            if u.phase:
                phases.setdefault(u.phase, []).append(u)
        if phases:
            lines.append("Phase breakdown:")
            for phase, phase_usages in phases.items():
                p_cost = sum(u.cost_usd or 0 for u in phase_usages)
                p_calls = len(phase_usages)
                pct = (p_cost / cost * 100) if cost > 0 else 0
                lines.append(f"  {phase}: {p_calls} calls, ${p_cost:.4f} ({pct:.0f}%)")

        return "\n".join(lines)

    @staticmethod
    def _truncate_output(output: str, max_chars: int = 3000) -> str:
        """Truncate long output preserving head (compile errors) and tail (summary)."""
        if len(output) <= max_chars:
            return output
        half = max_chars // 2
        return output[:half] + "\n\n... [truncated] ...\n\n" + output[-half:]

    def _extract_gate_failure_summary(self, gate_output: str) -> str:
        """Extract FAILED test names and key error lines from gate output."""
        lines = gate_output.splitlines()
        summary_parts = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("FAILED ") or stripped.endswith(" FAILED"):
                summary_parts.append(stripped)
            elif _re.match(r'^E\s+\w*Error:', stripped):
                summary_parts.append(stripped)
            elif "panicked at" in stripped:
                summary_parts.append(stripped)
            elif stripped.startswith("--- FAIL:"):
                summary_parts.append(stripped)
        return "\n".join(summary_parts)

    async def _run_command(self, cmd: str, cwd: str) -> tuple[bool, str]:
        """Run a shell command, return (passed, output)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        passed = proc.returncode == 0
        return passed, output

    async def _detect_test_command(self, worktree_path: str) -> str:
        """Detect test command from project files."""
        p = Path(worktree_path)
        if (p / "Cargo.toml").exists():
            return "cargo test 2>&1"
        if (p / "go.mod").exists():
            return "go test ./... 2>&1"
        if (p / "package.json").exists():
            return "npm test 2>&1"
        if (p / "pyproject.toml").exists() or (p / "setup.py").exists():
            return "python -m pytest -v 2>&1"
        if (p / "Makefile").exists():
            return "make test 2>&1"
        raise RuntimeError(
            f"Cannot detect test command for {worktree_path}. "
            f"Set build.test_command in config.")

    # Common patterns that indicate a test passed (language-agnostic heuristics)
    _PASS_PATTERNS = (" ... ok", " PASSED", " passed", "PASS: ", " ✓")
    _SKIP_PATTERNS = (" ... ignored", " SKIPPED", " skipped", " ... skip")

    async def _gate_check(self, issue_id: int, worktree_path: str,
                          proposed_tests: list) -> tuple[bool, str, str]:
        """Symbolic gate: run test command, verify acceptance tests executed and passed."""
        test_cmd = self.config.get_test_command()
        if not test_cmd:
            if not worktree_path:
                return True, "no worktree, gate skipped", ""
            try:
                test_cmd = await self._detect_test_command(worktree_path)
            except RuntimeError as e:
                return False, str(e), ""

        passed, output = await self._run_command(test_cmd, cwd=worktree_path)
        if not passed:
            return False, "build/tests failed", output

        # Check for stray files
        try:
            untracked = await self._get_untracked_files(worktree_path)
            stray = self._detect_stray_files(untracked)
            if stray:
                warning = f"\nWARNING: Stray files in worktree root: {', '.join(stray)}\nRemove these or move them to a test directory."
                output += warning
        except Exception:
            pass  # best-effort

        # Verify each acceptance test was actually executed and passed
        not_passed = []
        for tc in proposed_tests:
            name = tc["name"]
            if name not in output:
                not_passed.append(f"{name} (not found in output)")
                continue
            # Check if the test was skipped/ignored
            if any(f"{name}{pat}" in output for pat in self._SKIP_PATTERNS):
                not_passed.append(f"{name} (skipped/ignored)")
                continue
            # Check if the test actually passed (heuristic)
            if not any(f"{name}{pat}" in output for pat in self._PASS_PATTERNS):
                # Name present but no pass indicator — run individually as fallback
                individual_ok = await self._run_individual_test(
                    worktree_path, name)
                if not individual_ok:
                    not_passed.append(f"{name} (failed individual run)")

        if not_passed:
            return False, f"acceptance tests not passed: {not_passed}", output

        return True, "gate passed", output

    async def _get_untracked_files(self, worktree_path: str) -> list[str]:
        """Get list of untracked files in worktree."""
        proc = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return [f for f in stdout.decode().strip().splitlines() if f]

    def _detect_stray_files(self, untracked: list[str]) -> list[str]:
        """Flag files in worktree root that look like temp/debug artifacts."""
        return [f for f in untracked
                if "/" not in f and f.endswith((".py", ".js", ".ts", ".rs", ".go"))]

    async def _run_individual_test(self, worktree_path: str, test_name: str) -> bool:
        """Run a single test with language-specific force-include flags."""
        p = Path(worktree_path)
        if (p / "Cargo.toml").exists():
            cmd = f"cargo test {test_name} -- --include-ignored 2>&1"
        elif (p / "go.mod").exists():
            cmd = f"go test -run {test_name} -v ./... 2>&1"
        elif (p / "pyproject.toml").exists() or (p / "setup.py").exists():
            cmd = f"python -m pytest -k {test_name} -v 2>&1"
        elif (p / "package.json").exists():
            cmd = f"npx jest -t {test_name} 2>&1"
        else:
            return False  # can't determine how to run individually
        passed, _ = await self._run_command(cmd, cwd=worktree_path)
        return passed

    async def _get_code_diff(self, worktree_path: str) -> str:
        """Get git diff of all changes in the worktree."""
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        diff = stdout.decode("utf-8", errors="replace")

        proc2 = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await proc2.communicate()
        for fpath in stdout2.decode().strip().splitlines():
            full = Path(worktree_path) / fpath
            if full.exists() and full.stat().st_size < 50000:
                diff += f"\n\n=== NEW FILE: {fpath} ===\n{full.read_text(errors='replace')}"
        return diff

    def _review_decision(self, review) -> str:
        """Decide based on comment severity counts.

        Returns: "pass", "conditional_pass", or "retry".
        CRITICAL always → retry. HIGH 1-2 → conditional_pass. HIGH 3+ → retry.
        The pass_threshold config controls what happens with conditional_pass
        downstream (design: always accept; develop: see _run_develop_cycle).
        """
        from shadowcoder.agents.types import Severity
        critical = sum(1 for c in review.comments if c.severity == Severity.CRITICAL)
        high = sum(1 for c in review.comments if c.severity == Severity.HIGH)
        if critical > 0:
            return "retry"
        if high == 0:
            return "pass"
        if high <= 2:
            return "conditional_pass"
        return "retry"

    def _get_latest_review(self, issue_id: int, review_section_key: str) -> str:
        """Extract the latest full review from the log file."""
        log = self.issue_store.get_log(issue_id)
        if not log:
            return ""
        # Find the last occurrence of the review section key in the log
        marker = f"] {review_section_key}\n"
        idx = log.rfind(marker)
        if idx < 0:
            return ""
        # Extract from that point to the next log entry (## [) or end
        start = idx + len(marker)
        next_entry = log.find("\n\n## [", start)
        if next_entry < 0:
            return log[start:].strip()
        return log[start:next_entry].strip()

    def _check_budget(self, issue_id: int) -> bool:
        """Check if budget exceeded. Returns True if over budget."""
        max_budget = self.config.get_max_budget_usd()
        if max_budget is None:
            return False
        return self._total_cost(issue_id) > max_budget

    def _get_gate_tests(self, issue_id: int) -> list:
        """Get tests for gate check based on gate mode."""
        fb = self.issue_store.load_feedback(issue_id)
        tests = list(fb.get("acceptance_tests", []))
        if self.config.get_gate_mode() == "strict":
            tests.extend(fb.get("supplementary_tests", []))
        # Fallback: if no categorized tests yet, use legacy proposed_tests
        if not tests:
            tests = list(fb.get("proposed_tests", []))
        return tests

    @staticmethod
    def _fetch_url_content(url: str) -> str:
        """Fetch text content from a URL.

        GitHub issue URLs are converted to API calls to get structured content.
        """
        import json
        import re
        import urllib.request

        # GitHub issue URL → API call for structured content
        gh_match = re.match(
            r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url
        )
        if gh_match:
            owner, repo, number = gh_match.groups()
            api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
            headers = {"Accept": "application/vnd.github.v3+json",
                        "User-Agent": "shadowcoder"}
            # Use gh CLI token if available for higher rate limits
            try:
                import subprocess
                token = subprocess.check_output(
                    ["gh", "auth", "token"], stderr=subprocess.DEVNULL
                ).decode().strip()
                if token:
                    headers["Authorization"] = f"token {token}"
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = [f"# {data['title']}", ""]
            if data.get("labels"):
                labels = ", ".join(l["name"] for l in data["labels"])
                parts.append(f"Labels: {labels}")
                parts.append("")
            if data.get("body"):
                parts.append(data["body"])
            return "\n".join(parts)

        # Generic URL
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8")

    def _log(self, issue_id: int, entry: str):
        """Append to 航海日志."""
        try:
            self.issue_store.append_log(issue_id, entry)
        except Exception:
            logger.debug("Failed to write log for issue %d", issue_id)

    async def _on_create(self, msg):
        title = msg.payload["title"]
        priority = msg.payload.get("priority", "medium")
        tags = msg.payload.get("tags")
        description = msg.payload.get("description")
        if description and Path(description).is_file():
            description = Path(description).read_text(encoding="utf-8")
        elif description and description.startswith(("http://", "https://")):
            description = self._fetch_url_content(description)
        issue = self.issue_store.create(title, priority=priority, tags=tags,
                                        description=description)
        self._log(issue.id, f"Issue 创建: {title}")
        await self.bus.publish(Message(MessageType.EVT_ISSUE_CREATED,
            {"issue_id": issue.id, "title": issue.title}))

    async def _review_with_retry(self, reviewer, request, max_retries=3):
        for attempt in range(1, max_retries + 1):
            try:
                return await reviewer.review(request)
            except Exception:
                if attempt == max_retries:
                    raise
                await self.bus.publish(Message(MessageType.EVT_ERROR,
                    {"message": f"reviewer failed, retry {attempt}/{max_retries}"}))

    def _update_feedback(self, issue_id: int, review, current_round: int,
                         is_design_review: bool = False):
        """Update feedback state after a review. Pure symbolic logic."""
        fb = self.issue_store.load_feedback(issue_id)
        items = fb.get("items", [])

        # Mark resolved items
        resolved_ids = set(review.resolved_item_ids)
        for item in items:
            if item["id"] in resolved_ids and not item["resolved"]:
                item["resolved"] = True
                item["resolved_round"] = current_round

        # Unresolved items: bump times_raised
        for item in items:
            if not item["resolved"] and item["id"] not in resolved_ids:
                item["times_raised"] = item.get("times_raised", 1) + 1
                item["escalation_level"] = min(item["times_raised"], 4)

        # New comments → new FeedbackItems
        existing_ids = {item["id"] for item in items}
        next_num = max((int(item["id"][1:]) for item in items), default=0) + 1
        for comment in review.comments:
            fid = f"F{next_num}"
            next_num += 1
            items.append({
                "id": fid,
                "category": comment.severity.value,
                "description": comment.message,
                "round_introduced": current_round,
                "times_raised": 1,
                "resolved": False,
                "escalation_level": 1,
            })

        # Route proposed tests to the right bucket
        target_key = "acceptance_tests" if is_design_review else "supplementary_tests"
        tests = fb.get(target_key, [])
        for tc in review.proposed_tests:
            if not any(t["name"] == tc.name for t in tests):
                tests.append({
                    "name": tc.name,
                    "description": tc.description,
                    "expected_behavior": tc.expected_behavior,
                    "category": tc.category,
                    "round_proposed": current_round,
                })
        fb[target_key] = tests

        # Also maintain proposed_tests as union for backward compat
        all_tests = fb.get("proposed_tests", [])
        for tc in review.proposed_tests:
            if not any(t["name"] == tc.name for t in all_tests):
                all_tests.append({
                    "name": tc.name,
                    "description": tc.description,
                    "expected_behavior": tc.expected_behavior,
                    "category": tc.category,
                    "round_proposed": current_round,
                })
        fb["proposed_tests"] = all_tests

        fb["items"] = items
        self.issue_store.save_feedback(issue_id, fb)

    def _format_feedback_for_agent(self, issue_id: int) -> str:
        """Format feedback state for injection into agent context."""
        fb = self.issue_store.load_feedback(issue_id)
        items = fb.get("items", [])
        if not items:
            return ""

        resolved = [i for i in items if i["resolved"]]
        unresolved = [i for i in items if not i["resolved"]]

        lines = []
        if resolved:
            lines.append(f"已解决 ({len(resolved)}/{len(items)}):")
            for item in resolved:
                lines.append(f"  [R{item['round_introduced']}] #{item['id']} {item['description'][:60]} ✓")

        if unresolved:
            lines.append(f"\n未解决 ({len(unresolved)}/{len(items)}):")
            for item in unresolved:
                escalated = self._escalate_feedback_text(item)
                intro = item["round_introduced"]
                raised = item["times_raised"]
                lines.append(f"  [R{intro}, {raised}次] #{item['id']} {escalated}")

        return "\n".join(lines)

    def _format_unresolved_for_reviewer(self, issue_id: int) -> str:
        """Format unresolved items for reviewer prompt."""
        fb = self.issue_store.load_feedback(issue_id)
        items = fb.get("items", [])
        unresolved = [i for i in items if not i["resolved"]]
        if not unresolved:
            return ""
        lines = ["当前未解决的 feedback items:"]
        for item in unresolved:
            lines.append(f"  #{item['id']}: {item['description'][:100]}")
        lines.append("\n请在 review 中对每个 item 判断是否已解决（列入 resolved_item_ids）。")
        return "\n".join(lines)

    def _format_acceptance_tests_for_developer(self, issue_id: int) -> str:
        """Format all tests for developer context, distinguishing types."""
        fb = self.issue_store.load_feedback(issue_id)
        acceptance = fb.get("acceptance_tests", [])
        supplementary = fb.get("supplementary_tests", [])
        # Fallback for legacy issues without categorized tests
        if not acceptance and not supplementary:
            tests = fb.get("proposed_tests", [])
            if not tests:
                return ""
            lines = ["Acceptance tests to implement (from reviewer):"]
            for tc in tests:
                lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
            lines.append("\nYou must write executable tests for each.")
            return "\n".join(lines)

        lines = []
        if acceptance:
            lines.append("Acceptance tests (MUST pass for gate):")
            for tc in acceptance:
                lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
        if supplementary:
            lines.append("Supplementary tests (should implement for quality):")
            for tc in supplementary:
                lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
        if lines:
            lines.append("\nYou must write executable tests for each. Place them in the project's existing test directory.")
        return "\n".join(lines)

    @staticmethod
    def _escalate_feedback_text(item: dict) -> str:
        times = item.get("times_raised", 1)
        desc = item["description"]
        if times >= 4:
            return f"CRITICAL [第{times}次提出，需要人类介入]: {desc}"
        elif times >= 3:
            return f"[第{times}次] {desc}\n    请给出具体代码修改。"
        elif times >= 2:
            return f"[第{times}次] {desc}\n    请明确说明修改方向。"
        return desc

    async def _run_all_reviewers(self, issue, task, action, review_section_key,
                                  code_diff: str = ""):
        reviewer_names = self.config.get_agent_for_phase(f"{action}_review")
        failed_reviewers = []
        last_review = None

        for rname in reviewer_names:
            reviewer = self.agents.get(rname)
            context = {
                "worktree_path": task.worktree_path,
                "latest_review": self._get_latest_review(issue.id, review_section_key),
                "unresolved_feedback": self._format_unresolved_for_reviewer(issue.id),
            }
            if code_diff:
                context["code_diff"] = code_diff
            request = AgentRequest(action="review", issue=issue, context=context)
            try:
                review = await self._review_with_retry(reviewer, request)
                self._track_usage(issue.id, review.usage, phase=f"{action}_review")
                self.issue_store.append_review(issue.id, review_section_key, review)
                last_review = review
                decision = self._review_decision(review)
                await self.bus.publish(Message(MessageType.EVT_REVIEW_RESULT, {
                    "issue_id": issue.id, "reviewer": rname,
                    "passed": decision in ("pass", "conditional_pass"),
                    "comments": len(review.comments)}))
            except Exception:
                failed_reviewers.append(rname)
                logger.warning("Reviewer %s unavailable after retries", rname)

        if len(failed_reviewers) == len(reviewer_names):
            raise RuntimeError(f"All reviewers unavailable: {failed_reviewers}")

        return last_review

    async def _run_design_cycle(self, issue, task):
        """Design cycle: design → review → repeat or approved. No gate."""
        max_rounds = self.config.get_max_review_rounds()
        action_label = "Design"
        section_key = "设计"
        review_section_key = "Design Review"
        success_status = IssueStatus.APPROVED

        try:
            for round_num in range(1, max_rounds + 1):
                issue = self.issue_store.get(issue.id)
                if issue.status != IssueStatus.DESIGNING:
                    self.issue_store.transition_status(issue.id, IssueStatus.DESIGNING)
                issue = self.issue_store.get(issue.id)
                self._log(issue.id, f"{action_label} R{round_num} 开始")
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

                agent = self.agents.get(self.config.get_agent_for_phase("design"))
                latest_review = self._get_latest_review(issue.id, review_section_key)
                request = AgentRequest(action="design", issue=issue,
                    context={
                        "worktree_path": task.worktree_path,
                        "latest_review": latest_review,
                        "feedback_summary": self._format_feedback_for_agent(issue.id),
                    })

                output = await agent.design(request)
                content = output.document

                self._track_usage(issue.id, output.usage, phase="design", round_num=round_num)
                if output.usage:
                    self._log(issue.id,
                        f"Usage: {output.usage.input_tokens}+{output.usage.output_tokens} tokens, "
                        f"${output.usage.cost_usd or 0:.4f}")
                if self._check_budget(issue.id):
                    summary = self._usage_summary(issue.id)
                    self._log(issue.id, f"预算超限 → BLOCKED\n{summary}")
                    self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                    self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
                    task.status = TaskStatus.FAILED
                    await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                        "issue_id": issue.id, "task_id": task.task_id,
                        "reason": f"budget exceeded: {summary}"}))
                    return

                vfile = self.issue_store.save_version(issue.id, "design", round_num, content)
                self.issue_store.update_section(issue.id, section_key, content)
                self._log(issue.id,
                    f"{action_label} R{round_num} Agent 产出\n"
                    f"内容长度: {len(content)} 字符, 存档: {vfile}")

                self.issue_store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
                issue = self.issue_store.get(issue.id)

                last_review = await self._run_all_reviewers(issue, task, "design", review_section_key)
                if last_review:
                    self._update_feedback(issue.id, last_review, round_num, is_design_review=True)

                decision = self._review_decision(last_review) if last_review else "retry"

                if decision in ("pass", "conditional_pass"):
                    self.issue_store.transition_status(issue.id, success_status)
                    self._log(issue.id,
                        f"{action_label} Review R{round_num} — {decision.upper()} → {success_status.value}")
                    self._log(issue.id, f"=== 总计 ===\n{self._usage_summary(issue.id)}")
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return

                # retry — log why
                critical = sum(1 for c in last_review.comments
                               if last_review and c.severity.value == "critical") if last_review else 0
                high = sum(1 for c in last_review.comments
                           if last_review and c.severity.value == "high") if last_review else 0
                if last_review:
                    from shadowcoder.agents.types import Severity
                    critical_msgs = [c.message[:100] for c in last_review.comments if c.severity == Severity.CRITICAL]
                    high_msgs = [c.message[:100] for c in last_review.comments if c.severity == Severity.HIGH]
                    detail = ""
                    if critical_msgs:
                        detail += "\nCRITICAL:\n" + "\n".join(f"  - {m}" for m in critical_msgs)
                    if high_msgs:
                        detail += "\nHIGH:\n" + "\n".join(f"  - {m}" for m in high_msgs)
                    self._log(issue.id,
                        f"{action_label} Review R{round_num} — RETRY (CRITICAL={critical}, HIGH={high}){detail}")
                else:
                    self._log(issue.id,
                        f"{action_label} Review R{round_num} — RETRY (CRITICAL={critical}, HIGH={high})")
                issue = self.issue_store.get(issue.id)

            self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
            self._log(issue.id,
                f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id,
                 "reason": f"review not passed after {max_rounds} rounds"}))

        except AgentActionFailed as e:
            partial = e.partial_output if e.partial_output else ""
            if partial:
                self.issue_store.update_section(issue.id, section_key, partial)
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            self._log(issue.id, f"{action_label} Agent 操作失败 → FAILED: {e}")
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
        except Exception as e:
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            self._log(issue.id, f"{action_label} 异常 → FAILED: {e}")
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))

    async def _run_develop_cycle(self, issue, task):
        """Develop cycle: develop → gate → review → repeat or done."""
        max_rounds = self.config.get_max_review_rounds()
        action_label = "Develop"
        section_key = "开发步骤"
        review_section_key = "Dev Review"

        proposed_tests = self._get_gate_tests(issue.id)

        try:
            gate_fail_count = 0
            conditional_pass_count = 0
            last_gate_output = ""
            current_session_id = str(uuid.uuid4())
            use_resume = False

            for round_num in range(1, max_rounds + 1):
                issue = self.issue_store.get(issue.id)
                if issue.status != IssueStatus.DEVELOPING:
                    self.issue_store.transition_status(issue.id, IssueStatus.DEVELOPING)
                issue = self.issue_store.get(issue.id)
                self._log(issue.id, f"{action_label} R{round_num} 开始")
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

                agent = self.agents.get(self.config.get_agent_for_phase("develop"))
                latest_review = self._get_latest_review(issue.id, review_section_key)
                ctx_dict = {
                    "worktree_path": task.worktree_path,
                    "gate_failure_summary": self._extract_gate_failure_summary(last_gate_output) if last_gate_output else "",
                    "latest_review": latest_review,
                    "feedback_summary": self._format_feedback_for_agent(issue.id),
                    "acceptance_tests": self._format_acceptance_tests_for_developer(issue.id),
                    "gate_output": self._truncate_output(last_gate_output) if last_gate_output else "",
                }
                if use_resume:
                    ctx_dict["resume_id"] = current_session_id
                else:
                    ctx_dict["session_id"] = current_session_id
                request = AgentRequest(action="develop", issue=issue, context=ctx_dict)

                output = await agent.develop(request)
                use_resume = True
                content = output.summary
                files = output.files_changed
                feat_summary = f" (files: {', '.join(files)})" if files else ""

                self._track_usage(issue.id, output.usage, phase="develop", round_num=round_num)
                if output.usage:
                    self._log(issue.id,
                        f"Usage: {output.usage.input_tokens}+{output.usage.output_tokens} tokens, "
                        f"${output.usage.cost_usd or 0:.4f}")
                if self._check_budget(issue.id):
                    summary = self._usage_summary(issue.id)
                    self._log(issue.id, f"预算超限 → BLOCKED\n{summary}")
                    self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                    self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
                    task.status = TaskStatus.FAILED
                    await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                        "issue_id": issue.id, "task_id": task.task_id,
                        "reason": f"budget exceeded: {summary}"}))
                    return

                vfile = self.issue_store.save_version(issue.id, "develop", round_num, content)
                self.issue_store.update_section(issue.id, section_key, content)
                self._log(issue.id,
                    f"{action_label} R{round_num} Agent 产出{feat_summary}\n"
                    f"内容长度: {len(content)} 字符, 存档: {vfile}")

                # Gate (symbolic)
                gate_ok, gate_msg, gate_output = await self._gate_check(
                    issue.id, task.worktree_path, proposed_tests)
                if not gate_ok:
                    gate_fail_count += 1
                    self._log(issue.id, f"Gate FAIL ({gate_fail_count}): {gate_msg}")
                    if gate_output:
                        self._log(issue.id, f"Gate output (last 1000 chars):\n{gate_output[-1000:]}")
                    last_gate_output = gate_output

                    if gate_fail_count >= 2:
                        # Escalate: ask reviewer to analyze gate failure
                        self._log(issue.id, "Gate 连续失败，升级给 reviewer 分析")
                        reviewer_names = self.config.get_agent_for_phase("develop_review")
                        if reviewer_names:
                            reviewer = self.agents.get(reviewer_names[0])
                            try:
                                code_diff_for_escalation = ""
                                if task.worktree_path:
                                    try:
                                        code_diff_for_escalation = await self._get_code_diff(task.worktree_path)
                                    except Exception:
                                        pass
                                review_request = AgentRequest(action="review", issue=issue,
                                    context={
                                        "worktree_path": task.worktree_path,
                                        "code_diff": code_diff_for_escalation,
                                        "gate_failure_output": self._truncate_output(gate_output),
                                        "unresolved_feedback": self._format_unresolved_for_reviewer(issue.id),
                                    })
                                review = await reviewer.review(review_request)
                                self._track_usage(issue.id, review.usage, phase="gate_escalation", round_num=round_num)
                                self._update_feedback(issue.id, review, round_num, is_design_review=False)
                                self.issue_store.append_review(issue.id, "Dev Review", review)
                            except Exception:
                                pass  # reviewer analysis is best-effort
                        gate_fail_count = 0  # reset after escalation

                    issue = self.issue_store.get(issue.id)
                    continue  # back to develop, skip review

                self._log(issue.id, f"Gate PASS R{round_num}")
                gate_fail_count = 0  # reset on gate pass
                last_gate_output = ""

                # Review (neural, based on git diff)
                self.issue_store.transition_status(issue.id, IssueStatus.DEV_REVIEW)
                issue = self.issue_store.get(issue.id)

                code_diff = ""
                if task.worktree_path:
                    try:
                        code_diff = await self._get_code_diff(task.worktree_path)
                    except Exception as ex:
                        logger.warning("Could not get code diff: %s", ex)

                last_review = await self._run_all_reviewers(
                    issue, task, "develop", review_section_key, code_diff=code_diff)
                if last_review:
                    self._update_feedback(issue.id, last_review, round_num, is_design_review=False)
                    proposed_tests = self._get_gate_tests(issue.id)

                decision = self._review_decision(last_review) if last_review else "retry"

                if decision == "pass":
                    self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                    self._log(issue.id,
                        f"Dev Review R{round_num} — PASS → done")
                    self._log(issue.id, f"=== 总计 ===\n{self._usage_summary(issue.id)}")
                    self._log(issue.id, "提示: 可以用 `merge #id` 合并分支，或 `cleanup #id` 清理 worktree")
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return

                if decision == "conditional_pass":
                    threshold = self.config.get_pass_threshold()
                    if threshold != "no_high_or_critical" or conditional_pass_count > 0:
                        # Lenient mode: accept immediately.
                        # Strict mode, second conditional_pass: accept to avoid loop.
                        suffix = " (2nd)" if conditional_pass_count > 0 else ""
                        self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                        self._log(issue.id,
                            f"Dev Review R{round_num} — CONDITIONAL_PASS{suffix} → done")
                        self._log(issue.id, f"=== 总计 ===\n{self._usage_summary(issue.id)}")
                        self._log(issue.id, "提示: 可以用 `merge #id` 合并分支，或 `cleanup #id` 清理 worktree")
                        task.status = TaskStatus.COMPLETED
                        await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                            {"issue_id": issue.id, "task_id": task.task_id}))
                        return
                    # Strict mode, first conditional_pass: fix HIGH issues
                    conditional_pass_count += 1
                    self._log(issue.id,
                        f"Dev Review R{round_num} — CONDITIONAL_PASS → 再修一轮 HIGH issues")

                # retry — fresh session for next develop (review feedback may change direction)
                current_session_id = str(uuid.uuid4())
                use_resume = False

                critical = sum(1 for c in last_review.comments
                               if last_review and c.severity.value == "critical") if last_review else 0
                high = sum(1 for c in last_review.comments
                           if last_review and c.severity.value == "high") if last_review else 0
                if last_review:
                    from shadowcoder.agents.types import Severity
                    critical_msgs = [c.message[:100] for c in last_review.comments if c.severity == Severity.CRITICAL]
                    high_msgs = [c.message[:100] for c in last_review.comments if c.severity == Severity.HIGH]
                    detail = ""
                    if critical_msgs:
                        detail += "\nCRITICAL:\n" + "\n".join(f"  - {m}" for m in critical_msgs)
                    if high_msgs:
                        detail += "\nHIGH:\n" + "\n".join(f"  - {m}" for m in high_msgs)
                    self._log(issue.id,
                        f"Dev Review R{round_num} — RETRY (CRITICAL={critical}, HIGH={high}){detail}")
                else:
                    self._log(issue.id,
                        f"Dev Review R{round_num} — RETRY (CRITICAL={critical}, HIGH={high})")
                issue = self.issue_store.get(issue.id)

            self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
            self._log(issue.id,
                f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id,
                 "reason": f"review not passed after {max_rounds} rounds"}))

        except AgentActionFailed as e:
            partial = e.partial_output if e.partial_output else ""
            if partial:
                self.issue_store.update_section(issue.id, section_key, partial)
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            self._log(issue.id, f"{action_label} Agent 操作失败 → FAILED: {e}")
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
        except Exception as e:
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            self._log(issue.id, f"{action_label} 异常 → FAILED: {e}")
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))

    async def _on_design(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])

        # Run preflight if this is the first design attempt (no existing design)
        if "设计" not in issue.sections or not issue.sections["设计"]:
            agent = self.agents.get(self.config.get_agent_for_phase("design"))
            request = AgentRequest(action="preflight", issue=issue,
                context={"worktree_path": None})
            try:
                pf = await agent.preflight(request)
                self._track_usage(issue.id, pf.usage, phase="preflight")
                pf_summary = (f"Feasibility: {pf.feasibility} | "
                             f"Complexity: {pf.estimated_complexity} | "
                             f"Risks: {', '.join(pf.risks) or 'none identified'}")
                if pf.tech_stack_recommendation:
                    pf_summary += f" | Tech: {pf.tech_stack_recommendation}"
                self._log(issue.id, f"Preflight 评估\n{pf_summary}")

                if pf.feasibility == "low":
                    self._log(issue.id, "Preflight: feasibility=low → BLOCKED，等待人类确认")
                    self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
                    await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                        "issue_id": issue.id,
                        "reason": f"Preflight: low feasibility — {pf_summary}"}))
                    return
            except Exception as e:
                self._log(issue.id, f"Preflight 跳过 (error: {e})")

        # Continue with design cycle
        task = await self.task_manager.create(issue, repo_path=self.repo_path,
            action="design", agent_name=self.config.get_agent_for_phase("design"))
        await self._run_design_cycle(issue, task)

    async def _on_develop(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(issue, repo_path=self.repo_path,
            action="develop", agent_name=self.config.get_agent_for_phase("develop"))
        await self._run_develop_cycle(issue, task)

    async def _on_run(self, msg):
        """Run the full lifecycle: create (optional) → design → develop → done."""
        issue_id = msg.payload.get("issue_id")

        # If title given, create issue first
        if "title" in msg.payload:
            await self._on_create(msg)
            issues = self.issue_store.list_all()
            issue_id = issues[-1].id

        issue = self.issue_store.get(issue_id)

        # Recover interrupted issue: infer which phase to resume
        if issue.status in (IssueStatus.IN_PROGRESS, IssueStatus.FAILED):
            stage = self._infer_blocked_stage(issue)
            if stage == "develop":
                self._log(issue_id, f"run 恢复: {issue.status.value} → 继续 develop")
                issue.status = IssueStatus.APPROVED
            else:
                self._log(issue_id, f"run 恢复: {issue.status.value} → 重跑 design")
                issue.status = IssueStatus.CREATED
            self.issue_store.save(issue)

        # Design phase
        if issue.status in (IssueStatus.CREATED, IssueStatus.BLOCKED):
            await self._on_design(Message(MessageType.CMD_DESIGN, {"issue_id": issue_id}))
            issue = self.issue_store.get(issue_id)
            if issue.status == IssueStatus.BLOCKED:
                self._log(issue_id, "run 暂停: design BLOCKED，需人类介入")
                return
            if issue.status != IssueStatus.APPROVED:
                self._log(issue_id, f"run 停止: design 后 status={issue.status.value}")
                return

        # Develop phase
        if issue.status == IssueStatus.APPROVED:
            await self._on_develop(Message(MessageType.CMD_DEVELOP, {"issue_id": issue_id}))
            issue = self.issue_store.get(issue_id)
            if issue.status == IssueStatus.BLOCKED:
                self._log(issue_id, "run 暂停: develop BLOCKED，需人类介入")
                return

        issue = self.issue_store.get(issue_id)
        self._log(issue_id, f"run 结束: status={issue.status.value}")

    def _infer_blocked_stage(self, issue):
        """Infer which stage was running based on issue sections."""
        if "Dev Review" in issue.sections:
            return "develop"
        if "开发" in issue.sections:
            return "develop"
        if "Design Review" in issue.sections:
            return "design"
        if "设计" in issue.sections:
            return "design"
        return None

    async def _on_resume(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is not BLOCKED"}))
            return
        action = self._infer_blocked_stage(issue)
        self._log(issue.id, f"人类介入: resume → 重跑 {action}")
        if action == "design":
            await self._on_design(msg)
        elif action == "develop":
            await self._on_develop(msg)
        else:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"cannot infer blocked stage for issue #{issue.id}"}))

    async def _on_approve(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is not BLOCKED"}))
            return
        stage = self._infer_blocked_stage(issue)
        next_status = IssueStatus.DONE if stage == "develop" else IssueStatus.APPROVED
        self.issue_store.transition_status(issue.id, next_status)
        self._log(issue.id, f"人类介入: approve → {next_status.value}")
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": next_status.value}))

    async def _on_cancel(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        self.issue_store.transition_status(issue.id, IssueStatus.CANCELLED)
        self._log(issue.id, "用户取消 → CANCELLED")
        for task in self.task_manager.list_active():
            if task.issue_id == issue.id:
                await self.task_manager.cancel(task.task_id)
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": "cancelled"}))

    async def _on_list(self, msg):
        issues = self.issue_store.list_all()
        await self.bus.publish(Message(MessageType.EVT_ISSUE_LIST, {
            "issues": [{"id": i.id, "title": i.title, "status": i.status.value,
                         "priority": i.priority} for i in issues]}))

    async def _on_info(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        await self.bus.publish(Message(MessageType.EVT_ISSUE_INFO, {
            "issue": {"id": issue.id, "title": issue.title,
                      "status": issue.status.value, "priority": issue.priority,
                      "tags": issue.tags, "assignee": issue.assignee,
                      "sections": list(issue.sections.keys())}}))

    async def _on_iterate(self, msg):
        """Iterate on a DONE issue: append new requirements, re-enter develop cycle."""
        issue_id = msg.payload["issue_id"]
        issue = self.issue_store.get(issue_id)

        if issue.status != IssueStatus.DONE:
            await self.bus.publish(Message(MessageType.EVT_ERROR, {
                "message": f"issue #{issue_id} is not DONE, cannot iterate"}))
            return

        requirements = msg.payload.get("requirements", "")
        if requirements:
            existing = issue.sections.get("需求", "")
            separator = "\n\n---\n\n" if existing else ""
            self.issue_store.update_section(issue_id, "需求", existing + separator + requirements)
            self._log(issue_id, f"Iterate: 追加需求\n{requirements[:200]}")

        self.issue_store.transition_status(issue_id, IssueStatus.APPROVED)
        self._log(issue_id, "Iterate: DONE → APPROVED, 重新进入 develop")
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED, {
            "issue_id": issue_id, "status": "approved"}))

        await self._on_develop(Message(MessageType.CMD_DEVELOP, {"issue_id": issue_id}))

    async def _on_cleanup(self, msg):
        issue_id = msg.payload["issue_id"]
        delete_branch = msg.payload.get("delete_branch", False)
        issue = self.issue_store.get(issue_id)

        if issue.status not in (IssueStatus.DONE, IssueStatus.CANCELLED):
            await self.bus.publish(Message(MessageType.EVT_ERROR, {
                "message": f"issue #{issue_id} is not DONE or CANCELLED, cannot cleanup"}))
            return

        wt_manager = self.task_manager.worktree_manager
        if await wt_manager.exists(self.repo_path, issue_id):
            await wt_manager.cleanup(self.repo_path, issue_id, delete_branch=delete_branch)
            self._log(issue_id, f"Worktree 已清理 (delete_branch={delete_branch})")
            await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED, {
                "issue_id": issue_id, "status": "cleaned_up"}))
        else:
            await self.bus.publish(Message(MessageType.EVT_ERROR, {
                "message": f"issue #{issue_id} has no worktree to clean up"}))
