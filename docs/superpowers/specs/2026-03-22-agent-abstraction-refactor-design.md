# Agent 抽象层重构设计

## 问题

当前 `BaseAgent` 只有通用的 `execute(action, ...) -> AgentResponse` 和 `review() -> ReviewResult`。
AgentResponse 是自由文本（content: str），Engine 侧 ad-hoc 解析格式（找 JSON、找 `RESULT:` 行）。

真实 e2e 验证中暴露的问题：
- test 结果显示 `(?/?)`——`RESULT:` 行没被解析到
- review JSON 解析失败时 fallback 到 not passed
- develop 的 `files_changed` 完全靠 agent 自报，不可靠
- 不同 agent 实现（ClaudeCode、Codex、LangChain）有不同的格式约束手段，但无法在抽象层统一

## 方案

将 `execute + review` 拆分为**每个 action 一个方法**，返回结构化类型。
子类负责把 LLM 原始输出解析成结构化类型，Base class 提供辅助方法。
Engine 直接使用结构化字段，零解析逻辑。

## 结构化输出类型

新建 `agents/types.py`：

```python
from __future__ import annotations
from dataclasses import dataclass, field
from shadowcoder.core.models import ReviewComment


@dataclass
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None


@dataclass
class DesignOutput:
    document: str
    usage: AgentUsage | None = None


@dataclass
class DevelopOutput:
    summary: str
    files_changed: list[str] = field(default_factory=list)
    usage: AgentUsage | None = None


@dataclass
class ReviewOutput:
    passed: bool
    comments: list[ReviewComment] = field(default_factory=list)
    reviewer: str = ""
    usage: AgentUsage | None = None


@dataclass
class TestOutput:
    report: str
    success: bool
    passed_count: int | None = None
    total_count: int | None = None
    recommendation: str | None = None
    usage: AgentUsage | None = None
```

`ReviewOutput` 取代现有的 `ReviewResult`（相同字段 + usage）。

## BaseAgent 新接口

```python
# agents/base.py
from abc import ABC, abstractmethod
import asyncio

class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def design(self, request: AgentRequest) -> DesignOutput: ...

    @abstractmethod
    async def develop(self, request: AgentRequest) -> DevelopOutput: ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewOutput: ...

    @abstractmethod
    async def test(self, request: AgentRequest) -> TestOutput: ...

    # --- 辅助方法 ---

    async def _get_files_changed(self, worktree_path: str) -> list[str]:
        """通过 git diff + ls-files 获取实际变更文件列表，不依赖 agent 自报"""
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", "HEAD",
            cwd=worktree_path, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        proc2 = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=worktree_path, stdout=asyncio.subprocess.PIPE)
        stdout2, _ = await proc2.communicate()
        files = set(stdout.decode().strip().splitlines()
                    + stdout2.decode().strip().splitlines())
        return sorted(f for f in files if f)

    def _extract_json(self, raw: str) -> dict:
        """从 raw 文本中提取 JSON，支持被 markdown 代码块包裹"""
        import json
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
```

`AgentRequest` 保持不变（action 字段保留，供子类内部判断或日志使用）。

删除：`AgentResponse`、`AgentStream`、`ReviewResult`（被新类型替代）。

## Engine 适配

Engine 不再做任何格式解析，直接使用结构化字段。

### _run_with_review 改动

```python
async def _run_with_review(self, issue, task, action, ...):
    for round_num in range(1, max_rounds + 1):
        # 执行阶段
        agent = self.agents.get(issue.assignee or "default")
        request = AgentRequest(action=action, issue=issue,
            context={"worktree_path": task.worktree_path})

        if action == "design":
            output = await agent.design(request)
            self.issue_store.update_section(issue.id, section_key, output.document)
            self._log(issue.id, f"Design R{round_num} 产出\n"
                f"内容长度: {len(output.document)} 字符")
        elif action == "develop":
            output = await agent.develop(request)
            self.issue_store.update_section(issue.id, section_key, output.summary)
            self._log(issue.id, f"Develop R{round_num} 产出\n"
                f"Files changed: {', '.join(output.files_changed)}")

        # review 阶段
        review = await reviewer.review(review_request)
        # review 是 ReviewOutput，直接用 review.passed, review.comments
```

关键变化：
- design/develop **不再有 `success=False` 路径**。agent 无法完成时抛异常，
  Engine 的 `except` 统一处理为 FAILED
- Engine **零格式解析**

### _on_test 改动

```python
async def _on_test(self, msg):
    output = await agent.test(request)
    self.issue_store.update_section(issue.id, "测试", output.report)

    if output.success:
        # 直接用 output.success，不解析 RESULT: 行
        ...
    else:
        recommendation = output.recommendation  # 直接字段，不从 metadata 读
        ...
```

## ClaudeCodeAgent 适配

每个方法的格式约束逻辑**自包含在 agent 实现中**：

```python
class ClaudeCodeAgent(BaseAgent):

    async def design(self, request) -> DesignOutput:
        result = await self._run_claude(prompt, system_prompt=DESIGN_SYSTEM)
        return DesignOutput(document=result)

    async def develop(self, request) -> DevelopOutput:
        cwd = request.context.get("worktree_path")
        result = await self._run_claude(prompt, cwd=cwd, system_prompt=DEVELOP_SYSTEM)
        files = await self._get_files_changed(cwd)
        return DevelopOutput(summary=result, files_changed=files)

    async def review(self, request) -> ReviewOutput:
        result = await self._run_claude(prompt, system_prompt=REVIEW_SYSTEM)
        data = self._extract_json(result)
        comments = [ReviewComment(...) for c in data["comments"]]
        return ReviewOutput(passed=data["passed"], comments=comments,
                           reviewer="claude-code")

    async def test(self, request) -> TestOutput:
        result = await self._run_claude(prompt, cwd=cwd, system_prompt=TEST_SYSTEM)
        success, recommendation, passed, total = self._parse_test_result(result)
        return TestOutput(report=result, success=success,
                         passed_count=passed, total_count=total,
                         recommendation=recommendation)

    def _parse_test_result(self, raw: str) -> tuple:
        """格式约束在 agent 实现层，不在 Engine"""
        ...
```

不同 agent 用不同手段约束格式：
- **ClaudeCode**: `--json-schema` (review), `_parse_test_result` (test), `git diff` (develop)
- **未来 Codex**: `response_format: { type: "json_schema" }`
- **未来 LangChain**: `PydanticOutputParser(pydantic_object=ReviewOutput)`

## 模型兼容性

`ReviewResult`（core/models.py）重命名为 `ReviewOutput`（agents/types.py），
原位置保留别名以兼容：

```python
# core/models.py
from shadowcoder.agents.types import ReviewOutput
ReviewResult = ReviewOutput  # backward compat
```

或者更简洁：直接迁移，一次性更新所有引用。考虑到代码量不大（99 个测试），
推荐一次性迁移。

## 改动范围汇总

| 文件 | 改动 |
|------|------|
| `agents/types.py` | 新建，定义所有 Output 类型 |
| `agents/base.py` | 重写接口：4 个 abstract 方法 + 辅助方法 |
| `agents/claude_code.py` | 适配新接口 |
| `agents/registry.py` | 无改动 |
| `core/models.py` | 删除 `ReviewResult`（迁移到 agents/types.py） |
| `core/engine.py` | 使用结构化类型，删除解析逻辑 |
| `core/issue_store.py` | `append_review` 参数类型改为 `ReviewOutput` |
| `tests/` | 更新所有使用 `AgentResponse`/`ReviewResult` 的测试 |
