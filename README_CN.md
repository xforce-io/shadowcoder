[English](README.md) | [中文](README_CN.md)

# ShadowCoder

指向仓库，给出任务，自动编码直到完成。

```
         生成                验证                反馈
  Agent ──────────→ 代码 ──────────→ 评分 ──────────→ Agent
    ↑                                                    │
    └────────────── 迭代直到收敛 ────────────────────────┘
```

## 快速开始

```bash
pip install -e ".[dev]"

# 运行 — 已安装 Claude Code CLI 即可，无需配置
python scripts/run_real.py /path/to/repo run "添加用户认证" --from requirements.md

# 或直接指向 GitHub issue
python scripts/run_real.py /path/to/repo run --from https://github.com/owner/repo/issues/42
```

就这样。ShadowCoder 会创建设计、在隔离的 worktree 中编写代码、运行测试、评审输出，然后迭代直到全部通过。

## 工作流程

```
create → preflight → design ⇄ review → develop ⇄ gate ⇄ review → done
                                            ↑       │
                                            └───────┘
                                         失败：重试 develop
```

- **Preflight**：快速可行性评估，低可行性直接阻塞。
- **Design**：Agent 生成架构文档，Reviewer 评审。
- **Develop**：Agent 在隔离的 git worktree 中编写代码。
- **Gate**：Engine 独立运行测试（`cargo test`、`pytest`、`go test`），验证验收测试通过。失败回退到 develop。
- **Review**：Reviewer 评审代码 diff。通过 → 完成。

评审严重度计数是损失信号，随轮次递减：

```
五子棋 Design: R1=CRITICAL:2,HIGH:4 → R2=CRITICAL:1,HIGH:1 → R3=CRITICAL:0,HIGH:0（收敛）
```

## 验证结果

### SQL 数据库引擎

从需求文档构建（解析器、查询规划器、执行器、存储、B 树索引、MVCC 事务）：

| 语言 | 设计 | 开发 | 测试 | 代码量 |
|------|------|------|------|--------|
| Go | 3 轮 | 3 轮 | 1 轮 | 17K 行 |
| Rust | 6 轮 | 4 轮 | 3 轮 | 10K 行 |
| Haskell | 9 轮（阻塞） | - | - | - |

Rust 版本展示了完整循环：Agent 报告测试通过，但独立验证发现 2 个性能基准失败。系统自动路由回 develop，Agent 优化代码，下一轮全部 44 个测试通过。

### 五子棋 AI（Rust，Claude Sonnet）

| 阶段 | 轮次 | 备注 |
|------|------|------|
| 设计 | 3 | R1：2 个 CRITICAL。R3：通过 |
| 开发 | 4 | R1-R3：gate 失败。R4：全部测试通过 |

AI（depth=4）对 baseline：100 局胜率 >90%。

### 多模型：LRU 缓存（Python，DeepSeek-v3 via 火山引擎）

| 阶段 | 轮次 | 备注 |
|------|------|------|
| 设计 | 2 | R1：1 个 CRITICAL，3 个 HIGH。R2：有条件通过 |
| 开发 | 1 | 首次 gate 通过。26 个测试 |

任何通过 Anthropic 兼容 API 可达的模型都能驱动完整循环。

## 配置

**零配置**：已安装并认证 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 即可直接使用，无需配置文件。默认使用本地 Claude Code。

**高级配置**：创建 `~/.shadowcoder/config.yaml` 自定义模型、接入第三方 API 或混合使用 Agent：

```yaml
clouds:
  anthropic:
    env: {}
  volcengine:
    env:
      ANTHROPIC_BASE_URL: https://ark.cn-beijing.volces.com/api/coding
      ANTHROPIC_AUTH_TOKEN: <key>

models:
  sonnet:
    cloud: anthropic
    model: sonnet
  deepseek-v3:
    cloud: volcengine
    model: deepseek-v3-2-251201

agents:
  claude-coder:
    type: claude_code
    model: sonnet
  fast-coder:
    type: claude_code
    model: deepseek-v3

dispatch:
  design: fast-coder
  develop: fast-coder
  design_review: [claude-coder]
  develop_review: [claude-coder]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 5
  max_test_retries: 3
  # max_budget_usd: 10.0
```

自由混合 Agent：一个负责开发，另一个负责评审。

## 使用方法

```bash
# 完整循环 — 标题 + 需求文件
python scripts/run_real.py /path/to/repo run "功能名称" --from requirements.md

# 完整循环 — 从 GitHub issue（自动提取标题）
python scripts/run_real.py /path/to/repo run --from https://github.com/owner/repo/issues/42

# 恢复上次 issue
python scripts/run_real.py /path/to/repo run

# 单独运行各阶段
python scripts/run_real.py /path/to/repo design 1
python scripts/run_real.py /path/to/repo develop 1

# 人工介入控制
python scripts/run_real.py /path/to/repo approve 1    # 批准阻塞的 issue
python scripts/run_real.py /path/to/repo resume 1     # 从阻塞处重试
python scripts/run_real.py /path/to/repo cancel 1

# 查询
python scripts/run_real.py /path/to/repo list
python scripts/run_real.py /path/to/repo info 1

# 清理
python scripts/run_real.py /path/to/repo cleanup 1
python scripts/run_real.py /path/to/repo cleanup 1 --delete-branch
```

## 工作原理

ShadowCoder 将人类开发循环自动化：编写代码 → 验证 → 修复 → 重复。这在结构上等同于神经-符号训练循环：

| 训练概念 | ShadowCoder 对应 |
|----------|-----------------|
| 前向传播 | Agent 生成设计/代码 |
| 损失函数 | 评审严重度计数 (CRITICAL/HIGH) + 测试退出码 |
| 反向传播 | 评审反馈注入下一轮上下文 |
| 梯度裁剪 | 每轮功能容量限制 |
| 早停 | 达到最大轮次，升级给人类 |
| 真值预言机 | 独立测试验证 |

关键区别：ShadowCoder 优化的是**输出产物**（代码），而非模型权重。它是一个测试时计算系统。

### 符号约束

"符号"部分保障系统可靠性：

- **状态机**：Issue 生命周期带验证的状态转换，不能跳过阶段。
- **评审阈值**：基于 CRITICAL/HIGH/MEDIUM/LOW 计数的确定性通过/失败决策。
- **独立测试验证**：Engine 自行运行测试。退出码非零则覆盖 Agent 的 PASS 为 FAIL。
- **预算限制**：每次 Agent 调用后检查累计 token 成本。
- **重试上限**：最大评审轮次和测试重试次数防止无限循环。

### 架构

```
src/shadowcoder/
  core/
    engine.py          # 循环：状态机 + 评审评分 + 测试验证
    bus.py             # 异步消息总线
    issue_store.py     # Issue 文件、日志、版本归档
    models.py          # 状态、转换
    config.py          # 类型化配置，支持零配置
    task_manager.py    # 运行时任务
    worktree.py        # Git worktree 生命周期
  agents/
    types.py           # 结构化输出类型
    base.py            # 抽象接口 + 辅助方法
    claude_code.py     # Claude Code CLI 实现
    registry.py        # Agent 发现
```

### Agent 抽象

```python
class BaseAgent(ABC):
    async def preflight(self, request) -> PreflightOutput
    async def design(self, request) -> DesignOutput
    async def develop(self, request) -> DevelopOutput
    async def review(self, request) -> ReviewOutput
```

测试由 Engine 的 gate 处理——不是 Agent。添加新 Agent 只需实现这四个方法。

### 审计追踪

```
.shadowcoder/issues/
  0001.md          # 当前状态（需求、设计、实现、测试结果）
  0001.log.md      # 按时间顺序的时间线——每个操作带时间戳
  0001.versions/   # 归档输出——design_r1.md, design_r2.md, develop_r1.md, ...
```

日志仅追加，不丢失任何信息。

## 已知限制

- **成本追踪不完整**：无法可靠从 Claude CLI 响应中提取 token 计数和成本。
- **无优雅停止**：终止运行中的 Agent 需要 `pkill`。
- **无断点续传**：中断的 develop 会话无法从部分进度自动恢复。
- **单 repo 单进程**：同一 repo 并发工作需使用不同进程。

## 路线图

- **上下文压缩**：用快速模型结构化摘要替代 head+tail 截断。
- **Prompt 审计**：每次运行后自动评估上下文效率。
- **并行 issue**：支持并发执行，带适当锁机制。

## 许可证

MIT
