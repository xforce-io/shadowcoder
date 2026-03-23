from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from shadowcoder.agents.types import AgentRequest, AgentActionFailed, AgentUsage, TestOutput
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
        self.bus.subscribe(MessageType.CMD_TEST, self._on_test)
        self.bus.subscribe(MessageType.CMD_RESUME, self._on_resume)
        self.bus.subscribe(MessageType.CMD_APPROVE, self._on_approve)
        self.bus.subscribe(MessageType.CMD_CANCEL, self._on_cancel)
        self.bus.subscribe(MessageType.CMD_LIST, self._on_list)
        self.bus.subscribe(MessageType.CMD_INFO, self._on_info)
        self.bus.subscribe(MessageType.CMD_CLEANUP, self._on_cleanup)

    def _track_usage(self, issue_id: int, usage: AgentUsage | None):
        """Accumulate usage for an issue."""
        if usage is None:
            return
        self._usage_by_issue.setdefault(issue_id, []).append(usage)

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
        """Format usage summary for logging."""
        usages = self._usage_by_issue.get(issue_id, [])
        if not usages:
            return "No usage data"
        input_t, output_t = self._total_tokens(issue_id)
        cost = self._total_cost(issue_id)
        total_duration = sum(u.duration_ms for u in usages) / 1000
        return (f"Calls: {len(usages)} | "
                f"Tokens: {input_t:,} in + {output_t:,} out | "
                f"Cost: ${cost:.4f} | "
                f"Time: {total_duration:.1f}s")

    async def _verify_tests(self, worktree_path: str) -> tuple[bool, str]:
        """Independently run the configured test command and check exit code.
        Returns (passed, output). This overrides agent's self-report."""
        test_cmd = self.config.get_test_command()
        if not test_cmd or not worktree_path:
            return True, ""  # no command configured, trust agent

        proc = await asyncio.create_subprocess_shell(
            test_cmd,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        passed = proc.returncode == 0
        return passed, output

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

    def _update_feedback(self, issue_id: int, review, current_round: int):
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

        # Accumulate proposed tests
        tests = fb.get("proposed_tests", [])
        for tc in review.proposed_tests:
            if not any(t["name"] == tc.name for t in tests):
                tests.append({
                    "name": tc.name,
                    "description": tc.description,
                    "expected_behavior": tc.expected_behavior,
                    "category": tc.category,
                    "round_proposed": current_round,
                })

        fb["items"] = items
        fb["proposed_tests"] = tests
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
        """Format accumulated acceptance tests for developer context."""
        fb = self.issue_store.load_feedback(issue_id)
        tests = fb.get("proposed_tests", [])
        if not tests:
            return ""
        lines = ["Acceptance tests to implement (from reviewer):"]
        for tc in tests:
            lines.append(f"  - {tc['name']}: {tc['description']} → {tc['expected_behavior']}")
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

    async def _run_all_reviewers(self, issue, task, action, review_section_key):
        reviewer_names = self.config.get_reviewers(action)
        min_score = 100
        failed_reviewers = []
        last_review = None

        for rname in reviewer_names:
            reviewer = self.agents.get(rname)
            request = AgentRequest(action="review", issue=issue,
                context={
                    "worktree_path": task.worktree_path,
                    "latest_review": self._get_latest_review(issue.id, review_section_key),
                    "unresolved_feedback": self._format_unresolved_for_reviewer(issue.id),
                })
            try:
                review = await self._review_with_retry(reviewer, request)
                self._track_usage(issue.id, review.usage)
                self.issue_store.append_review(issue.id, review_section_key, review)
                min_score = min(min_score, review.score)
                last_review = review
                await self.bus.publish(Message(MessageType.EVT_REVIEW_RESULT, {
                    "issue_id": issue.id, "reviewer": rname,
                    "passed": review.score >= 70, "score": review.score,
                    "comments": len(review.comments)}))
            except Exception:
                failed_reviewers.append(rname)
                logger.warning("Reviewer %s unavailable after retries", rname)

        if len(failed_reviewers) == len(reviewer_names):
            raise RuntimeError(f"All reviewers unavailable: {failed_reviewers}")

        return min_score, last_review

    async def _run_with_review(self, issue, task, action, review_stage,
                                success_status, section_key, review_section_key):
        max_rounds = self.config.get_max_review_rounds()
        action_label = action.capitalize()
        try:
            for round_num in range(1, max_rounds + 1):
                target_status = IssueStatus[action.upper() + "ING"]
                issue = self.issue_store.get(issue.id)
                if issue.status != target_status:
                    self.issue_store.transition_status(issue.id, target_status)
                issue = self.issue_store.get(issue.id)
                self._log(issue.id, f"{action_label} R{round_num} 开始")
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

                agent = self.agents.get(issue.assignee or "default")
                # Include latest full review from log file so agent sees
                # specific feedback, not just the summary in .md
                latest_review = self._get_latest_review(issue.id, review_section_key)
                request = AgentRequest(action=action, issue=issue,
                    context={
                        "worktree_path": task.worktree_path,
                        "latest_review": latest_review,
                        "feedback_summary": self._format_feedback_for_agent(issue.id),
                        "acceptance_tests": self._format_acceptance_tests_for_developer(issue.id) if action == "develop" else "",
                    })

                if action == "design":
                    output = await agent.design(request)
                    content = output.document
                    feat_summary = ""
                elif action == "develop":
                    output = await agent.develop(request)
                    content = output.summary
                    files = output.files_changed
                    feat_summary = f" (files: {', '.join(files)})" if files else ""
                else:
                    raise ValueError(f"Unknown action for _run_with_review: {action}")

                self._track_usage(issue.id, output.usage)
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

                # Archive version before overwriting
                vfile = self.issue_store.save_version(
                    issue.id, action, round_num, content)
                self.issue_store.update_section(issue.id, section_key, content)
                self._log(issue.id,
                    f"{action_label} R{round_num} Agent 产出{feat_summary}\n"
                    f"内容长度: {len(content)} 字符, 存档: {vfile}")

                self.issue_store.transition_status(issue.id, IssueStatus[review_stage.upper()])
                issue = self.issue_store.get(issue.id)

                min_score, last_review = await self._run_all_reviewers(issue, task, action, review_section_key)
                if last_review:
                    self._update_feedback(issue.id, last_review, round_num)

                if min_score >= 90:
                    self.issue_store.transition_status(issue.id, success_status)
                    self._log(issue.id,
                        f"{action_label} Review R{round_num} — PASSED (score={min_score}) → {success_status.value}")
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return
                elif min_score >= 70:
                    # Conditional pass — log deferred issues, proceed
                    self._log(issue.id,
                        f"{action_label} Review R{round_num} — 带条件通过 (score={min_score}) → {success_status.value}")
                    self.issue_store.transition_status(issue.id, success_status)
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return

                # Fail — summarize review rejection
                review_content = self.issue_store.get(issue.id).sections.get(review_section_key, "")
                reject_lines = [l for l in review_content.split("\n") if "[HIGH]" in l]
                reject_summary = "; ".join(l.strip()[:80] for l in reject_lines[-5:])
                self._log(issue.id,
                    f"{action_label} Review R{round_num} — NOT PASSED (score={min_score})\n"
                    f"HIGH issues: {reject_summary or '(see review section)'}")

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
            agent = self.agents.get(issue.assignee or "default")
            request = AgentRequest(action="preflight", issue=issue,
                context={"worktree_path": None})
            try:
                pf = await agent.preflight(request)
                self._track_usage(issue.id, pf.usage)
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

        # Continue with normal design flow
        task = await self.task_manager.create(issue, repo_path=self.repo_path,
            action="design", agent_name=issue.assignee or "default")
        await self._run_with_review(issue, task, action="design",
            review_stage="design_review", success_status=IssueStatus.APPROVED,
            section_key="设计", review_section_key="Design Review")

    async def _on_develop(self, msg):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(issue, repo_path=self.repo_path,
            action="develop", agent_name=issue.assignee or "default")
        await self._run_with_review(issue, task, action="develop",
            review_stage="dev_review", success_status=IssueStatus.TESTING,
            section_key="开发步骤", review_section_key="Dev Review")

    async def _on_test(self, msg):
        """Test with auto-retry: on failure, read recommendation from agent
        response metadata and route to develop/design automatically.
        Retries up to max_test_retries before going BLOCKED."""
        issue_id = msg.payload["issue_id"]
        max_retries = self.config.get_max_test_retries()

        for attempt in range(1, max_retries + 1):
            issue = self.issue_store.get(issue_id)
            task = await self.task_manager.create(issue, repo_path=self.repo_path,
                action="test", agent_name=issue.assignee or "default")
            try:
                if issue.status != IssueStatus.TESTING:
                    self.issue_store.transition_status(issue.id, IssueStatus.TESTING)
                issue = self.issue_store.get(issue.id)

                agent = self.agents.get(issue.assignee or "default")
                request = AgentRequest(action="test", issue=issue,
                    context={"worktree_path": task.worktree_path})
                output = await agent.test(request)
                self._track_usage(issue.id, output.usage)
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
                self.issue_store.update_section(issue.id, "测试", output.report)

                if output.success:
                    # Independent verification: run test command if configured
                    verified, verify_output = await self._verify_tests(task.worktree_path)
                    if not verified:
                        # Agent said PASS but tests actually failed — override
                        self._log(issue.id,
                            f"Test R{attempt} — Agent 报告 PASSED，但独立验证 FAILED")
                        self.issue_store.update_section(issue.id, "测试",
                            output.report + f"\n\n## 独立验证 FAILED\n```\n{verify_output[-2000:]}\n```")
                        output = TestOutput(
                            report=output.report, success=False,
                            recommendation="develop",
                            passed_count=output.passed_count,
                            total_count=output.total_count,
                            usage=output.usage)
                        # Fall through to failure handling below
                    else:
                        passed = output.passed_count if output.passed_count is not None else "?"
                        total = output.total_count if output.total_count is not None else "?"
                        verify_note = ", 独立验证通过" if verify_output else ""
                        self._log(issue.id,
                            f"Test R{attempt} — PASSED ({passed}/{total}){verify_note} → DONE")
                        self._log(issue.id, f"=== 总计 ===\n{self._usage_summary(issue.id)}")
                        self._log(issue.id, "提示: 可以用 `merge #id` 合并分支，或 `cleanup #id` 清理 worktree")
                        self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                        task.status = TaskStatus.COMPLETED
                        await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                            {"issue_id": issue.id, "task_id": task.task_id}))
                        return

                # --- Test failed (or verification override): check recommendation ---
                recommendation = output.recommendation
                passed = output.passed_count if output.passed_count is not None else "?"
                total = output.total_count if output.total_count is not None else "?"
                self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                self._log(issue.id,
                    f"Test R{attempt} — FAILED ({passed}/{total})\n"
                    f"Recommendation: {recommendation or 'none'}")
                task.status = TaskStatus.FAILED
                await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
                    "issue_id": issue.id, "task_id": task.task_id,
                    "reason": "tests failed",
                    "recommendation": recommendation,
                    "attempt": attempt,
                }))

                if not recommendation:
                    self._log(issue.id, "无 recommendation，等待人类介入")
                    return

                if attempt >= max_retries:
                    break  # fall through to BLOCKED

                # Route to recommended stage
                self._log(issue.id,
                    f"自动路由到 {recommendation} (test attempt {attempt}/{max_retries})")
                route_msg = Message(msg.type, {"issue_id": issue.id})
                if recommendation == "develop":
                    await self._on_develop(route_msg)
                elif recommendation == "design":
                    await self._on_design(route_msg)
                    issue = self.issue_store.get(issue_id)
                    if issue.status == IssueStatus.APPROVED:
                        await self._on_develop(route_msg)
                else:
                    return  # unknown recommendation, let human decide

                # Check if the routed stage succeeded (issue should be at TESTING)
                issue = self.issue_store.get(issue_id)
                if issue.status != IssueStatus.TESTING:
                    # develop/design didn't complete successfully, stop
                    return

                # Loop: re-test

            except AgentActionFailed as e:
                self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                task.status = TaskStatus.FAILED
                await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                    {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
                return
            except Exception as e:
                self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                task.status = TaskStatus.FAILED
                await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                    {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
                return

        # Retries exhausted → BLOCKED
        issue = self.issue_store.get(issue_id)
        if issue.status == IssueStatus.FAILED:
            self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
        self._log(issue_id,
            f"Test 重试耗尽 ({max_retries} 轮) → BLOCKED，等待人类介入")
        await self.bus.publish(Message(MessageType.EVT_TASK_FAILED, {
            "issue_id": issue_id, "reason":
            f"test failed after {max_retries} retries, awaiting human intervention",
        }))

    def _infer_blocked_stage(self, issue):
        if "Dev Review" in issue.sections:
            return "develop"
        if "Design Review" in issue.sections:
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
        next_status = IssueStatus.TESTING if stage == "develop" else IssueStatus.APPROVED
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
