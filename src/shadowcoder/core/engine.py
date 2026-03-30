from __future__ import annotations

import asyncio
import dataclasses
import logging
import re as _re
import time
import uuid
from pathlib import Path

from shadowcoder.agents.types import AgentRequest, AgentActionFailed, AgentUsage, PreparedCall
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.language import detect_language
from shadowcoder.core.models import (
    Issue, IssueStatus, TaskStatus,
    BLOCKED_BUDGET, BLOCKED_MAX_ROUNDS, BLOCKED_ACCEPTANCE_WEAK,
    BLOCKED_ACCEPTANCE_CONFIRMED, BLOCKED_ACCEPTANCE_BUG,
    BLOCKED_LOW_FEASIBILITY, BLOCKED_METRIC_GATE,
)
from shadowcoder.core.task_manager import TaskManager

logger = logging.getLogger(__name__)

# Extensions that should never appear in code diffs sent to reviewers.
_BINARY_EXTS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe",
    ".o", ".a", ".class", ".jar", ".wasm",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".zip", ".gz", ".tar", ".bz2", ".xz",
    ".pdf", ".woff", ".woff2", ".ttf", ".eot",
})

# Max size (bytes) for untracked files included in code diffs.
_MAX_NEW_FILE_SIZE = 50_000


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
        self.bus.subscribe(MessageType.CMD_UNBLOCK, self._on_unblock)
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
                p_time = sum(u.duration_ms for u in phase_usages) / 1000
                pct = (p_cost / cost * 100) if cost > 0 else 0
                lines.append(f"  {phase}: {p_calls} calls, ${p_cost:.4f} ({pct:.0f}%), {p_time:.1f}s")

        return "\n".join(lines)

    def _dump_agent_context(
        self,
        issue_id: int,
        phase: str,
        round_num: int,
        action: str,
        agent_name: str,
        agent,
        prepared_call,
    ) -> None:
        """Dump the fully-assembled agent input to a markdown file for debugging."""
        if not self.config.get_dump_agent_context():
            return

        from datetime import datetime, timezone

        max_chars = self.config.get_dump_agent_context_max_chars()
        system_prompt = prepared_call.system_prompt
        prompt = prepared_call.prompt
        if max_chars:
            system_prompt = system_prompt[:max_chars]
            prompt = prompt[:max_chars]

        timestamp = datetime.now(timezone.utc).isoformat()
        def _yaml_val(v):
            if v is None:
                return "null"
            return str(v)

        frontmatter = (
            f"---\n"
            f"issue_id: {issue_id}\n"
            f"phase: {phase}\n"
            f"round: {round_num}\n"
            f"action: {action}\n"
            f"agent_name: {agent_name}\n"
            f"agent_type: {agent.config.get('type', 'unknown')}\n"
            f"model: {agent._get_model()}\n"
            f"cwd: {_yaml_val(prepared_call.cwd)}\n"
            f"permission_mode: {agent._get_permission_mode()}\n"
            f"session_id: {_yaml_val(prepared_call.session_id)}\n"
            f"resume_id: {_yaml_val(prepared_call.resume_id)}\n"
            f"timestamp: \"{timestamp}\"\n"
            f"system_prompt_chars: {len(prepared_call.system_prompt)}\n"
            f"prompt_chars: {len(prepared_call.prompt)}\n"
            f"---\n"
        )

        content = (
            f"{frontmatter}\n"
            f"## System Prompt\n\n"
            f"{system_prompt}\n\n"
            f"## Prompt\n\n"
            f"{prompt}\n"
        )

        issue_dir = Path(self.repo_path) / self.config.get_issue_dir() / f"{issue_id:04d}"
        prompts_dir = issue_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        ts_short = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{action}_r{round_num}_{agent_name}_{ts_short}.md"
        out_path = prompts_dir / filename
        try:
            out_path.write_text(content, encoding="utf-8")
            logger.debug("Dumped agent context to %s", out_path)
        except Exception:
            logger.warning("Failed to dump agent context to %s", out_path, exc_info=True)

    @staticmethod
    def _truncate_output(output: str, max_chars: int = 3000) -> str:
        """Truncate long output preserving head (compile errors) and tail (summary)."""
        if len(output) <= max_chars:
            return output
        half = max_chars // 2
        return output[:half] + "\n\n... [truncated] ...\n\n" + output[-half:]

    def _extract_gate_failure_summary(self, gate_output: str, worktree_path: str = "") -> str:
        """Extract FAILED test names and key error lines from gate output."""
        profile = detect_language(worktree_path) if worktree_path else None
        lines = gate_output.splitlines()
        summary_parts = []
        for line in lines:
            stripped = line.strip()
            if profile:
                for pat in profile.gate_failure_patterns:
                    if _re.search(pat, stripped):
                        summary_parts.append(stripped)
                        break
            else:
                # Fallback: common patterns across languages
                if stripped.startswith("FAILED ") or stripped.endswith(" FAILED"):
                    summary_parts.append(stripped)
                elif _re.match(r'^E\s+\w*Error:', stripped):
                    summary_parts.append(stripped)
                elif "panicked at" in stripped:
                    summary_parts.append(stripped)
                elif stripped.startswith("--- FAIL:"):
                    summary_parts.append(stripped)
        return "\n".join(summary_parts)

    @staticmethod
    def _error_hash(summary: str | None) -> str:
        """Hash an error summary for same-error detection across rounds."""
        if not summary:
            return ""
        import hashlib
        return hashlib.sha256(summary.strip().encode()).hexdigest()[:16]

    async def _extract_error_summary(
        self, raw_output: str, *, issue_id: int | None = None
    ) -> str:
        """Use utility agent (LLM) to extract root-cause error summary from test output.

        Falls back to _truncate_output if utility agent is unavailable or fails.
        """
        if not raw_output or len(raw_output) <= 3000:
            return raw_output

        utility_agent_name = self.config.get_agent_for_phase("utility")
        agent = self.agents.get(utility_agent_name)
        if agent is None:
            return self._truncate_output(raw_output)

        prompt = (
            "Below is the output from a failed test run. "
            "Extract the ROOT CAUSE error — the first few lines that explain WHY the test failed. "
            "Ignore cascade errors (e.g. 'missing call' lines caused by an earlier failure). "
            "Output only the key error lines (3-10 lines), nothing else.\n\n"
            f"```\n{raw_output[-8000:]}\n```"
        )
        system_prompt = "You are a test output analyzer. Extract root cause errors concisely."
        if issue_id is not None:
            self._dump_agent_context(
                issue_id, "utility", 0, "utility",
                utility_agent_name, agent,
                PreparedCall(action="utility", system_prompt=system_prompt, prompt=prompt))
        try:
            summary, usage = await agent._run(prompt, system_prompt=system_prompt)
            if issue_id is not None and usage:
                self._track_usage(issue_id, usage, phase="utility")
            return summary.strip() if summary.strip() else self._truncate_output(raw_output)
        except Exception:
            return self._truncate_output(raw_output)

    async def _run_command(self, cmd: str, cwd: str) -> tuple[bool, str, float]:
        """Run a shell command, return (passed, output, elapsed_seconds)."""
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        elapsed = time.monotonic() - t0
        output = stdout.decode("utf-8", errors="replace")
        passed = proc.returncode == 0
        return passed, output, elapsed

    async def _gate_check(self, issue_id: int, worktree_path: str,
                          proposed_tests: list) -> tuple[bool, str, str, float]:
        """Symbolic gate: run test command, verify acceptance tests executed and passed.
        Returns (passed, message, output, elapsed_seconds)."""
        test_cmd = self.config.get_test_command()
        profile = None
        if not test_cmd:
            # Try design-provided test_command
            fb = self.issue_store.load_feedback(issue_id)
            test_cmd = fb.get("test_command")
        if not test_cmd:
            if not worktree_path:
                return True, "no worktree, gate skipped", "", 0.0
            profile = detect_language(worktree_path)
            if not profile:
                return False, (
                    f"Cannot detect test command for {worktree_path}. "
                    f"Set build.test_command in config."), "", 0.0
            test_cmd = profile.test_command

        # If we need to verify proposed tests, ensure verbose output so test
        # names appear. Design-provided commands like "pytest -q" won't list
        # individual test names, causing false "not found in output" failures.
        gate_cmd = test_cmd
        if proposed_tests and "pytest" in test_cmd:
            # Replace -q with -v, or append -v if neither present
            if "-q" in test_cmd:
                gate_cmd = test_cmd.replace("-q", "-v")
            elif "-v" not in test_cmd:
                gate_cmd = test_cmd.rstrip() + " -v"

        passed, output, gate_elapsed = await self._run_command(gate_cmd, cwd=worktree_path)
        if not passed:
            return False, "build/tests failed", output, gate_elapsed

        # Check for stray files
        try:
            untracked = await self._get_untracked_files(worktree_path)
            if profile:
                stray = [f for f in untracked
                         if "/" not in f and any(f.endswith(ext) for ext in profile.source_extensions)]
            else:
                stray = [f for f in untracked
                         if "/" not in f and f.endswith((".py", ".js", ".ts", ".rs", ".go"))]
            if stray:
                warning = f"\nWARNING: Stray files in worktree root: {', '.join(stray)}\nRemove these or move them to a test directory."
                output += warning
        except Exception:
            pass

        # Determine patterns
        pass_patterns = profile.pass_patterns if profile else (" ... ok", " PASSED", " passed", "PASS: ", " ✓")
        skip_patterns = profile.skip_patterns if profile else (" ... ignored", " SKIPPED", " skipped", " ... skip")

        # Verify each acceptance test was actually executed and passed
        not_passed = []
        for tc in proposed_tests:
            name = tc["name"]
            if name not in output:
                # Test name not in output — try running individually before failing
                individual_ok = await self._run_individual_test(
                    name, profile, worktree_path)
                if not individual_ok:
                    not_passed.append(f"{name} (not found in output)")
                continue
            if any(f"{name}{pat}" in output for pat in skip_patterns):
                not_passed.append(f"{name} (skipped/ignored)")
                continue
            if not any(f"{name}{pat}" in output for pat in pass_patterns):
                # Run individually as fallback
                individual_ok = await self._run_individual_test(
                    name, profile, worktree_path)
                if not individual_ok:
                    not_passed.append(f"{name} (failed individual run)")

        if not_passed:
            return False, f"acceptance tests not passed: {not_passed}", output, gate_elapsed

        return True, "gate passed", output, gate_elapsed

    async def _run_individual_test(self, name: str, profile, worktree_path: str) -> bool:
        """Try running a single test by name. Returns True if it passes."""
        if profile and profile.individual_test_cmd:
            cmd = profile.individual_test_cmd.format(name=name)
        else:
            # Fallback: try pytest -k for Python projects
            cmd = f"python -m pytest -k {name} -v 2>&1"
        ok, _, _elapsed = await self._run_command(cmd, cwd=worktree_path)
        return ok

    async def _get_untracked_files(self, worktree_path: str) -> list[str]:
        """Get list of untracked files in worktree."""
        proc = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return [f for f in stdout.decode().strip().splitlines() if f]

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
            if not full.exists() or full.stat().st_size >= _MAX_NEW_FILE_SIZE:
                continue
            if full.suffix.lower() in _BINARY_EXTS:
                continue
            try:
                content = full.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                continue  # skip files that aren't valid text
            diff += f"\n\n=== NEW FILE: {fpath} ===\n{content}"
        return diff

    @staticmethod
    def _review_blames_acceptance(review) -> bool:
        """Check if reviewer identified the acceptance script as the problem."""
        if not review or not review.comments:
            return False
        marker = "[TARGET:acceptance_script]"
        return any(marker.lower() in c.message.lower() for c in review.comments)

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
        # Extract from that point to the next log entry or end
        start = idx + len(marker)
        next_entry = log.find("\n[20", start)  # next timestamped entry
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
        title = msg.payload.get("title", "")
        priority = msg.payload.get("priority", "medium")
        tags = msg.payload.get("tags")
        description = msg.payload.get("description")
        if description and Path(description).is_file():
            description = Path(description).read_text(encoding="utf-8")
        elif description and description.startswith(("http://", "https://")):
            description = self._fetch_url_content(description)
        # Extract title from fetched content if not provided
        if not title and description and description.startswith("# "):
            title = description.split("\n", 1)[0].removeprefix("# ").strip()
        if not title:
            title = "Untitled"
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
        next_num = max(
            (int(item["id"][1:]) for item in items if item["id"].startswith("F")),
            default=0) + 1
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

    def _update_metric_gate_feedback(self, issue_id: int, round_num: int,
                                      metrics_str: str, failures: list[str]):
        """Record metric gate failure as feedback. Replaces previous metric_gate feedback."""
        fb = self.issue_store.load_feedback(issue_id)
        items = fb.get("items", [])

        # Remove previous metric gate feedback (replace, not accumulate)
        items = [item for item in items if item.get("source") != "metric_gate"]

        # Compute next F-number
        next_num = max(
            (int(item["id"][1:]) for item in items if item["id"].startswith("F")),
            default=0) + 1

        items.append({
            "id": f"F{next_num}",
            "source": "metric_gate",
            "category": "high",
            "description": (
                f"Metric gate failed at R{round_num}: {'; '.join(failures)}. "
                f"Metrics were: {metrics_str}. "
                f"Code was reverted. Try a different approach."
            ),
            "round_introduced": round_num,
            "times_raised": 1,
            "resolved": False,
            "escalation_level": 2,
        })
        fb["items"] = items
        self.issue_store.save_feedback(issue_id, fb)

    def _resolve_metric_gate_feedback(self, issue_id: int, round_num: int):
        """Auto-resolve metric gate feedback when baselines are met."""
        fb = self.issue_store.load_feedback(issue_id)
        for item in fb.get("items", []):
            if item.get("source") == "metric_gate" and not item["resolved"]:
                item["resolved"] = True
                item["resolved_round"] = round_num
        self.issue_store.save_feedback(issue_id, fb)

    def _format_feedback_for_agent(self, issue_id: int,
                                    round_num: int = 0,
                                    has_gate_failure: bool = False) -> str:
        """Format feedback state for injection into agent context.

        When has_gate_failure is True, old feedback items are folded to a
        single line so the developer can focus on fixing the gate error.
        """
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
            # In gate-failure context, fold old items to reduce noise
            folded_count = 0
            for item in unresolved:
                intro = item["round_introduced"]
                distance = round_num - intro if round_num else 0
                if has_gate_failure and distance >= 2:
                    folded_count += 1
                    continue
                escalated = self._escalate_feedback_text(item)
                raised = item["times_raised"]
                dist_hint = f", {distance}轮前" if distance >= 2 else ""
                lines.append(f"  [R{intro}, {raised}次{dist_hint}] #{item['id']} {escalated}")
            if folded_count:
                lines.append(f"  (另有 {folded_count} 条旧反馈待 gate 通过后复审)")

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

    @staticmethod
    def _read_metrics(worktree_path: str) -> tuple[bool, dict[str, float], str]:
        """Read metrics.json from worktree root.
        Returns (ok, metrics_dict, error_message).
        Rejects non-finite values (NaN, Inf).
        """
        import json
        import math
        from pathlib import Path
        metrics_path = Path(worktree_path) / "metrics.json"
        if not metrics_path.exists():
            return False, {}, "metrics.json not found in worktree root"
        try:
            raw = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            return False, {}, f"metrics.json parse error: {e}"
        if not isinstance(raw, dict):
            return False, {}, f"metrics.json must be a JSON object, got {type(raw).__name__}"
        metrics = {}
        for k, v in raw.items():
            if not isinstance(v, (int, float)):
                continue
            fv = float(v)
            if not math.isfinite(fv):
                continue
            metrics[k] = fv
        return True, metrics, ""

    @staticmethod
    def _validate_metrics(
        metrics: dict[str, float],
        targets: dict[str, str],
    ) -> tuple[bool, list[str]]:
        """Validate metrics against configured baseline targets.
        Returns (all_passed, list_of_failure_descriptions).
        """
        import re
        failures = []
        for name, spec in targets.items():
            if name not in metrics:
                failures.append(f"{name}: MISSING from metrics.json")
                continue
            match = re.match(r"(>=|<=|>|<)\s*([\d.eE+-]+)$", spec.strip())
            if not match:
                failures.append(f"{name}: invalid target spec '{spec}'")
                continue
            op, threshold = match.group(1), float(match.group(2))
            value = metrics[name]
            ops = {">=": value >= threshold, "<=": value <= threshold,
                   ">": value > threshold, "<": value < threshold}
            if not ops[op]:
                failures.append(f"{name}: {value:.4f} not {op} {threshold} (baseline)")
        return len(failures) == 0, failures

    @staticmethod
    def _is_pareto_improvement(
        current: dict[str, float],
        previous: dict[str, float] | None,
        targets: dict[str, str],
        threshold: float = 0.01,
    ) -> bool:
        """Check if current metrics are a Pareto improvement over previous.

        Pareto improvement: no target metric got worse AND at least one improved
        by more than threshold. First round (previous=None) always counts.
        Only metrics named in targets are compared.
        """
        if previous is None:
            return True
        target_names = list(targets.keys())
        any_improved = False
        for name in target_names:
            curr_val = current.get(name)
            prev_val = previous.get(name)
            if curr_val is None or prev_val is None:
                continue
            delta = curr_val - prev_val
            if delta < -threshold:
                return False
            if delta > threshold:
                any_improved = True
        return any_improved

    async def _run_all_reviewers(self, issue, task, action, review_section_key,
                                  code_diff: str = "", round_num: int = 0):
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
                self._dump_agent_context(
                    issue.id, f"{action}_review", round_num,
                    f"{action}_review", rname, reviewer,
                    reviewer.prepare_review(request))
                review = await self._review_with_retry(reviewer, request)
                self._track_usage(issue.id, review.usage, phase=f"{action}_review")
                if review.usage:
                    self._log(issue.id,
                        f"Review ({rname}): {review.usage.duration_ms / 1000:.1f}s")
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

                agent_name = self.config.get_agent_for_phase("design")
                agent = self.agents.get(agent_name)
                latest_review = self._get_latest_review(issue.id, review_section_key)
                request = AgentRequest(action="design", issue=issue,
                    context={
                        "worktree_path": task.worktree_path,
                        "latest_review": latest_review,
                        "feedback_summary": self._format_feedback_for_agent(issue.id),
                    })

                self._dump_agent_context(
                    issue.id, "design", round_num, "design",
                    agent_name, agent, agent.prepare_design(request))
                output = await agent.design(request)
                content = output.document

                self._track_usage(issue.id, output.usage, phase="design", round_num=round_num)
                if output.usage:
                    self._log(issue.id,
                        f"Usage: {output.usage.input_tokens}+{output.usage.output_tokens} tokens, "
                        f"${output.usage.cost_usd or 0:.4f}, "
                        f"{output.usage.duration_ms / 1000:.1f}s")
                if self._check_budget(issue.id):
                    summary = self._usage_summary(issue.id)
                    self._log(issue.id, f"预算超限 → BLOCKED\n{summary}")
                    await self._block_issue(issue.id, task, BLOCKED_BUDGET,
                        from_status=IssueStatus.DESIGNING,
                        event_reason=f"budget exceeded: {summary}")
                    return

                vfile = self.issue_store.save_version(issue.id, "design", round_num, content)
                self.issue_store.update_section(issue.id, section_key, content)
                if output.test_command:
                    fb = self.issue_store.load_feedback(issue.id)
                    fb["test_command"] = output.test_command
                    self.issue_store.save_feedback(issue.id, fb)
                    self._log(issue.id, f"Test command: {output.test_command}")
                self._log(issue.id,
                    f"{action_label} R{round_num} Agent 产出\n"
                    f"内容长度: {len(content)} 字符, 存档: {vfile}")

                self.issue_store.transition_status(issue.id, IssueStatus.DESIGN_REVIEW)
                issue = self.issue_store.get(issue.id)

                last_review = await self._run_all_reviewers(issue, task, "design", review_section_key, round_num=round_num)
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

            self._log(issue.id,
                f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
            await self._block_issue(issue.id, task, BLOCKED_MAX_ROUNDS,
                event_reason=f"review not passed after {max_rounds} rounds")

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

    async def _escalate_to_reviewer(
        self, issue, task, round_num: int,
        failure_output: str, failure_summary: str,
    ):
        """Ask reviewer to analyze repeated failure (gate or acceptance)."""
        reviewer_names = self.config.get_agent_for_phase("develop_review")
        if not reviewer_names:
            return None
        reviewer = self.agents.get(reviewer_names[0])
        try:
            code_diff = ""
            if task.worktree_path:
                try:
                    code_diff = await self._get_code_diff(task.worktree_path)
                except Exception:
                    pass

            acceptance_path = self._acceptance_script_path(issue.id)
            acceptance_script_content = ""
            if acceptance_path.exists():
                acceptance_script_content = acceptance_path.read_text(encoding="utf-8")

            review_request = AgentRequest(action="review", issue=issue,
                context={
                    "worktree_path": task.worktree_path,
                    "code_diff": code_diff,
                    "gate_failure_output": failure_summary or self._truncate_output(failure_output),
                    "acceptance_script": acceptance_script_content,
                    "unresolved_feedback": self._format_unresolved_for_reviewer(issue.id),
                    "escalation": True,
                })
            self._dump_agent_context(
                issue.id, "escalation_review", round_num, "develop",
                reviewer_names[0], reviewer,
                reviewer.prepare_review(review_request))
            review = await reviewer.review(review_request)
            self._track_usage(issue.id, review.usage,
                              phase="gate_escalation", round_num=round_num)
            if review.usage:
                self._log(issue.id,
                    f"Escalation review: {review.usage.duration_ms / 1000:.1f}s")
            self._update_feedback(issue.id, review, round_num,
                                  is_design_review=False)
            self.issue_store.append_review(issue.id, "Dev Review", review)
            return review
        except Exception:
            return None

    def _acceptance_script_path(self, issue_id: int) -> Path:
        """Path for the acceptance test script."""
        return Path(self.issue_store.base) / f"{issue_id:04d}" / "acceptance.sh"

    async def _run_acceptance_phase(self, issue, task) -> bool:
        """Write acceptance script and verify it FAILS on unchanged code.

        Returns True if acceptance phase passed (script written and fails as expected),
        False if blocked (script keeps passing — criteria too weak).
        """
        acceptance_path = self._acceptance_script_path(issue.id)
        max_attempts = 3
        self._last_acceptance_output = ""
        last_failure_feedback = ""

        for attempt in range(1, max_attempts + 1):
            # Write or rewrite acceptance script
            acc_agent_name = self.config.get_agent_for_phase("acceptance")
            acc_agent = self.agents.get(acc_agent_name)

            ctx = {
                "worktree_path": task.worktree_path,
                "acceptance_tests": self._format_acceptance_tests_for_developer(issue.id),
                "feedback_summary": self._format_feedback_for_agent(issue.id),
            }
            if last_failure_feedback:
                ctx["pre_gate_failure"] = last_failure_feedback

            request = AgentRequest(
                action="write_acceptance_script", issue=issue, context=ctx)

            self._log(issue.id, f"Acceptance script 生成 (attempt {attempt}/{max_attempts})")
            try:
                self._dump_agent_context(
                    issue.id, "acceptance", attempt, "acceptance",
                    acc_agent_name, acc_agent,
                    acc_agent.prepare_write_acceptance_script(request))
                output = await acc_agent.write_acceptance_script(request)
            except Exception as e:
                self._log(issue.id, f"Acceptance writer 异常: {e}")
                # Non-fatal: skip acceptance phase, proceed to develop
                return True

            self._track_usage(issue.id, output.usage,
                              phase="acceptance", round_num=attempt)
            if output.usage:
                self._log(issue.id,
                    f"Acceptance writer: {output.usage.input_tokens}+{output.usage.output_tokens} tokens, "
                    f"{output.usage.duration_ms / 1000:.1f}s")
            acceptance_path.write_text(output.script, encoding="utf-8")

            # Validate bash syntax before execution
            syntax_ok, syntax_err, _elapsed = await self._run_command(
                f"bash -n {acceptance_path}", cwd=task.worktree_path)
            if not syntax_ok:
                self._log(issue.id,
                    f"Acceptance script bash 语法错误 (attempt {attempt}/{max_attempts}): "
                    f"{syntax_err[:200]}")
                last_failure_feedback = (
                    f"Your acceptance script has bash SYNTAX ERRORS:\n"
                    f"{syntax_err}\n\n"
                    f"Script content:\n{output.script}\n\n"
                    f"Fix the syntax and regenerate a valid bash script."
                )
                continue

            # Pre-gate: script must FAIL
            passed, exec_output, acc_elapsed = await self._run_command(
                f"bash {acceptance_path}", cwd=task.worktree_path)
            self._last_acceptance_output = exec_output

            if not passed:
                # Distinguish script-self-error from business assertion failure
                if "command not found" in (exec_output or ""):
                    self._log(issue.id,
                        f"Pre-gate: acceptance script 自身损坏 "
                        f"(attempt {attempt}/{max_attempts}): command not found")
                    last_failure_feedback = (
                        f"Your script crashed due to its own error "
                        f"(not an assertion failure):\n{exec_output}\n\n"
                        f"Fix the script content."
                    )
                    continue
                self._log(issue.id,
                    f"Pre-gate: acceptance script FAIL (expected) ✓ ({acc_elapsed:.1f}s)")
                return True

            self._log(issue.id,
                f"Pre-gate: acceptance script PASS — too weak "
                f"(attempt {attempt}/{max_attempts})")
            last_failure_feedback = (
                f"Your acceptance script PASSED on unchanged code. "
                f"This means your assertions don't test the actual problem.\n\n"
                f"Script content:\n{acceptance_path.read_text(encoding='utf-8')}\n\n"
                f"Execution output:\n{self._last_acceptance_output}\n\n"
                f"Analyze WHY it passed. Write stronger assertions that "
                f"will FAIL until the fix/feature is implemented."
            )

        # Exhausted retries
        self._log(issue.id,
            "Pre-gate: acceptance script still PASS after "
            f"{max_attempts} attempts → BLOCKED")
        await self._block_issue(issue.id, task, BLOCKED_ACCEPTANCE_WEAK,
            from_status=IssueStatus.APPROVED,
            event_reason="acceptance tests already pass — criteria too weak")
        return False

    async def _run_develop_cycle(self, issue, task, skip_first_develop: bool = False):
        """Develop cycle: [acceptance] → develop → gate → metric gate → review → acceptance → done."""
        max_rounds = self.config.get_max_review_rounds()
        action_label = "Develop"
        section_key = "开发步骤"
        review_section_key = "Dev Review"

        proposed_tests = self._get_gate_tests(issue.id)

        # --- Acceptance script: write and verify it fails ---
        acceptance_path = self._acceptance_script_path(issue.id)
        if acceptance_path.exists():
            # Validate existing script isn't corrupted
            _ok, _out, _ = await self._run_command(
                f"bash {acceptance_path}", cwd=task.worktree_path)
            if not _ok and "command not found" in (_out or ""):
                self._log(issue.id,
                    "Acceptance script 自身损坏 (command not found)，重新生成")
                acceptance_path.unlink()
                acceptance_ok = await self._run_acceptance_phase(issue, task)
                if not acceptance_ok:
                    return
            else:
                self._log(issue.id, "Acceptance script 已存在，跳过生成阶段")
        else:
            acceptance_ok = await self._run_acceptance_phase(issue, task)
            if not acceptance_ok:
                return
            # Optionally pause for human review before entering develop loop
            if self.config.get_confirm_acceptance():
                self._log(issue.id,
                    "Acceptance tests xfail 确认 ✓ → BLOCKED，等待人类确认")
                await self._block_issue(issue.id, task, BLOCKED_ACCEPTANCE_CONFIRMED,
                    from_status=IssueStatus.APPROVED)
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": "blocked",
                     "reason": "acceptance_confirmed"}))
                return

        try:
            gate_fail_count = 0
            conditional_pass_count = 0
            last_gate_output = ""
            last_gate_summary = ""
            last_error_hash = ""
            current_session_id = str(uuid.uuid4())
            use_resume = False
            metric_gate_retries = 0
            max_metric_retries = self.config.get_max_metric_retries()
            metric_gate_targets = self.config.get_metric_gate()

            for round_num in range(1, max_rounds + 1):
                issue = self.issue_store.get(issue.id)
                if issue.status != IssueStatus.DEVELOPING:
                    self.issue_store.transition_status(issue.id, IssueStatus.DEVELOPING)
                issue = self.issue_store.get(issue.id)
                self._log(issue.id, f"{action_label} R{round_num} 开始")
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

                # Save checkpoint for potential metric gate revert
                wt_manager = self.task_manager.worktree_manager
                checkpoint = await wt_manager.save_checkpoint(
                    task.worktree_path, f"pre-develop-R{round_num}")

                # Skip develop agent on first round if resuming from acceptance_script_bug
                if skip_first_develop and round_num == 1:
                    self._log(issue.id,
                        f"{action_label} R{round_num} 跳过 develop agent（acceptance script 已修正）")
                    skip_first_develop = False  # only skip once
                else:
                    develop_agent_name = self.config.get_agent_for_phase("develop")
                    agent = self.agents.get(develop_agent_name)
                    latest_review = self._get_latest_review(issue.id, review_section_key)
                    ctx_dict = {
                        "worktree_path": task.worktree_path,
                        "gate_failure_summary": self._extract_gate_failure_summary(last_gate_output, task.worktree_path) if last_gate_output else "",
                        "latest_review": latest_review,
                        "feedback_summary": self._format_feedback_for_agent(
                            issue.id, round_num=round_num,
                            has_gate_failure=bool(last_gate_summary)),
                        "acceptance_tests": self._format_acceptance_tests_for_developer(issue.id),
                        "gate_output": last_gate_summary if last_gate_summary else "",
                    }
                    if use_resume:
                        ctx_dict["resume_id"] = current_session_id
                    else:
                        ctx_dict["session_id"] = current_session_id
                    request = AgentRequest(action="develop", issue=issue, context=ctx_dict)

                    self._dump_agent_context(
                        issue.id, "develop", round_num, "develop",
                        develop_agent_name, agent, agent.prepare_develop(request))
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
                        await self._block_issue(issue.id, task, BLOCKED_BUDGET,
                            from_status=IssueStatus.DEVELOPING,
                            event_reason=f"budget exceeded: {summary}")
                        return

                    vfile = self.issue_store.save_version(issue.id, "develop", round_num, content)
                    self.issue_store.update_section(issue.id, section_key, content)
                    self._log(issue.id,
                        f"{action_label} R{round_num} Agent 产出{feat_summary}\n"
                        f"内容长度: {len(content)} 字符, 存档: {vfile}")

                # Gate (symbolic)
                gate_ok, gate_msg, gate_output, gate_elapsed = await self._gate_check(
                    issue.id, task.worktree_path, proposed_tests)
                if not gate_ok:
                    gate_fail_count += 1
                    self._log(issue.id, f"Gate FAIL ({gate_fail_count}, {gate_elapsed:.1f}s): {gate_msg}")
                    if gate_output:
                        gate_summary = await self._extract_error_summary(
                            gate_output, issue_id=issue.id)
                        last_gate_summary = gate_summary
                        self._log(issue.id,
                            f"Gate output (extracted):\n{gate_summary}")
                        new_hash = self._error_hash(last_gate_summary)
                        if new_hash and new_hash == last_error_hash:
                            self._log(issue.id,
                                "Same error detected in consecutive rounds — "
                                "forcing root cause analysis via reviewer")
                            gate_fail_count = 2  # triggers existing escalation logic
                        last_error_hash = new_hash
                    last_gate_output = gate_output

                    if gate_fail_count >= 2:
                        self._log(issue.id, "Gate 连续失败，升级给 reviewer 分析")
                        review = await self._escalate_to_reviewer(
                            issue, task, round_num,
                            gate_output, last_gate_summary)
                        if review and self._review_blames_acceptance(review):
                            self._log(issue.id,
                                "Reviewer 判定 acceptance script 有误 → BLOCKED，需人类介入修正验收标准")
                            await self._block_issue(issue.id, task, BLOCKED_ACCEPTANCE_BUG,
                                from_status=IssueStatus.DEVELOPING,
                                event_reason="acceptance script may be incorrect — reviewer flagged it")
                            return
                        gate_fail_count = 0  # reset after escalation

                    issue = self.issue_store.get(issue.id)
                    continue  # back to develop, skip review

                self._log(issue.id, f"Gate PASS R{round_num} ({gate_elapsed:.1f}s)")
                gate_fail_count = 0  # reset on gate pass
                last_gate_output = ""

                # Metric gate: check baselines if configured
                if metric_gate_targets:
                    mg_ok, mg_metrics, mg_err = self._read_metrics(task.worktree_path)
                    if not mg_ok:
                        # metrics.json missing/invalid — normal gate fail (code incomplete)
                        gate_fail_count += 1
                        last_gate_summary = f"metrics.json: {mg_err}"
                        self._log(issue.id,
                            f"Metric gate: {mg_err} — treating as normal gate failure")
                        last_gate_output = mg_err
                        issue = self.issue_store.get(issue.id)
                        continue
                    mg_valid, mg_failures = self._validate_metrics(
                        mg_metrics, metric_gate_targets)
                    if not mg_valid:
                        metric_gate_retries += 1
                        metrics_str = ", ".join(
                            f"{k}={v:.4f}" for k, v in mg_metrics.items())
                        self._log(issue.id,
                            f"Metric gate FAIL ({metric_gate_retries}/{max_metric_retries}): "
                            f"{'; '.join(mg_failures)}")
                        if metric_gate_retries >= max_metric_retries:
                            self._log(issue.id,
                                "Metric gate: max retries reached → BLOCKED")
                            await self._block_issue(
                                issue.id, task, BLOCKED_METRIC_GATE,
                                from_status=IssueStatus.DEVELOPING,
                                event_reason=f"metric gate failed {max_metric_retries} times")
                            return
                        # Revert code to checkpoint
                        revert_ok = await wt_manager.revert_to(
                            task.worktree_path, checkpoint)
                        if not revert_ok:
                            self._log(issue.id,
                                "Metric gate: code revert failed → BLOCKED")
                            await self._block_issue(
                                issue.id, task, BLOCKED_METRIC_GATE,
                                from_status=IssueStatus.DEVELOPING,
                                event_reason="git revert failed after metric gate failure")
                            return
                        self._log(issue.id,
                            f"Metric gate: code reverted to pre-R{round_num}")
                        self._update_metric_gate_feedback(
                            issue.id, round_num, metrics_str, mg_failures)
                        last_gate_summary = f"Metric gate failed: {'; '.join(mg_failures)}"
                        last_gate_output = f"Metrics: {metrics_str}"
                        # Reset session — agent must try different approach
                        current_session_id = str(uuid.uuid4())
                        use_resume = False
                        issue = self.issue_store.get(issue.id)
                        continue
                    # Metric gate passed
                    self._resolve_metric_gate_feedback(issue.id, round_num)
                    self._log(issue.id,
                        f"Metric gate PASS "
                        f"({', '.join(f'{k}={v:.4f}' for k, v in mg_metrics.items())})")

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
                    issue, task, "develop", review_section_key,
                    code_diff=code_diff, round_num=round_num)
                if last_review:
                    self._update_feedback(issue.id, last_review, round_num, is_design_review=False)
                    proposed_tests = self._get_gate_tests(issue.id)

                decision = self._review_decision(last_review) if last_review else "retry"

                # Determine if review accepted
                should_accept = False
                if decision == "pass":
                    should_accept = True
                elif decision == "conditional_pass":
                    threshold = self.config.get_pass_threshold()
                    if threshold != "no_high_or_critical" or conditional_pass_count > 0:
                        should_accept = True
                    else:
                        conditional_pass_count += 1
                        self._log(issue.id,
                            f"Dev Review R{round_num} — CONDITIONAL_PASS → fix HIGH issues")

                if should_accept:
                    # Run acceptance as final gate before DONE
                    acceptance_path = self._acceptance_script_path(issue.id)
                    if acceptance_path.exists():
                        acc_passed, acc_output, acc_elapsed = await self._run_command(
                            f"bash {acceptance_path}", cwd=task.worktree_path)
                        if not acc_passed:
                            self._log(issue.id,
                                f"Acceptance FAIL after review pass ({acc_elapsed:.1f}s)")
                            if acc_output:
                                acc_summary = await self._extract_error_summary(
                                    acc_output, issue_id=issue.id)
                                last_gate_summary = acc_summary
                                last_gate_output = acc_output
                                self._log(issue.id,
                                    f"Acceptance output:\n{acc_summary}")
                            # Reset session and feedback
                            current_session_id = str(uuid.uuid4())
                            use_resume = False
                            fb = self.issue_store.load_feedback(issue.id)
                            for item in fb.get("items", []):
                                if item.get("resolved"):
                                    item["resolved"] = False
                                    item.pop("resolved_round", None)
                            self.issue_store.save_feedback(issue.id, fb)
                            issue = self.issue_store.get(issue.id)
                            continue  # back to develop loop

                        self._log(issue.id, f"Acceptance PASS ({acc_elapsed:.1f}s)")

                    # All gates passed — DONE
                    suffix = ""
                    if decision == "conditional_pass":
                        suffix = " (2nd)" if conditional_pass_count > 0 else ""
                    self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                    self._log(issue.id,
                        f"Dev Review R{round_num} — {decision.upper()}{suffix} → done")
                    self._log(issue.id, f"=== 总计 ===\n{self._usage_summary(issue.id)}")
                    self._log(issue.id,
                        "提示: 可以用 `merge #id` 合并分支，或 `cleanup #id` 清理 worktree")
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return

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

            self._log(issue.id,
                f"{action_label} review 未通过，已重试 {max_rounds} 轮 → BLOCKED")
            await self._block_issue(issue.id, task, BLOCKED_MAX_ROUNDS,
                event_reason=f"review not passed after {max_rounds} rounds")

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
                pf_dur = f" ({pf.usage.duration_ms / 1000:.1f}s)" if pf.usage else ""
                pf_summary = (f"Feasibility: {pf.feasibility} | "
                             f"Complexity: {pf.estimated_complexity} | "
                             f"Risks: {', '.join(pf.risks) or 'none identified'}")
                if pf.tech_stack_recommendation:
                    pf_summary += f" | Tech: {pf.tech_stack_recommendation}"
                self._log(issue.id, f"Preflight 评估{pf_dur}\n{pf_summary}")

                if pf.feasibility == "low":
                    self._log(issue.id, "Preflight: feasibility=low → BLOCKED，等待人类确认")
                    await self._block_issue(issue.id, None, BLOCKED_LOW_FEASIBILITY,
                        from_status=IssueStatus.CREATED,
                        event_reason=f"Preflight: low feasibility — {pf_summary}")
                    return
            except Exception as e:
                self._log(issue.id, f"Preflight 跳过 (error: {e})")

        # Create task (sets up worktree)
        task = await self.task_manager.create(issue, repo_path=self.repo_path,
            action="design", agent_name=self.config.get_agent_for_phase("design"))

        # Check test command detectability for existing projects
        if not self.config.get_test_command():
            wt = task.worktree_path
            if wt and Path(wt).exists() and any(Path(wt).iterdir()):
                if not detect_language(wt):
                    self._log(issue.id,
                        "Preflight 警告: Cannot auto-detect test command. "
                        "Designer MUST provide test_command in the design document. "
                        "Alternatively, set build.test_command in config.")

        await self._run_design_cycle(issue, task)

    async def _on_develop(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(issue, repo_path=self.repo_path,
            action="develop", agent_name=self.config.get_agent_for_phase("develop"))
        skip_develop = msg.payload.get("skip_first_develop", False)
        await self._run_develop_cycle(issue, task, skip_first_develop=skip_develop)

    async def _on_run(self, msg):
        """Run the full lifecycle: create (optional) → design → develop → done."""
        issue_id = msg.payload.get("issue_id")

        # resume_last: look up last saved issue
        if msg.payload.get("resume_last"):
            issue_id = self.issue_store.get_last()
            if issue_id is None:
                await self.bus.publish(Message(MessageType.EVT_ERROR,
                    {"message": "No previous issue to resume. Use 'run <title>' to create one."}))
                return

        # If title given, create issue first
        if "title" in msg.payload:
            # Warn if there are active issues
            active_statuses = {
                IssueStatus.CREATED, IssueStatus.DESIGNING,
                IssueStatus.DESIGN_REVIEW, IssueStatus.APPROVED,
                IssueStatus.DEVELOPING, IssueStatus.DEV_REVIEW,
            }
            active = [i for i in self.issue_store.list_all()
                      if i.status in active_statuses]
            if active:
                ids = ", ".join(f"#{i.id}({i.status.value})" for i in active)
                self._log(0, f"Warning: 已有活跃 issue: {ids}. "
                          f"如需继续已有 issue，请用 resume 命令。")

            await self._on_create(msg)
            issues = self.issue_store.list_all()
            issue_id = issues[-1].id

        self.issue_store.save_last(issue_id)

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
        elif issue.status == IssueStatus.CANCELLED:
            self._log(issue_id, "run 恢复: cancelled → 重跑 design")
            issue.status = IssueStatus.CREATED
            self.issue_store.save(issue)
        elif issue.status == IssueStatus.DONE:
            self._log(issue_id, "run 恢复: done → 重跑 develop")
            issue.status = IssueStatus.APPROVED
            self.issue_store.save(issue)

        # BLOCKED → use blocked_from to restore, fallback to inference
        if issue.status == IssueStatus.BLOCKED:
            from_status = issue.blocked_from
            if from_status:
                self._log(issue_id,
                    f"run 恢复: BLOCKED ({issue.blocked_reason}) → {from_status.value}")
                issue.blocked_reason = None
                issue.blocked_from = None
                issue.status = from_status
                self.issue_store.save(issue)
            else:
                # Legacy fallback
                stage = self._infer_blocked_stage(issue)
                if stage == "develop":
                    self._log(issue_id, "run 恢复: BLOCKED → 继续 develop")
                    issue.status = IssueStatus.APPROVED
                elif stage == "design":
                    self._log(issue_id, "run 恢复: BLOCKED → 重跑 design")
                    issue.status = IssueStatus.CREATED
                else:
                    self._log(issue_id, "run 停止: 无法推断 BLOCKED 阶段")
                    return
                self.issue_store.save(issue)
            issue = self.issue_store.get(issue_id)

        # Design phase
        if issue.status == IssueStatus.CREATED:
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
        if "开发" in issue.sections or "开发步骤" in issue.sections:
            return "develop"
        # Acceptance confirmed but develop not started yet — still develop stage
        if self._acceptance_script_path(issue.id).exists():
            return "develop"
        if "Design Review" in issue.sections:
            return "design"
        if "设计" in issue.sections:
            return "design"
        return None

    async def _block_issue(self, issue_id: int, task, reason: str,
                           from_status: IssueStatus | None = None,
                           event_reason: str = "") -> None:
        """Transition to BLOCKED with structured metadata."""
        issue = self.issue_store.get(issue_id)
        if from_status is None:
            from_status = issue.status
        issue.blocked_reason = reason
        issue.blocked_from = from_status
        issue.status = IssueStatus.BLOCKED
        self.issue_store.save(issue)
        if task:
            task.status = TaskStatus.FAILED
        if event_reason:
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                "issue_id": issue_id,
                "task_id": task.task_id if task else None,
                "reason": event_reason}))

    async def _on_resume(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status == IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is BLOCKED. Use `unblock` to continue or `approve` to accept current state."}))
            return
        # Resume from non-BLOCKED active states (e.g., process interrupted)
        action = self._infer_blocked_stage(issue)
        self._log(issue.id, f"人类介入: resume → 重跑 {action}")
        if action == "design":
            await self._on_design(msg)
        elif action == "develop":
            await self._on_develop(msg)
        else:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"cannot infer stage for issue #{issue.id}"}))

    async def _on_approve(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is not BLOCKED"}))
            return
        stage = self._infer_blocked_stage(issue)
        next_status = IssueStatus.DONE if stage == "develop" else IssueStatus.APPROVED
        # Clear blocked metadata
        issue.blocked_reason = None
        issue.blocked_from = None
        issue.status = next_status
        self.issue_store.save(issue)
        self._log(issue.id, f"人类介入: approve → {next_status.value}")
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": next_status.value}))

    async def _on_unblock(self, msg):
        """Unblock: restore pre-BLOCKED state and re-enter cycle."""
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} is not BLOCKED"}))
            return

        message = msg.payload.get("message", "")
        from_status = issue.blocked_from
        reason = issue.blocked_reason

        # Log the unblock
        if message:
            self._log(issue.id, f"人类介入: unblock ({reason}) — {message}")
        else:
            self._log(issue.id, f"人类介入: unblock ({reason})")

        # Clear blocked metadata and restore status
        issue.blocked_reason = None
        issue.blocked_from = None
        if from_status:
            issue.status = from_status
        else:
            # Fallback for legacy issues without blocked_from
            stage = self._infer_blocked_stage(issue)
            if stage == "develop":
                issue.status = IssueStatus.APPROVED
            elif stage == "design":
                issue.status = IssueStatus.CREATED
            else:
                await self.bus.publish(Message(MessageType.EVT_ERROR,
                    {"message": f"cannot infer stage for issue #{issue.id}"}))
                return
        self.issue_store.save(issue)

        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": issue.status.value}))

        # Auto-trigger the corresponding cycle
        # Re-read issue after save to get fresh state
        issue = self.issue_store.get(issue.id)
        stage = self._infer_blocked_stage(issue)
        if stage == "develop":
            # If acceptance script was the problem, delete it so it gets regenerated
            if reason == BLOCKED_ACCEPTANCE_BUG:
                acc_path = self._acceptance_script_path(issue.id)
                if acc_path.exists():
                    acc_path.unlink()
                    self._log(issue.id, "Acceptance script 已删除，将重新生成")
            develop_msg = Message(msg.type, {
                **msg.payload,
                "skip_first_develop": reason == BLOCKED_ACCEPTANCE_BUG,
            })
            await self._on_develop(develop_msg)
        elif stage == "design":
            await self._on_design(msg)

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
        info = {
            "id": issue.id, "title": issue.title,
            "status": issue.status.value, "priority": issue.priority,
            "tags": issue.tags, "assignee": issue.assignee,
            "sections": list(issue.sections.keys()),
        }
        if issue.blocked_reason:
            info["blocked_reason"] = issue.blocked_reason
        if issue.blocked_from:
            info["blocked_from"] = issue.blocked_from.value
        await self.bus.publish(Message(MessageType.EVT_ISSUE_INFO, {"issue": info}))

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
