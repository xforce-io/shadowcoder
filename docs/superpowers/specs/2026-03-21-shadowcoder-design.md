# ShadowCoder 系统设计

## 概述

ShadowCoder 是一个基于 Agent 的需求管理与开发系统。用户在目标仓库中创建 issue（需求），系统通过可插拔的 Agent（Claude Code、Codex 等）自动完成需求分析、设计、开发、测试的全流程，并在 design 和 develop 阶段引入 Agent reviewer 自动审查。

**核心定位**：日常开发的核心工具，每个 repo 的需求都走此流程。

## 架构

分层消息总线架构，三层职责分离：

```
CLI 层 (TUI / 未来 Skill)
    ↕ MessageBus (命令/事件)
Core 层 (Engine, IssueStore, TaskManager, WorktreeManager)
    ↕
Agent 层 (BaseAgent → ClaudeCode / Codex / ...)
```

- **CLI 层**：纯表现层，解析输入发命令，订阅事件做渲染
- **Core 层**：业务逻辑，状态机驱动 issue 生命周期
- **Agent 层**：可插拔的 Agent 实现，统一抽象接口

## 技术选型

- **语言**：Python
- **TUI**：Textual（天然消息传递架构，契合显示与逻辑分离）
- **配置**：PyYAML
- **Issue 文件**：python-frontmatter（Markdown + YAML 元数据）
- **异步**：asyncio（支持多 task 并发）

## 项目结构

```
shadowcoder/
├── docs/
├── src/shadowcoder/
│   ├── __init__.py
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── tui/
│   │   │   ├── app.py             # Textual App，订阅 bus 事件渲染
│   │   │   └── widgets.py         # 自定义组件
│   │   └── skill/                 # 未来 skill 接口
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # 全局配置加载
│   │   ├── bus.py                 # MessageBus
│   │   ├── engine.py              # Core Engine，编排生命周期
│   │   ├── models.py              # 数据模型
│   │   ├── issue_store.py         # Issue CRUD
│   │   ├── task_manager.py        # 运行时 Task 管理
│   │   └── worktree.py            # Git worktree 管理
│   └── agents/
│       ├── __init__.py
│       ├── base.py                # Agent 抽象基类
│       ├── claude_code.py         # Claude Code 实现
│       └── codex.py               # Codex 实现
├── tests/
├── pyproject.toml
└── README.md
```

## 数据模型

### Issue 生命周期状态

```
created → designing → design_review → approved → developing → dev_review → testing → done
                ↘          ↘                          ↘          ↘
               failed     blocked                   failed     blocked
                          (超过 max_review_rounds)              (超过 max_review_rounds)
```

- 每个 review 阶段可能回退到前一步（带 review 意见重新执行）
- review 循环超过 `max_review_rounds`（默认 3）则进入 `blocked` 状态，等待人类介入
- Agent 执行异常则进入 `failed` 状态
- 用户可主动取消，进入 `cancelled` 状态

### Issue Markdown 文件

存储位置：`<目标仓库>/.shadowcoder/issues/0001.md`

```markdown
---
id: 1
title: 支持用户登录功能
status: designing
priority: high
created: 2026-03-21T10:00:00
updated: 2026-03-21T14:30:00
tags: [auth, backend]
assignee: claude-code
---

## 需求分析
（Agent 输出的需求分析内容）

## 设计
（Agent 输出的设计方案）

## Design Review
（Reviewer Agent 的审查结果）

## 开发步骤
（Agent 拆解的开发计划和执行记录）

## Dev Review
（Reviewer Agent 的代码审查结果）

## 测试
（测试计划和执行结果）

## 建议
（Agent 给出的后续建议）
```

### 全局配置

位置：`~/.shadowcoder/config.yaml`

```yaml
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
    codex:
      type: codex

reviewers:
  design: [claude-code]
  develop: [claude-code]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 3              # 超过则 issue 进入 blocked 状态

logging:
  dir: ~/.shadowcoder/logs
  level: INFO

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

### Python 数据模型（`core/models.py`）

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

class IssueStatus(Enum):
    CREATED = "created"
    DESIGNING = "designing"
    DESIGN_REVIEW = "design_review"
    APPROVED = "approved"
    DEVELOPING = "developing"
    DEV_REVIEW = "dev_review"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"         # Agent 执行异常
    BLOCKED = "blocked"       # review 循环超限，等待人类介入
    CANCELLED = "cancelled"   # 用户主动取消

class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass
class ReviewComment:
    severity: Severity
    message: str
    location: str | None = None

@dataclass
class ReviewResult:
    passed: bool
    comments: list[ReviewComment]
    reviewer: str

@dataclass
class Issue:
    id: int
    title: str
    status: IssueStatus
    priority: str
    created: datetime
    updated: datetime
    tags: list[str] = field(default_factory=list)
    assignee: str | None = None
    sections: dict[str, str] = field(default_factory=dict)

@dataclass
class Task:
    """运行时概念，一个 task = 一个 issue 的一次阶段执行"""
    task_id: str
    issue_id: int
    repo_path: str
    action: str          # design / develop / test
    agent_name: str
    worktree_path: str | None = None
    status: "TaskStatus" = None  # 见下方 TaskStatus

    def __post_init__(self):
        if self.status is None:
            self.status = TaskStatus.RUNNING

class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

## Agent 抽象

```python
# agents/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class AgentRequest:
    action: str                    # analyze / design / develop / test / review
    issue: Issue
    context: dict                  # 工作目录、相关文件等
    prompt_override: str | None = None

@dataclass
class AgentResponse:
    content: str                   # Agent 输出的主要内容（markdown）
    success: bool
    metadata: dict | None = None   # 额外信息（token 用量、耗时等）

class AgentStream:
    """Agent 流式输出的异步迭代器"""
    async def __aiter__(self): ...
    async def __anext__(self) -> str: ...

class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def execute(self, request: AgentRequest) -> AgentResponse:
        """执行完整请求，返回最终结果"""
        ...

    @abstractmethod
    async def stream(self, request: AgentRequest) -> AgentStream:
        """流式执行，用于 TUI 实时展示"""
        ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewResult:
        """Review 专用，返回结构化审查结果"""
        ...
```

## MessageBus

```python
# core/bus.py
from dataclasses import dataclass
from enum import Enum

class MessageType(Enum):
    # 命令（CLI → Engine）
    CMD_CREATE_ISSUE = "cmd.create_issue"
    CMD_DESIGN = "cmd.design"
    CMD_DEVELOP = "cmd.develop"
    CMD_TEST = "cmd.test"
    CMD_LIST = "cmd.list"
    CMD_INFO = "cmd.info"
    CMD_RESUME = "cmd.resume"
    CMD_APPROVE = "cmd.approve"
    CMD_CANCEL = "cmd.cancel"

    # 事件（Engine → CLI）
    EVT_ISSUE_CREATED = "evt.issue_created"
    EVT_STATUS_CHANGED = "evt.status_changed"
    EVT_AGENT_OUTPUT = "evt.agent_output"
    EVT_REVIEW_RESULT = "evt.review_result"
    EVT_TASK_STARTED = "evt.task_started"
    EVT_TASK_COMPLETED = "evt.task_completed"
    EVT_TASK_FAILED = "evt.task_failed"
    EVT_ERROR = "evt.error"

@dataclass
class Message:
    type: MessageType
    payload: dict
    task_id: str | None = None

class MessageBus:
    def __init__(self):
        self._handlers: dict[MessageType, list] = {}

    def subscribe(self, msg_type: MessageType, handler):
        self._handlers.setdefault(msg_type, []).append(handler)

    async def publish(self, message: Message):
        for handler in self._handlers.get(message.type, []):
            try:
                await handler(message)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Handler failed for %s", message.type
                )
```

## Engine 状态机

### 多 repo 策略

每个 Engine 实例绑定一个 repo。多 repo 场景由上层（TUI/Skill）管理多个 Engine 实例。

### Agent 注册

Engine 通过 `AgentRegistry` 查找 Agent，Registry 根据配置文件实例化对应的 Agent 类：

```python
# agents/registry.py
class AgentRegistry:
    """根据配置实例化并缓存 Agent"""
    _agent_classes: dict[str, type] = {}  # type name → class，通过 register() 注册

    def __init__(self, config: Config):
        self.config = config
        self._instances: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, type_name: str, agent_class: type):
        cls._agent_classes[type_name] = agent_class

    def get(self, name: str) -> BaseAgent:
        if name == "default":
            name = self.config.get_default_agent()
        if name not in self._instances:
            agent_conf = self.config.get_agent_config(name)
            cls = self._agent_classes[agent_conf["type"]]
            self._instances[name] = cls(agent_conf)
        return self._instances[name]
```

### IssueStore 业务接口

IssueStore 封装所有 issue 文件操作，外部不直接修改 Issue 字段后调 save：

```python
# core/issue_store.py
class IssueStore:
    def __init__(self, repo_path: str, config: Config): ...

    # --- CRUD ---
    def create(self, title, priority="medium", tags=None) -> Issue: ...
    def get(self, issue_id: int) -> Issue: ...
    def list_all(self) -> list[Issue]: ...
    def list_by_status(self, status: IssueStatus) -> list[Issue]: ...
    def list_by_tag(self, tag: str) -> list[Issue]: ...

    # --- 状态流转 ---
    def transition_status(self, issue_id: int, new_status: IssueStatus):
        """校验状态转换合法性后更新。非法转换抛 InvalidTransitionError"""
        ...

    # --- 内容操作 ---
    def update_section(self, issue_id: int, section: str, content: str):
        """覆盖写入指定 section 的内容"""
        ...

    def append_review(self, issue_id: int, section: str, review: ReviewResult):
        """将 ReviewResult 格式化后写入 review section"""
        ...

    def assign(self, issue_id: int, agent_name: str): ...
```

### Config 业务接口

Config 封装配置访问，提供类型安全的方法，外部不直接读 dict：

```python
# core/config.py
class Config:
    def __init__(self, path: str = "~/.shadowcoder/config.yaml"): ...

    def get_default_agent(self) -> str: ...
    def get_agent_config(self, name: str) -> dict: ...
    def get_available_agents(self) -> list[str]: ...
    def get_reviewers(self, stage: str) -> list[str]: ...
    def get_max_review_rounds(self) -> int: ...
    def get_issue_dir(self) -> str: ...
    def get_worktree_dir(self) -> str: ...
    def get_log_dir(self) -> str: ...
    def get_log_level(self) -> str: ...
```

### Review 流程详细逻辑

#### 正常流程（review 循环）

design 和 develop 共用 `_run_with_review` 循环，流程如下：

```
for round in 1..max_review_rounds:
    1. Agent 执行（design/develop）
       - 产出内容 **覆盖** 上一轮的 section（不保留历史版本）
       - 如果 Agent 返回 success=false → 直接 FAILED，不进入 review
       - 如果 Agent 抛异常 → FAILED（见异常处理）

    2. 所有 reviewers 依次 review（配置中可有多个）
       - **所有 reviewer 都通过**才算通过
       - 任一 reviewer 发现 critical/high → 本轮未通过
       - medium/low 问题记录在 review section，不阻塞通过
       - Reviewer 自身崩溃（抛异常）→ **不消耗轮次**，重试该 reviewer
         - reviewer 连续失败 3 次 → 该 reviewer 标记为不可用，跳过
         - 如果所有 reviewer 都不可用 → FAILED

    3. 通过 → 进入下一状态（APPROVED / TESTING）
    4. 未通过 → review comments 作为 context 喂给 agent，下一轮重试

超过 max_review_rounds → BLOCKED（等待人类介入）
```

#### 异常处理分类

| 场景 | 结果状态 | task 状态 | 是否可重试 |
|------|----------|-----------|-----------|
| Agent 执行抛异常 | FAILED | FAILED | 是，用户发 `design #id` 重跑 |
| Agent 返回 success=false | FAILED | FAILED | 是，同上 |
| Reviewer 抛异常 | 不变（停留当前状态） | 不变 | 自动重试该 reviewer，不消耗轮次 |
| Reviewer 连续失败 3 次 | 跳过该 reviewer | 不变 | 继续其他 reviewer |
| 所有 reviewer 不可用 | FAILED | FAILED | 是，用户重跑 |
| Review 轮次耗尽 | BLOCKED | FAILED | 等人类介入后 `resume #id` |
| 用户取消 | CANCELLED | CANCELLED | 是，用户重新发命令 |

#### BLOCKED 状态的人类介入

issue 进入 BLOCKED 后，人类可以：
- `resume #id` —— 重置为上一个执行状态（DESIGNING/DEVELOPING），继续 review 循环（轮次计数重置）
- `approve #id` —— 手动批准，跳过 review 直接进入下一状态
- `cancel #id` —— 放弃

#### FAILED 状态的重试

issue 进入 FAILED 后，用户直接重新发对应命令（`design #id` / `develop #id` / `test #id`），系统从该阶段重头开始。已产出的 sections 内容保留（新一轮会覆盖）。

#### 取消

用户发 `cancel #id`：
- task 标记为 CANCELLED
- issue 标记为 CANCELLED
- worktree **保留**，不自动清理（用户可能要检查中间产物）
- 已写入的 sections 内容保留

### Engine 实现

```python
# core/engine.py
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

    async def _review_with_retry(self, reviewer, request, max_retries=3) -> ReviewResult:
        """单个 reviewer 执行 review，自身崩溃时重试，不消耗 review 轮次"""
        for attempt in range(1, max_retries + 1):
            try:
                return await reviewer.review(request)
            except Exception:
                if attempt == max_retries:
                    raise  # 连续失败，向上传播
                await self.bus.publish(Message(MessageType.EVT_ERROR,
                    {"message": f"reviewer 失败，重试 {attempt}/{max_retries}"}))

    async def _run_all_reviewers(self, issue, task, action, review_section_key):
        """所有 reviewer 依次 review，全部通过才算通过。
        返回 (all_passed: bool, failed_reviewers: list[str])"""
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
                # reviewer 连续失败 3 次，标记为不可用，跳过
                failed_reviewers.append(rname)

        if len(failed_reviewers) == len(reviewer_names):
            raise RuntimeError(f"所有 reviewer 不可用: {failed_reviewers}")

        return all_passed

    async def _run_with_review(self, issue, task, action, review_stage,
                                success_status, section_key, review_section_key):
        """通用的 执行→review→重试 循环，design 和 develop 共用"""
        max_rounds = self.config.get_max_review_rounds()
        try:
            for round_num in range(1, max_rounds + 1):
                # --- 执行阶段 ---
                self.issue_store.transition_status(issue.id, IssueStatus[action.upper() + "ING"])
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

                agent = self.agents.get(issue.assignee or "default")
                request = AgentRequest(action=action, issue=issue,
                    context={"worktree_path": task.worktree_path})
                response = await agent.execute(request)

                # Agent 返回 success=false → 直接 FAILED
                if not response.success:
                    self.issue_store.update_section(issue.id, section_key, response.content)
                    self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                        {"issue_id": issue.id, "task_id": task.task_id,
                         "reason": "agent 报告执行失败"}))
                    return

                # 覆盖写入本轮产出
                self.issue_store.update_section(issue.id, section_key, response.content)

                # --- Review 阶段 ---
                self.issue_store.transition_status(issue.id, IssueStatus[review_stage.upper()])
                all_passed = await self._run_all_reviewers(
                    issue, task, action, review_section_key)

                if all_passed:
                    self.issue_store.transition_status(issue.id, success_status)
                    task.status = TaskStatus.COMPLETED
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return
                # 未通过：review comments 已在 issue 中，下一轮 agent 可读取

            # --- 轮次耗尽 → BLOCKED ---
            self.issue_store.transition_status(issue.id, IssueStatus.BLOCKED)
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id,
                 "reason": f"review 未通过，已重试 {max_rounds} 轮，等待人类介入"}))

        except Exception as e:
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))

    async def _on_design(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(issue, repo_path=self.repo_path, action="design",
            agent_name=issue.assignee or "default")
        await self._run_with_review(
            issue, task, action="design", review_stage="design_review",
            success_status=IssueStatus.APPROVED,
            section_key="设计", review_section_key="Design Review")

    async def _on_develop(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(issue, repo_path=self.repo_path, action="develop",
            agent_name=issue.assignee or "default")
        await self._run_with_review(
            issue, task, action="develop", review_stage="dev_review",
            success_status=IssueStatus.TESTING,
            section_key="开发步骤", review_section_key="Dev Review")

    async def _on_test(self, msg: Message):
        """测试阶段：执行测试，无 review 循环"""
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = await self.task_manager.create(issue, repo_path=self.repo_path, action="test",
            agent_name=issue.assignee or "default")
        try:
            self.issue_store.transition_status(issue.id, IssueStatus.TESTING)
            agent = self.agents.get(issue.assignee or "default")
            response = await agent.execute(AgentRequest(
                action="test", issue=issue,
                context={"worktree_path": task.worktree_path}))
            self.issue_store.update_section(issue.id, "测试", response.content)

            if response.success:
                self.issue_store.transition_status(issue.id, IssueStatus.DONE)
                task.status = TaskStatus.COMPLETED
                await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                    {"issue_id": issue.id, "task_id": task.task_id}))
            else:
                self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
                task.status = TaskStatus.FAILED
                await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                    {"issue_id": issue.id, "task_id": task.task_id,
                     "reason": "测试未通过"}))

        except Exception as e:
            self.issue_store.transition_status(issue.id, IssueStatus.FAILED)
            task.status = TaskStatus.FAILED
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))

    async def _on_resume(self, msg: Message):
        """人类介入后恢复 BLOCKED 的 issue，重置轮次重跑"""
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} 不在 BLOCKED 状态，无法 resume"}))
            return
        # 根据上一次阶段决定重跑 design 还是 develop
        action = msg.payload.get("action")  # 由 TUI 根据 issue 历史推断
        if action == "design":
            await self._on_design(msg)
        elif action == "develop":
            await self._on_develop(msg)

    async def _on_approve(self, msg: Message):
        """人类手动批准 BLOCKED 的 issue，跳过 review"""
        issue = self.issue_store.get(msg.payload["issue_id"])
        if issue.status != IssueStatus.BLOCKED:
            await self.bus.publish(Message(MessageType.EVT_ERROR,
                {"message": f"issue #{issue.id} 不在 BLOCKED 状态，无法 approve"}))
            return
        # BLOCKED 在 design_review 阶段 → APPROVED; 在 dev_review 阶段 → TESTING
        next_status = msg.payload.get("next_status", IssueStatus.APPROVED)
        self.issue_store.transition_status(issue.id, next_status)
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": next_status.value}))

    async def _on_cancel(self, msg: Message):
        """用户取消。worktree 保留，sections 内容保留"""
        issue = self.issue_store.get(msg.payload["issue_id"])
        self.issue_store.transition_status(issue.id, IssueStatus.CANCELLED)
        # 取消关联的运行中 task
        for task in self.task_manager.list_active():
            if task.issue_id == issue.id:
                await self.task_manager.cancel(task.task_id)
        await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
            {"issue_id": issue.id, "status": "cancelled"}))
```

## TaskManager

```python
# core/task_manager.py
import asyncio
import uuid

class TaskManager:
    def __init__(self, worktree_manager):
        self.tasks: dict[str, Task] = {}
        self.worktree_manager = worktree_manager
        self._running: dict[str, asyncio.Task] = {}

    async def create(self, issue, repo_path, action, agent_name) -> Task:
        task_id = str(uuid.uuid4())[:8]
        worktree_path = await self.worktree_manager.create(repo_path, issue.id)
        task = Task(
            task_id=task_id, issue_id=issue.id, repo_path=repo_path,
            action=action, agent_name=agent_name, worktree_path=worktree_path,
        )
        self.tasks[task_id] = task
        return task

    def launch(self, task_id, coro) -> asyncio.Task:
        atask = asyncio.create_task(coro)
        self._running[task_id] = atask
        return atask

    def list_active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]

    async def cancel(self, task_id):
        if task_id in self._running:
            self._running[task_id].cancel()
            self.tasks[task_id].status = TaskStatus.CANCELLED
```

## WorktreeManager

```python
# core/worktree.py
import asyncio
from pathlib import Path

class WorktreeManager:
    def __init__(self, base_dir=".shadowcoder/worktrees"):
        self.base_dir = base_dir

    async def _run_git(self, repo_path: str, *args) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {stderr.decode()}")
        return stdout.decode()

    async def create(self, repo_path, issue_id) -> str:
        branch = f"shadowcoder/issue-{issue_id}"
        wt_path = str(Path(repo_path) / self.base_dir / f"issue-{issue_id}")
        await self._run_git(repo_path, "worktree", "add", "-b", branch, wt_path)
        return wt_path

    async def remove(self, repo_path, issue_id):
        wt_path = str(Path(repo_path) / self.base_dir / f"issue-{issue_id}")
        await self._run_git(repo_path, "worktree", "remove", wt_path)

    async def list(self, repo_path) -> list[str]:
        output = await self._run_git(repo_path, "worktree", "list", "--porcelain")
        return [
            l.split()[1] for l in output.splitlines()
            if l.startswith("worktree")
        ]
```

> 注：worktree 创建在 `.shadowcoder/worktrees/` 下，该目录需加入目标仓库的 `.gitignore`。IssueStore 初始化时应自动确保 `.shadowcoder/worktrees` 在 `.gitignore` 中。

## IssueStore

```python
# core/issue_store.py
from pathlib import Path
import frontmatter

class IssueStore:
    def __init__(self, repo_path, issues_dir=".shadowcoder/issues"):
        self.base = Path(repo_path) / issues_dir

    def next_id(self) -> int:
        existing = list(self.base.glob("*.md"))
        if not existing:
            return 1
        return max(int(f.stem) for f in existing) + 1

    def create(self, title, priority="medium", tags=None) -> Issue:
        issue = Issue(
            id=self.next_id(), title=title,
            status=IssueStatus.CREATED, priority=priority,
            created=datetime.now(), updated=datetime.now(),
            tags=tags or [],
        )
        self.save(issue)
        return issue

    def save(self, issue):
        self.base.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(
            content=self._sections_to_markdown(issue.sections),
            id=issue.id, title=issue.title,
            status=issue.status.value, priority=issue.priority,
            created=issue.created.isoformat(),
            updated=datetime.now().isoformat(),
            tags=issue.tags, assignee=issue.assignee,
        )
        path = self.base / f"{issue.id:04d}.md"
        path.write_text(frontmatter.dumps(post), encoding="utf-8")

    def get(self, issue_id) -> Issue:
        path = self.base / f"{issue_id:04d}.md"
        post = frontmatter.load(str(path))
        return Issue(
            id=post["id"], title=post["title"],
            status=IssueStatus(post["status"]),
            priority=post["priority"],
            created=datetime.fromisoformat(post["created"]),
            updated=datetime.fromisoformat(post["updated"]),
            tags=post.get("tags", []),
            assignee=post.get("assignee"),
            sections=self._markdown_to_sections(post.content),
        )

    def list_all(self) -> list[Issue]:
        return [self.get(int(f.stem)) for f in sorted(self.base.glob("*.md"))]

    def _sections_to_markdown(self, sections):
        return "\n\n".join(f"## {k}\n{v}" for k, v in sections.items())

    def _markdown_to_sections(self, content):
        sections, current_key, lines = {}, None, []
        for line in content.split("\n"):
            if line.startswith("## "):
                if current_key:
                    sections[current_key] = "\n".join(lines).strip()
                current_key = line[3:].strip()
                lines = []
            else:
                lines.append(line)
        if current_key:
            sections[current_key] = "\n".join(lines).strip()
        return sections
```

## TUI

```python
# cli/tui/app.py
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Input

class ShadowCoderApp(App):
    def __init__(self, bus, **kwargs):
        super().__init__(**kwargs)
        self.bus = bus

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="output", wrap=True)
        yield Input(placeholder="命令: create / list / design #id / develop #id / test #id")
        yield Footer()

    async def on_mount(self):
        self.bus.subscribe(MessageType.EVT_AGENT_OUTPUT, self._on_agent_output)
        self.bus.subscribe(MessageType.EVT_STATUS_CHANGED, self._on_status_changed)
        self.bus.subscribe(MessageType.EVT_REVIEW_RESULT, self._on_review_result)
        self.bus.subscribe(MessageType.EVT_TASK_COMPLETED, self._on_task_completed)
        self.bus.subscribe(MessageType.EVT_TASK_FAILED, self._on_task_failed)
        self.bus.subscribe(MessageType.EVT_ERROR, self._on_error)

    async def on_input_submitted(self, event):
        cmd = event.value.strip()
        event.input.clear()
        msg = self._parse_command(cmd)
        if msg:
            await self.bus.publish(msg)

    async def _on_agent_output(self, msg):
        self.query_one("#output", RichLog).write(msg.payload["chunk"])

    def _parse_command(self, cmd) -> Message | None:
        parts = cmd.split()
        match parts:
            case ["create", *title_parts]:
                return Message(MessageType.CMD_CREATE_ISSUE, {"title": " ".join(title_parts)})
            case ["list"]:
                return Message(MessageType.CMD_LIST, {})
            case ["info", ref]:
                return Message(MessageType.CMD_INFO, {"issue_id": int(ref.lstrip("#"))})
            case ["design", ref]:
                return Message(MessageType.CMD_DESIGN, {"issue_id": int(ref.lstrip("#"))})
            case ["develop", ref]:
                return Message(MessageType.CMD_DEVELOP, {"issue_id": int(ref.lstrip("#"))})
            case ["test", ref]:
                return Message(MessageType.CMD_TEST, {"issue_id": int(ref.lstrip("#"))})
            case ["resume", ref]:
                return Message(MessageType.CMD_RESUME, {"issue_id": int(ref.lstrip("#"))})
            case ["approve", ref]:
                return Message(MessageType.CMD_APPROVE, {"issue_id": int(ref.lstrip("#"))})
            case ["cancel", ref]:
                return Message(MessageType.CMD_CANCEL, {"issue_id": int(ref.lstrip("#"))})
            case _:
                # 未知命令直接在 TUI 本地处理，不走 bus
                self.query_one("#output", RichLog).write(f"[red]未知命令: {cmd}[/red]")
                return None
```

### 入口（`pyproject.toml`）

```toml
[project.scripts]
shadowcoder = "shadowcoder.cli.tui.app:main"
```

## 关键设计决策

1. **消息总线解耦**：TUI/Skill 只通过 bus 与 Engine 通信，表现层可整体替换
2. **Agent 可插拔**：统一 BaseAgent 接口，新增 Agent 类型只需实现接口并注册
3. **Review 全自动**：Reviewer 是 Agent，循环直到无 critical/high 问题；人类在顶层流程以"被汇报"身份参与
4. **Worktree 隔离**：同 repo 多 issue 并发时自动创建独立 worktree 和分支
5. **Issue 即文件**：带 frontmatter 的 Markdown 存在目标仓库 `.shadowcoder/issues/`，可 git 追踪
6. **全局配置先行**：`~/.shadowcoder/config.yaml`，未来可扩展项目级覆盖

## 未来扩展点

- 项目级配置（`.shadowcoder/config.yaml`）覆盖全局配置
- Issue 间引用和依赖关系
- 更多 Agent 实现（Dolphin、LangChain 等）
- Skill 接口替代 TUI
- 自迭代：shadowcoder 管理自身需求
