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
    status: str = "running"  # running / completed / failed / cancelled

class TaskStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

> 注：Task.status 将使用 TaskStatus 枚举替代裸字符串。

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

Engine 驱动 issue 的生命周期流转，核心是 design/develop 阶段的 review 循环。

**多 repo 策略**：每个 Engine 实例绑定一个 repo。多 repo 场景由上层（TUI/Skill）管理多个 Engine 实例。

**Agent 注册**：Engine 通过 `AgentRegistry` 查找 Agent，Registry 根据配置文件实例化对应的 Agent 类：

```python
# agents/registry.py
class AgentRegistry:
    """根据配置实例化并缓存 Agent"""
    _agent_classes: dict[str, type] = {}  # type name → class，通过 register() 注册

    def __init__(self, config: dict):
        self.config = config
        self._instances: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, type_name: str, agent_class: type):
        cls._agent_classes[type_name] = agent_class

    def get(self, name: str) -> BaseAgent:
        if name == "default":
            name = self.config["agents"]["default"]
        if name not in self._instances:
            agent_conf = self.config["agents"]["available"][name]
            cls = self._agent_classes[agent_conf["type"]]
            self._instances[name] = cls(agent_conf)
        return self._instances[name]
```

```python
# core/engine.py
class Engine:
    def __init__(self, bus, issue_store, task_manager, agent_registry, config):
        self.bus = bus
        self.issue_store = issue_store
        self.task_manager = task_manager
        self.agents = agent_registry  # AgentRegistry 实例
        self.config = config
        self.max_review_rounds = config.get("review_policy", {}).get("max_review_rounds", 3)
        self._bind_commands()

    def _bind_commands(self):
        self.bus.subscribe(MessageType.CMD_CREATE_ISSUE, self._on_create)
        self.bus.subscribe(MessageType.CMD_DESIGN, self._on_design)
        self.bus.subscribe(MessageType.CMD_DEVELOP, self._on_develop)
        self.bus.subscribe(MessageType.CMD_TEST, self._on_test)

    async def _run_with_review(self, issue, task, action, review_stage,
                                success_status, section_key, review_section_key):
        """通用的 执行→review→重试 循环，design 和 develop 共用"""
        try:
            for round_num in range(1, self.max_review_rounds + 1):
                # 执行阶段
                issue.status = IssueStatus[action.upper() + "ING"]  # DESIGNING / DEVELOPING
                self.issue_store.save(issue)
                await self.bus.publish(Message(MessageType.EVT_STATUS_CHANGED,
                    {"issue_id": issue.id, "status": issue.status.value, "round": round_num}))

                agent = self.agents.get(issue.assignee or "default")
                request = AgentRequest(action=action, issue=issue,
                    context={"worktree_path": task.worktree_path})
                response = await agent.execute(request)
                issue.sections[section_key] = response.content

                # Review 阶段
                issue.status = IssueStatus[review_stage.upper()]
                self.issue_store.save(issue)

                reviewer_name = self.config["reviewers"][action][0]
                reviewer = self.agents.get(reviewer_name)
                review = await reviewer.review(
                    AgentRequest(action="review", issue=issue,
                        context={"worktree_path": task.worktree_path}))
                issue.sections[review_section_key] = format_review(review)
                self.issue_store.save(issue)

                await self.bus.publish(Message(MessageType.EVT_REVIEW_RESULT,
                    {"issue_id": issue.id, "passed": review.passed,
                     "round": round_num, "comments": len(review.comments)}))

                if review.passed:
                    issue.status = success_status
                    self.issue_store.save(issue)
                    task.status = "completed"
                    await self.bus.publish(Message(MessageType.EVT_TASK_COMPLETED,
                        {"issue_id": issue.id, "task_id": task.task_id}))
                    return
                # 未通过：下一轮重试

            # 超过 max_review_rounds，进入 blocked
            issue.status = IssueStatus.BLOCKED
            self.issue_store.save(issue)
            task.status = "failed"
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id,
                 "reason": f"review 未通过，已重试 {self.max_review_rounds} 轮"}))

        except Exception as e:
            issue.status = IssueStatus.FAILED
            self.issue_store.save(issue)
            task.status = "failed"
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))

    async def _on_design(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = self.task_manager.create(issue, repo_path=self.repo_path, action="design",
            agent_name=issue.assignee or "default")
        await self._run_with_review(
            issue, task, action="design", review_stage="design_review",
            success_status=IssueStatus.APPROVED,
            section_key="设计", review_section_key="Design Review")

    async def _on_develop(self, msg: Message):
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = self.task_manager.create(issue, repo_path=self.repo_path, action="develop",
            agent_name=issue.assignee or "default")
        await self._run_with_review(
            issue, task, action="develop", review_stage="dev_review",
            success_status=IssueStatus.TESTING,
            section_key="开发步骤", review_section_key="Dev Review")

    async def _on_test(self, msg: Message):
        """测试阶段：执行测试，无 review 循环"""
        issue = self.issue_store.get(msg.payload["issue_id"])
        task = self.task_manager.create(issue, repo_path=self.repo_path, action="test",
            agent_name=issue.assignee or "default")
        try:
            issue.status = IssueStatus.TESTING
            self.issue_store.save(issue)

            agent = self.agents.get(issue.assignee or "default")
            response = await agent.execute(AgentRequest(
                action="test", issue=issue,
                context={"worktree_path": task.worktree_path}))
            issue.sections["测试"] = response.content

            if response.success:
                issue.status = IssueStatus.DONE
                task.status = "completed"
            else:
                issue.status = IssueStatus.FAILED
                task.status = "failed"
            self.issue_store.save(issue)
            await self.bus.publish(Message(
                MessageType.EVT_TASK_COMPLETED if response.success else MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id}))

        except Exception as e:
            issue.status = IssueStatus.FAILED
            self.issue_store.save(issue)
            task.status = "failed"
            await self.bus.publish(Message(MessageType.EVT_TASK_FAILED,
                {"issue_id": issue.id, "task_id": task.task_id, "reason": str(e)}))
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

    def create(self, issue, repo_path, action, agent_name) -> Task:
        task_id = str(uuid.uuid4())[:8]
        worktree_path = self.worktree_manager.create(repo_path, issue.id)
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
        return [t for t in self.tasks.values() if t.status == "running"]

    async def cancel(self, task_id):
        if task_id in self._running:
            self._running[task_id].cancel()
            self.tasks[task_id].status = "cancelled"
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
