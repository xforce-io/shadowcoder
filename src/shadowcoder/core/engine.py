from __future__ import annotations

import logging
from pathlib import Path

from shadowcoder.agents.types import AgentRequest, AgentActionFailed
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

    async def _run_all_reviewers(self, issue, task, action, review_section_key):
        reviewer_names = self.config.get_reviewers(action)
        all_passed = True
        failed_reviewers = []

        for rname in reviewer_names:
            reviewer = self.agents.get(rname)
            request = AgentRequest(action="review", issue=issue,
                context={"worktree_path": task.worktree_path})
            try:
                review = await self._review_with_retry(reviewer, request)
                self.issue_store.append_review(issue.id, review_section_key, review)
                await self.bus.publish(Message(MessageType.EVT_REVIEW_RESULT,
                    {"issue_id": issue.id, "reviewer": rname,
                     "passed": review.passed, "comments": len(review.comments)}))
                if not review.passed:
                    all_passed = False
            except Exception:
                failed_reviewers.append(rname)
                logger.warning("Reviewer %s unavailable after retries", rname)

        if len(failed_reviewers) == len(reviewer_names):
            raise RuntimeError(f"All reviewers unavailable: {failed_reviewers}")

        return all_passed

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
                request = AgentRequest(action=action, issue=issue,
                    context={"worktree_path": task.worktree_path})

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

                self.issue_store.update_section(issue.id, section_key, content)
                self._log(issue.id,
                    f"{action_label} R{round_num} Agent 产出{feat_summary}\n"
                    f"内容长度: {len(content)} 字符")

                self.issue_store.transition_status(issue.id, IssueStatus[review_stage.upper()])
                issue = self.issue_store.get(issue.id)

                all_passed = await self._run_all_reviewers(issue, task, action, review_section_key)

                if all_passed:
                    self.issue_store.transition_status(issue.id, success_status)
                    self._log(issue.id,
                        f"{action_label} Review R{round_num} — PASSED → {success_status.value}")
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return

                # Summarize review rejection
                review_content = self.issue_store.get(issue.id).sections.get(review_section_key, "")
                reject_lines = [l for l in review_content.split("\n") if "[HIGH]" in l]
                reject_summary = "; ".join(l.strip()[:80] for l in reject_lines[-5:])
                self._log(issue.id,
                    f"{action_label} Review R{round_num} — NOT PASSED\n"
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
                self.issue_store.update_section(issue.id, "测试", output.report)

                if output.success:
                    passed = output.passed_count if output.passed_count is not None else "?"
                    total = output.total_count if output.total_count is not None else "?"
                    self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                    self._log(issue.id,
                        f"Test R{attempt} — PASSED ({passed}/{total}) → DONE")
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return

                # --- Test failed: check recommendation ---
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
