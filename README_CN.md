[English](README.md) | [中文](README_CN.md)

# ShadowCoder

神经-符号自进化开发系统。AI Agent 生成代码（神经），结构化评审评分、状态机和确定性测试验证（符号）驱动迭代改进——直到输出收敛满足需求。

## 核心思想

传统软件开发依赖人类在编写代码和验证之间反复迭代。ShadowCoder 将这个循环自动化：

```
         生成                验证                反馈
  Agent ──────────→ 代码 ──────────→ 评分 ──────────→ Agent
    ↑                                                    │
    └────────────── 迭代直到收敛 ────────────────────────┘
```

这在结构上等同于神经-符号训练循环：

| 训练概念 | ShadowCoder 对应 |
|----------|-----------------|
| 前向传播 | Agent 生成设计/代码 |
| 损失函数 | 评审严重度计数 (CRITICAL/HIGH) + 测试退出码 |
| 反向传播 | 评审反馈注入下一轮上下文 |
| 梯度裁剪 | 每轮功能容量限制 |
| 早停 | 达到最大轮次，升级给人类 |
| 课程学习 | 分阶段：preflight → design → develop → test |
| 真值预言机 | 独立测试验证（`cargo test`、`go test`） |

与模型训练的关键区别：ShadowCoder 优化的是**输出产物**（代码），而非模型权重。它是一个测试时计算系统——通过推理时迭代提升质量，而非训练。

## 循环流程

```
create → preflight → design ⇄ review → develop ⇄ gate ⇄ review → done
                                            ↑       │
                                            └───────┘
                                         失败：重试 develop
```

各阶段：

- **Preflight**：快速可行性评估。低可行性直接阻塞，避免浪费算力。
- **Design**：Agent 生成架构文档，Reviewer 评审。
  - 无 CRITICAL 或 HIGH：通过。1-2 个 HIGH：有条件通过。任何 CRITICAL 或 3+ HIGH：带反馈重试。
- **Develop**：Agent 在隔离的 git worktree 中编写实际代码。
- **Gate**：Engine 独立运行测试命令（`cargo test`、`pytest` 等），验证验收测试已执行且通过。Gate 失败只回退到 develop——永不回退到 design。
  - 连续 2 次 gate 失败后，调用 reviewer 分析失败原因并提供针对性反馈。
- **Review**：Gate 通过后，Reviewer 评审代码 diff。通过或有条件通过 → 完成。

评审严重度计数是损失信号，随轮次递减——一条真实的训练曲线：

```
五子棋 Design: R1=CRITICAL:2,HIGH:4 → R2=CRITICAL:1,HIGH:1 → R3=CRITICAL:0,HIGH:0（收敛）
```

## 符号约束

"符号"部分是系统可靠性的保障：

- **状态机**：Issue 生命周期带验证的状态转换，不能跳过阶段。
- **评审严重度阈值**：基于 CRITICAL/HIGH/MEDIUM/LOW 评论计数的确定性通过/失败/有条件通过决策。
- **独立测试验证**：Engine 自行运行 `cargo test` / `go test` / `pytest`。如果退出码非零，Agent 的 PASS 被覆盖为 FAIL。这是不可协商的真值预言机。
- **预算限制**：每次 Agent 调用后检查累计 token 成本。超出限制则停止循环。
- **重试上限**：最大评审轮次和最大测试重试次数防止无限循环。

这些约束不能被神经组件绕过。它们是游戏规则。

## 验证结果

### SQL 数据库引擎

从需求文档构建 SQL 数据库引擎（解析器、查询规划器、执行器、存储、B 树索引、MVCC 事务、错误处理）：

| 语言 | 设计 | 开发 | 测试 | 代码量 | 功能测试 | 性能测试 |
|------|------|------|------|--------|---------|---------|
| Go | 3 轮 | 3 轮 | 1 轮 | 17K 行 | 通过 | 通过 |
| Rust | 6 轮 | 4 轮 | 3 轮 | 10K 行 | 37/37 | 7/7（自动路由修复） |
| Haskell | 9 轮（阻塞） | - | - | - | - | - |

Rust 版本展示了完整的自进化循环：Agent 报告测试通过，但独立验证发现 2 个性能基准测试失败。系统自动路由回 develop，Agent 优化代码，下一轮全部 44 个测试通过。

Haskell 版本展示了优雅失败：系统通过 reviewer 反馈识别出 Haskell 的 STM/IO 交互模型使并发事务设计存在根本性问题，升级给人类处理而非无限循环。

### 五子棋 AI（Rust，Claude Sonnet）

带 minimax + alpha-beta 剪枝、模式识别和 Web 界面的五子棋 AI 引擎：

| 阶段 | 轮次 | 备注 |
|------|------|------|
| 设计 | 3 | R1：2 个 CRITICAL（搜索期间 mutex 锁、缺少 IDDFS）。R3：通过 |
| 开发 | 4 | R1-R3：gate 失败（abort 标志、战术测试）。R4：全部测试通过 |

AI（depth=4）对 baseline：100 局胜率 >90%。全部验收标准达成。

### 多模型：LRU 缓存（Python，DeepSeek-v3 via 火山引擎）

带 TTL 支持的线程安全 LRU 缓存，验证第三方模型集成：

| 阶段 | 轮次 | 备注 |
|------|------|------|
| 设计 | 2 | R1：1 个 CRITICAL，3 个 HIGH。R2：有条件通过 |
| 开发 | 1 | 首次 gate 通过。26 个测试（正确性 + 并发 + 性能） |

验证了任何通过 Anthropic 兼容 API 可达的模型都能驱动完整循环。

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
    volcengine:                # 通过兼容 API 接入第三方模型
      type: claude_code
      model: deepseek-v3-2-251201
      permission_mode: auto
      env:                     # 传递给 claude CLI 子进程的自定义环境变量
        ANTHROPIC_BASE_URL: https://ark.cn-beijing.volces.com/api/coding
        ANTHROPIC_AUTH_TOKEN: <your-key>

reviewers:
  design: [claude-code]
  develop: [claude-code]       # 可混合使用 agent，例如 [volcengine]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 5
  max_test_retries: 3
  # max_budget_usd: 10.0

build:
  test_command: "cargo test"  # 或 "go test ./..." 或 "pytest"

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
```

任何可通过 Claude CLI `--model` 参数访问的模型都能使用——包括通过 Anthropic 兼容 API 提供的第三方模型。通过 `env` 字段覆盖每个 agent 的 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_AUTH_TOKEN`。

## 使用方法

```bash
# 创建 issue 并指定需求
python scripts/run_real.py /path/to/repo create "Feature" --from requirements.md

# 运行完整循环（或单独运行各阶段）
python scripts/run_real.py /path/to/repo run "Feature" --from requirements.md
python scripts/run_real.py /path/to/repo design 1
python scripts/run_real.py /path/to/repo develop 1

# 人工介入控制
python scripts/run_real.py /path/to/repo approve 1    # 批准阻塞的 issue
python scripts/run_real.py /path/to/repo resume 1     # 从阻塞处重试
python scripts/run_real.py /path/to/repo cancel 1
python scripts/run_real.py /path/to/repo cleanup 1    # 清理 worktree

# 查询
python scripts/run_real.py /path/to/repo list
python scripts/run_real.py /path/to/repo info 1
```

或通过 TUI：`shadowcoder`

## 审计追踪

每个 issue 维护完整记录：

```
.shadowcoder/issues/
  0001.md          # 当前状态（需求、最新设计、实现、测试结果）
  0001.log.md      # 按时间顺序的时间线——每个操作带时间戳
  0001.versions/   # 归档输出——design_r1.md, design_r2.md, develop_r1.md, ...
```

日志仅追加。设计/代码部分显示最新版本；历史版本在 `.versions/` 中。评审记录在日志中。不丢失任何信息。

## Agent 抽象

Agent 实现四个方法，返回结构化类型：

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput
    async def design(self, request) -> DesignOutput
    async def develop(self, request) -> DevelopOutput
    async def review(self, request) -> ReviewOutput
```

测试由 Engine 的 gate 处理（而非 Agent）——独立运行测试命令并检查退出码。

每个 Agent 在内部处理自己的输出格式约束。Engine 从不解析原始 LLM 输出——只消费类型化字段。添加新 Agent（Codex、LangChain、本地模型）只需实现这些方法。

## 架构

```
src/shadowcoder/
  core/
    engine.py          # 循环：状态机 + 评审评分 + 测试验证
    bus.py             # 异步消息总线
    issue_store.py     # Issue 文件、日志、版本归档
    models.py          # 状态、转换
    config.py          # 类型化配置
    task_manager.py    # 运行时任务
    worktree.py        # Git worktree 生命周期
  agents/
    types.py           # 结构化输出类型
    base.py            # 抽象接口 + 辅助方法
    claude_code.py     # Claude Code CLI 实现
    registry.py        # Agent 发现
  cli/tui/app.py       # Textual TUI
```

143 个测试，18 个源文件，约 1,800 行 Python。

## 已知限制

- **成本追踪不完整**：`AgentUsage` 字段已定义，但 Claude CLI JSON 响应解析无法可靠提取 token 计数和成本。使用摘要显示 `$0.0000`。
- **Go 验证说明**：Go SQL 引擎在独立测试验证功能存在之前验证。其结果基于手动 `go test` 运行，而非自动化验证循环。
- **无优雅停止**：终止运行中的 agent 需要 `pkill`。尚未实现 `stop` 命令。
- **无断点续传**：如果长时间 develop 会话中断，没有从部分进度自动恢复的机制。
- **单 repo 单进程**：不能对同一 repo 并行运行多个 issue。使用不同 repo 或进程进行并发工作。

## 路线图

- **上下文压缩**：用快速模型（Haiku）结构化摘要替代 head+tail 截断，用于 gate 输出和 agent 间上下文传递。Head+tail 作为降级方案。
- **Prompt 审计**：每次运行后自动评估上下文效率——哪些字段被 agent 使用了，哪些被忽略了，哪里上下文不足。
- **并行 issue**：支持对同一 repo 并发执行多个 issue，带适当的锁机制。

## 许可证

MIT
