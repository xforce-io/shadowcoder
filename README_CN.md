# ShadowCoder

一个 neural-symbolic 自进化开发系统。AI Agent 生成代码（neural），结构化的 review 评分、状态机和确定性测试验证（symbolic）驱动迭代改进——直到输出收敛到满足需求。

## 核心思想

传统软件开发依赖人类在"写代码"和"验证"之间反复迭代。ShadowCoder 将这个循环自动化：

```
         生成                验证                反馈
  Agent ──────────→ 代码 ──────────→ 分数 ──────────→ Agent
    ↑                                                    │
    └────────────── 迭代直到收敛 ─────────────────────────┘
```

这在结构上等价于一个 neural-symbolic training loop：

| 训练概念 | ShadowCoder 对应 |
|---------|-----------------|
| 前向传播 | Agent 生成设计/代码 |
| 损失函数 | Review 分数（0-100）+ 测试 exit code |
| 反向传播 | Review 反馈注入下一轮 context |
| 梯度裁剪 | 每轮特性容量限制 |
| 早停 | 超过最大轮次，上报人类 |
| 课程学习 | 分阶段：preflight → design → develop → test |
| Ground truth oracle | 独立测试验证（`cargo test`、`go test`） |

与模型训练的关键区别：ShadowCoder 优化的是**输出产物**（代码），而非模型权重。这是一个 test-time compute 系统——通过推理时的迭代而非训练来提升输出质量。

## 循环

```
create → preflight → design ⇄ review → develop ⇄ review → test → done
                                  ↑                          │
                                  └──── 测试失败自动路由 ──────┘
```

各阶段：

- **Preflight**：快速可行性评估。可行性低则直接阻塞，避免浪费算力。
- **Design**：Agent 产出架构设计。Reviewer 评分。
  - 分数 >= 90：通过。70-89：带条件通过。< 70：带反馈重试。
- **Develop**：Agent 在隔离的 git worktree 中编写真实代码。Reviewer 评分。
- **Test**：Agent 运行测试。然后 Engine 独立运行配置的测试命令并检查 exit code。如果真实测试失败，覆盖 Agent 的自我报告。
  - 失败分析 → 自动路由到 `develop` 或 `design` → 重新测试。

Review 分数就是 loss signal，逐轮下降（改善）——一条字面意义上的 training curve：

```
Rust SQL 引擎设计：R1=52 → R2=68 → R3=73（收敛到阈值以上）
```

## Symbolic 约束

"Symbolic" 半边是系统可靠性的保证：

- **状态机**：Issue 生命周期有合法转换验证，不能跳过阶段。
- **Review 分数阈值**：从连续分数做确定性的 通过/不通过/带条件 决策。
- **独立测试验证**：Engine 自己运行 `cargo test` / `go test` / `pytest`。exit code 非零则覆盖 Agent 的 PASS 为 FAIL。这是不可欺骗的 ground truth oracle。
- **预算限制**：每次 Agent 调用后累计 token 成本。超限则停止循环。
- **重试上限**：最大 review 轮次和最大 test 重试次数防止无限循环。

这些约束不能被 neural 组件绕过。它们是游戏规则。

## 验证结果

从一个 requirements 文档出发，构建 SQL 数据库引擎（解析器、查询规划器、执行器、存储引擎、B-tree 索引、MVCC 事务、错误处理）：

| 语言 | 设计轮次 | 开发轮次 | 测试轮次 | 代码量 | 功能测试 | 性能测试 |
|------|---------|---------|---------|-------|---------|---------|
| Go | 3 | 3 | 1 | 17K 行 | 通过 | 通过 |
| Rust | 6 | 4 | 3 | 10K 行 | 37/37 | 7/7（自动路由修复） |
| Haskell | 9（阻塞） | - | - | - | - | - |

Rust 版展示了完整的自进化循环：Agent 报告测试通过，但独立验证发现 2 个性能基准失败。系统自动路由回 develop，Agent 优化了代码，下一轮全部 44 个测试通过。

Haskell 版展示了优雅失败：系统通过 reviewer 反馈识别出 Haskell 的 STM/IO 交互模型使并发事务设计从根本上有问题，上报人类而非无限循环。

## 安装

```bash
git clone https://github.com/xforce-io/shadowcoder.git
cd shadowcoder
pip install -e ".[dev]"
```

需要安装并认证 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)。

## 配置

`~/.shadowcoder/config.yaml`：

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
  # max_budget_usd: 10.0

build:
  test_command: "cargo test"  # 或 "go test ./..." 或 "pytest"

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

## 使用

```bash
# 创建 issue
python scripts/run_real.py /path/to/repo create "功能名称" --from requirements.md

# 运行循环
python scripts/run_real.py /path/to/repo design 1
python scripts/run_real.py /path/to/repo develop 1
python scripts/run_real.py /path/to/repo test 1

# 人类介入控制
python scripts/run_real.py /path/to/repo approve 1    # 批准阻塞的 issue
python scripts/run_real.py /path/to/repo resume 1     # 从阻塞恢复
python scripts/run_real.py /path/to/repo cancel 1
python scripts/run_real.py /path/to/repo cleanup 1    # 清理 worktree

# 查询
python scripts/run_real.py /path/to/repo list
python scripts/run_real.py /path/to/repo info 1
```

或通过 TUI：`shadowcoder`

## 审计轨迹

每个 issue 维护完整记录：

```
.shadowcoder/issues/
  0001.md          # 当前状态（需求、最新设计、实现摘要、测试结果）
  0001.log.md      # 时间线——每个操作带时间戳（航海日志）
  0001.versions/   # 版本存档——design_r1.md、design_r2.md、develop_r1.md ...
```

日志只追加。设计/代码 section 展示最新版本；历史版本在 `.versions/`。Review 历史在日志中。不丢失任何信息。

## Agent 抽象

Agent 实现五个方法，返回结构化类型：

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput
    async def design(self, request) -> DesignOutput
    async def develop(self, request) -> DevelopOutput
    async def review(self, request) -> ReviewOutput
    async def test(self, request) -> TestOutput
```

每种 Agent 在内部处理自己的输出格式约束。Engine 不解析任何 LLM 原始输出——只消费类型化字段。添加新 Agent（Codex、LangChain、本地模型）只需实现这五个方法。

## 架构

```
src/shadowcoder/
  core/
    engine.py          # 循环：状态机 + review 评分 + 测试验证
    bus.py             # 异步消息总线
    issue_store.py     # Issue 文件、日志、版本存档
    models.py          # 状态、转换规则
    config.py          # 类型安全配置
    task_manager.py    # 运行时 Task 管理
    worktree.py        # Git worktree 生命周期
  agents/
    types.py           # 结构化输出类型
    base.py            # 抽象接口 + 辅助方法
    claude_code.py     # Claude Code CLI 实现
    registry.py        # Agent 发现与缓存
  cli/tui/app.py       # Textual TUI
```

138 个测试。18 个源文件。约 1,700 行 Python。

## 已知限制

- **成本追踪不完整**：`AgentUsage` 字段已定义，但 Claude CLI 的 JSON 返回中 token 和费用解析不可靠，usage 显示 `$0.0000`。
- **Go 验证说明**：Go SQL 引擎在独立测试验证功能实现之前完成验证，结果基于手动 `go test`，非自动化验证循环。
- **无优雅停止**：终止正在运行的 agent 需要 `pkill`，`stop` 命令尚未实现。
- **无断点恢复**：长时间 develop 过程中断后，无法从部分进度自动恢复。
- **单 reviewer 模型**：设计和代码 review 目前使用同一个 agent 实例。跨模型 review（如 Opus 审查 Sonnet 的输出）已规划但未实现。

## License

MIT
