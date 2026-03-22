# ShadowCoder

基于 Agent 的需求管理与开发系统。定义需求，让 AI Agent 自动完成设计、实现、审查、测试和迭代。

## 功能

ShadowCoder 管理一个 issue 的完整开发生命周期：

```
create → preflight → design ⇄ review → develop ⇄ review → test → done
                                  ↑                          |
                                  └──── 测试失败自动路由 ──────┘
```

每个阶段由可插拔的 AI Agent 执行（当前支持 Claude Code CLI）。Reviewer 也是 Agent。系统自动迭代直到质量达标，或上报给人类决策。

核心特性：
- **自动 review 循环**，带评分机制（0-100 分）。>= 90 通过，70-89 带条件通过，< 70 重试。
- **测试失败路由**：测试不通过时，Agent 分析原因并推荐回到 `develop` 或 `design`。
- **独立测试验证**：Engine 独立运行配置的测试命令（`cargo test`、`go test ./...` 等），覆盖 Agent 的自我报告。
- **前置评估（Preflight）**：设计开始前快速评估可行性，提前暴露风险。
- **完整审计**：每个操作记录到独立的 `.log.md` 时间线文件，每轮产出存档到 `.versions/`。

## 安装

```bash
git clone https://github.com/xforce-io/shadowcoder.git
cd shadowcoder
pip install -e ".[dev]"
```

## 配置

创建 `~/.shadowcoder/config.yaml`：

```yaml
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
      model: sonnet
      permission_mode: auto

reviewers:
  design: [claude-code]
  develop: [claude-code]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 3
  max_test_retries: 3
  # max_budget_usd: 10.0  # 可选的费用上限

build:
  test_command: "cargo test"  # 或 "go test ./..." 或 "pytest"

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

需要安装并认证 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)。

## 使用

### 命令行脚本

```bash
# 创建 issue（附带需求文档）
python scripts/run_real.py /path/to/your/repo create "功能名称" --from requirements.md

# 运行设计（含前置评估）
python scripts/run_real.py /path/to/your/repo design 1

# 运行开发
python scripts/run_real.py /path/to/your/repo develop 1

# 运行测试（失败时自动路由到 develop/design）
python scripts/run_real.py /path/to/your/repo test 1

# 其他命令
python scripts/run_real.py /path/to/your/repo list
python scripts/run_real.py /path/to/your/repo info 1
python scripts/run_real.py /path/to/your/repo approve 1    # 批准 BLOCKED 的 issue
python scripts/run_real.py /path/to/your/repo resume 1     # 从 BLOCKED 恢复
python scripts/run_real.py /path/to/your/repo cancel 1
python scripts/run_real.py /path/to/your/repo cleanup 1    # 清理 worktree
```

### TUI

```bash
shadowcoder
# 然后输入命令：create、design #1、develop #1、test #1、list、info #1 等
```

## 工作原理

### Issue 文件

每个 issue 以 markdown 文件存储在目标仓库中：

```
your-repo/.shadowcoder/issues/
  0001.md          # 当前状态（需求、设计、实现摘要、测试结果）
  0001.log.md      # 时间线（每个操作带时间戳，即"航海日志"）
  0001.versions/   # 存档（design_r1.md、design_r2.md、develop_r1.md ...）
```

### Git Worktree

每个 issue 有独立的 git worktree 和分支（`shadowcoder/issue-N`），隔离并发工作。worktree 在首次 `design` 时创建，贯穿 `develop` 和 `test`。issue 完成后用 `cleanup` 清理。

### Agent 抽象

Agent 实现五个方法，每个返回结构化类型：

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput: ...
    async def design(self, request) -> DesignOutput: ...
    async def develop(self, request) -> DevelopOutput: ...
    async def review(self, request) -> ReviewOutput: ...
    async def test(self, request) -> TestOutput: ...
```

每种 Agent 实现自行处理输出格式约束。例如 `ClaudeCodeAgent` 调用 `claude` CLI 并解析返回。添加新 Agent（Codex、LangChain 等）只需实现这五个方法。

### Review 评分

Review 返回 0-100 分：

| 分数 | 决策 |
|------|------|
| 90-100 | 通过 |
| 70-89 | 带条件通过（问题推迟到下一阶段处理） |
| < 70 | 不通过，重试 |

超过 `max_review_rounds` 次失败后，issue 进入 BLOCKED 状态，等待人类干预（`approve` 或 `resume`）。

## 验证结果

ShadowCoder 已在真实任务上完成端到端验证：构建 SQL 数据库引擎（解析器、查询规划器、执行器、存储引擎、B-tree 索引、MVCC 事务、错误处理）。

| 语言 | 设计轮次 | 开发轮次 | 代码产出 | 功能测试 | 性能测试 |
|------|---------|---------|---------|---------|---------|
| Go | 3 | 3 | 52 文件，17K 行 | 通过 | 通过 |
| Rust | 6 | 3 | 24 文件，10K 行 | 23/23 通过 | 5/7 通过 |
| Haskell | 9（阻塞） | 未到达 | - | - | - |

## 开发

```bash
# 运行测试
pytest tests/ -v

# 当前：138 个测试（单元 + 集成 + 端到端）
```

## 架构

```
src/shadowcoder/
  cli/tui/app.py        # Textual TUI
  core/
    bus.py               # 异步消息总线（命令/事件）
    engine.py            # 编排器（状态机、review 循环、测试路由）
    config.py            # YAML 配置（类型安全访问）
    issue_store.py       # Issue CRUD、sections、日志、版本存档
    models.py            # IssueStatus、TaskStatus、状态转换规则
    task_manager.py      # 运行时 Task 管理
    worktree.py          # Git worktree 生命周期（ensure/cleanup/exists）
  agents/
    types.py             # 结构化输出类型（DesignOutput、ReviewOutput 等）
    base.py              # Agent 抽象接口
    claude_code.py       # Claude Code CLI 实现
    registry.py          # Agent 发现与缓存
```

## License

MIT
