# ShadowCoder 问题与改进计划

基于完整 e2e 验证（SQL 数据库引擎任务，4 小时，17K 行 Go 代码）发现的问题。

---

## 一、Agent 抽象层重构（P0）

### 问题

当前 `BaseAgent` 只有一个通用的 `execute(action, ...)` 方法，返回自由文本。
格式约束完全靠 system prompt 引导，Engine 侧 ad-hoc 解析（找 JSON、找 `RESULT:` 行），
真实运行已证明不可靠（test 结果显示 `(?/?)`，review JSON 解析失败 fallback 到 not passed）。

### 方案

将 `execute + review` 拆分为 **每个 action 一个方法**，返回结构化类型。
不同 Agent 实现用不同手段保证输出格式（ClaudeCode 用 `--json-schema`，
Codex 用 response_format，LangChain 用 output parser），这正是抽象层要封装的。

```python
# 结构化输出类型
@dataclass
class DesignOutput:
    document: str

@dataclass
class DevelopOutput:
    summary: str
    files_changed: list[str]   # 由 base class 通过 git diff 自动填充

@dataclass
class ReviewOutput:
    passed: bool
    comments: list[ReviewComment]

@dataclass
class TestOutput:
    report: str
    success: bool
    passed_count: int
    total_count: int
    recommendation: str | None

# BaseAgent 拆分方法
class BaseAgent(ABC):
    @abstractmethod
    async def design(self, request: AgentRequest) -> DesignOutput: ...

    @abstractmethod
    async def develop(self, request: AgentRequest) -> DevelopOutput: ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewOutput: ...

    @abstractmethod
    async def test(self, request: AgentRequest) -> TestOutput: ...

    # Base class 提供的格式约束辅助
    def _enforce_json_output(self, raw: str, schema: type) -> dict:
        """尝试从 raw 文本提取 JSON，子类可调用"""
        ...

    async def _get_files_changed(self, worktree_path: str) -> list[str]:
        """通过 git diff 获取实际变更文件列表，不依赖 agent 自报"""
        ...
```

**ClaudeCode 实现格式约束的方式**：
- review: `claude -p --json-schema '{"passed": bool, "comments": [...]}'`
- test: 解析 `RESULT:` 行 + fallback 到全文分析
- develop: 调完 claude 后用 `git diff --name-only` 获取 files_changed

**未来 Codex 实现**：
- `response_format: { type: "json_schema", ... }`

**未来 LangChain 实现**：
- `PydanticOutputParser(pydantic_object=ReviewOutput)`

### 改动范围
- `agents/base.py`: 重构接口
- `agents/claude_code.py`: 适配新接口
- `core/engine.py`: 使用结构化类型替代 ad-hoc 解析
- 所有测试更新

---

## 二、Issue 文件格式分离（P0）

### 问题

`0001.md` 混合了三类信息：
1. **元数据**（frontmatter）—— id, title, status, tags
2. **关键产出**（sections）—— 需求、设计、开发步骤、测试结果
3. **时序日志**（航海日志）—— 不断增长，包含每轮的 review 历史

文件越来越大（真实运行中 issue 文件超过 100KB），且日志和产出混在一起难以阅读。

### 方案

拆分为两个文件：
```
.shadowcoder/issues/
  0001.md        # 元数据 + 关键产出（需求、设计、开发步骤、测试）
  0001.log.md    # 航海日志 + 完整 review 历史（只追加，不覆盖）
```

- `IssueStore.append_log()` 写入 `.log.md`
- `IssueStore.append_review()` 写入 `.log.md`（review 全文）
- `0001.md` 中的 Design Review / Dev Review section 只保留最后一次的摘要
- Agent 读 issue 时默认不加载 log 文件（减小 context），需要时显式加载

### 改动范围
- `core/issue_store.py`: 拆分读写逻辑
- `core/engine.py`: review 写入目标改为 log 文件
- `core/models.py`: Issue 加 `log_path` 字段

---

## 三、Develop 阶段增加编译/构建检查（P1）

### 问题

Agent 写了代码但 Engine 不验证是否能编译。真实运行中 reviewer 靠"阅读代码"
发现问题，但可能漏掉编译错误。Go 的 `go build` 失败不会被捕获。

### 方案

在 `DevelopOutput` 返回后、进入 review 前，Engine 自动跑构建检查：

```python
# Engine._run_with_review develop 分支
if action == "develop":
    build_result = await self._run_build_check(worktree_path)
    if not build_result.success:
        # 不进入 review，直接让 agent 修
        issue_store.update_section(issue.id, "开发步骤",
            response.summary + f"\n\n## Build Failed\n{build_result.output}")
        continue  # 下一轮
```

构建命令从配置读取：
```yaml
build:
  command: "go build ./..."    # 或 "python -m py_compile" / "cargo build"
  test_command: "go test ./..."
```

### 改动范围
- `core/config.py`: 加 `get_build_command()`
- `core/engine.py`: develop review 前加 build check
- `~/.shadowcoder/config.yaml`: 加 build 配置

---

## 四、Worktree 生命周期管理（P1）

### 问题

- 多轮 develop 每轮创建新 Task 但复用同一个 worktree，语义不清
- worktree 何时清理没有定义（issue DONE 后？CANCELLED 后？）
- 分支何时合并到主分支没有定义
- `git worktree prune` 是 workaround，不是方案

### 方案

明确生命周期：
```
create issue → 创建 worktree + 分支
design/develop/test → 在同一个 worktree 中工作
issue DONE → 提示用户合并分支（PR 或 merge），合并后清理 worktree
issue CANCELLED → 保留 worktree（用户可能要检查），提供 cleanup 命令
issue FAILED → 保留 worktree
```

新增命令：
- `cleanup #id` — 手动清理 worktree 和分支
- `merge #id` — 将 worktree 的分支合并到主分支

TaskManager.create 不再每次创建 worktree，改为 issue 级别管理：
```python
# 第一次 design 时创建，后续 develop/test 复用
worktree_path = await self.worktree_manager.ensure(repo_path, issue.id)
```

### 改动范围
- `core/worktree.py`: `ensure()` 替代 `create()`
- `core/task_manager.py`: worktree 与 issue 绑定，不与 task 绑定
- `core/engine.py`: 新增 `_on_cleanup`, `_on_merge`
- `core/bus.py`: 新增 `CMD_CLEANUP`, `CMD_MERGE`

---

## 五、成本追踪（P1）

### 问题

4 小时的真实运行没有任何成本统计。不知道花了多少 token、多少钱。

### 方案

- `AgentResponse` / 各 Output 类型加 `usage` 字段：
  ```python
  @dataclass
  class AgentUsage:
      input_tokens: int
      output_tokens: int
      cost_usd: float | None
      duration_ms: int
  ```
- ClaudeCodeAgent 从 `--output-format json` 的 response 中提取 usage
- 航海日志记录每次 agent 调用的 usage
- issue 完成时汇总总成本
- 配置 `max_budget_usd` 超限自动停止

### 改动范围
- `agents/base.py`: 加 `AgentUsage`
- `agents/claude_code.py`: 解析 usage
- `core/engine.py`: 累计 + 预算检查
- 航海日志格式扩展

---

## 六、Context 膨胀控制（P1）

### 问题

Design R2 产出 85KB。review 时需要把 requirements + 设计全文喂给 agent。
大项目会超出 context window。

### 方案

- Agent 构建 prompt 时按优先级裁剪 context：
  1. 始终包含：requirements（需求）
  2. 始终包含：当前阶段的 section（设计 or 开发步骤）
  3. 可选包含：上一轮 review comments（最近一次）
  4. 不包含：航海日志、历史 review
- 设置 `max_context_chars` 配置项，超过时截断低优先级内容
- Review 时只喂 diff（和上一轮相比的变化），不喂全文

### 改动范围
- `agents/base.py`: `_build_context()` 方法加优先级裁剪
- `core/config.py`: 加 `get_max_context_chars()`

---

## 七、中间状态持久化（P2）

### 问题

Develop R1 跑了 1.5 小时。如果中途进程被杀，代码在 worktree 里但
issue 状态卡在 `developing`。重跑会尝试创建新 worktree（冲突）。

### 方案

- Agent 调用前保存"进行中"标记到 issue 文件
- Agent 调用后立即保存结果（不等 review）
- 引入 `INTERRUPTED` 状态：检测到上次未完成的 session 时自动恢复
- worktree 已有代码变更时，不重新跑 agent，直接进入 review

### 改动范围
- `core/models.py`: 可能加 `INTERRUPTED` 状态
- `core/engine.py`: 启动时检测中断恢复
- `core/issue_store.py`: 原子写入（先写临时文件再 rename）

---

## 八、Section 分隔符鲁棒性（P2 — 已部分修复）

### 问题

经历了 3 次迭代才找到正确的分隔符方案：
1. `## ` → agent 输出 H2 标题冲突
2. `# ` → agent 输出 H1 标题冲突 + 代码块内 `#` 注释冲突
3. `<!-- section: X -->` → 目前方案，HTML 注释不冲突

### 残留风险

- 如果 agent 输出恰好包含 `<!-- section: X -->` 格式的 HTML 注释（概率极低但非零）
- 更安全的方案：用不可能出现在自然文本中的分隔符，如 `<!--§§ X §§-->`

### 建议

当前方案够用，加一个防御性检查：如果 agent 输出包含 section 分隔符模式，
在写入前 escape 掉。

---

## 执行优先级

| 优先级 | 项目 | 理由 |
|--------|------|------|
| **P0** | Agent 抽象层重构 | 当前格式解析是最大的可靠性风险 |
| **P0** | Issue 文件格式分离 | 文件增长不可控，影响可读性和 context |
| **P1** | 编译检查 | develop 产出不验证 = 隐患 |
| **P1** | Worktree 生命周期 | 真实使用中最常遇到的问题 |
| **P1** | 成本追踪 | 无预算控制的系统不可生产使用 |
| **P1** | Context 膨胀控制 | 大项目必遇 |
| **P2** | 中间状态持久化 | 长时间任务的可靠性 |
| **P2** | Section 分隔符加固 | 当前方案已足够，低概率风险 |

建议执行顺序：P0 两项先做（Agent 重构 + 文件分离），因为后续所有 P1 改动都依赖 Agent 接口的稳定。
