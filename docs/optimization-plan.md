# ShadowCoder 优化计划

基于完整 e2e 验证（Go SQL 引擎 4h 通过 + Haskell SQL 引擎 3h 未通过设计）总结。

---

## 1. Review 分级决策（替代 pass/fail 二元判断）

**问题**：reviewer 找到任何 HIGH 就不通过，Haskell 版 9 轮设计全部 NOT PASSED。
实际上 R1 的 CRITICAL（STM 并发安全）和 R9 的 HIGH（缺个 import）不是一个量级。

**方案**：reviewer 返回信心分数 0-100，Engine 按阈值决策：
- < 70：重做
- 70-90：带条件通过，issues 记录到下一阶段处理
- ≥ 90：通过

ReviewOutput 扩展：
```python
@dataclass
class ReviewOutput:
    passed: bool              # 保留，由 Engine 根据 score 决定
    score: int                # 0-100 信心分数
    comments: list[ReviewComment]
    reviewer: str
    usage: AgentUsage | None
```

Engine 决策逻辑：
```python
if review.score >= 90:
    # 通过
elif review.score >= 70:
    # 带条件通过，HIGH issues 记入 log，下一阶段处理
    self._log(issue.id, f"带条件通过 (score={review.score})，遗留问题已记录")
else:
    # 重做
```

## 2. Section 增量迭代（避免全量重写）

**问题**：84KB 设计文档每轮全量重写耗时 15 分钟。agent 倾向输出补丁。

**方案**：agent 可以选择输出完整文档或增量补丁，系统自动处理：

DesignOutput/DevelopOutput 扩展：
```python
@dataclass
class DesignOutput:
    document: str
    is_patch: bool = False     # True = 只包含变更部分，需要 merge
    usage: AgentUsage | None = None
```

Engine 处理：
```python
if output.is_patch:
    # 追加到现有内容，加分隔标记
    existing = self.issue_store.get(issue.id).sections.get(section_key, "")
    merged = existing + f"\n\n---\n## Revision (R{round_num})\n" + output.document
    self.issue_store.update_section(issue.id, section_key, merged)
else:
    # 完整替换
    self.issue_store.update_section(issue.id, section_key, output.document)
```

## 3. Preflight Check 阶段

**问题**：Haskell SQL 引擎跑了 3 小时 9 轮才暴露"方向有问题"。

**方案**：design 前增加轻量级 preflight，几分钟内评估可行性：

BaseAgent 新增方法：
```python
@dataclass
class PreflightOutput:
    feasibility: str          # "high" / "medium" / "low"
    estimated_complexity: str  # "simple" / "moderate" / "complex" / "very_complex"
    risks: list[str]
    tech_stack_recommendation: str | None
    usage: AgentUsage | None = None

class BaseAgent(ABC):
    async def preflight(self, request: AgentRequest) -> PreflightOutput: ...
```

Engine 流程：
```
create → preflight → design → develop → test → done
```

preflight 返回 low feasibility 时，Engine 自动 log 警告并暂停等人类确认，
而不是直接进入 design 花几小时发现不行。

## 4. Reviewer 和 Designer 使用不同 prompt/记忆

**问题**：reviewer 和 designer 是同一个 agent 实例，可能存在盲区。

**方案**（后续调整）：
- 配置中 reviewer 和 designer 可以使用不同的 system prompt
- Reviewer 至少不应该看到 designer 的 system prompt（避免"知道对方怎么想的"）
- Review prompt 根据轮次调整：前几轮关注架构，后几轮关注细节
- 长期：支持不同模型做 review（如 opus review sonnet 的设计）

## 5. 长任务 Checkpoint 机制

**问题**：Develop R1 跑了很久被 kill，代码在 worktree 但无 commit，无法恢复。

**方案**：

ClaudeCodeAgent 的 develop 方法定期 auto-commit：
```python
async def develop(self, request):
    # 在 system prompt 中要求 agent 每完成一个模块就 git commit
    system = """
    ...
    IMPORTANT: After completing each module/file, run:
      git add -A && git commit -m "wip: <module name>"
    This creates checkpoints in case of interruption.
    """
```

Engine 恢复逻辑：
- 检测到 worktree 有未完成的 wip commits → 跳过 agent 执行，直接进入 review
- 或者提示用户选择：继续（从 checkpoint 恢复）还是重来

## 6. 优雅停止机制

**问题**：用户说"停止"只能 pkill。

**方案**：
- 新增 `CMD_STOP` 消息
- Engine 维护当前运行的 agent 进程引用
- `stop #id` → cancel asyncio task + kill subprocess
- agent 的 `_run_claude` 方法支持 cancel token

---

## 优先级

| 优先级 | 项目 | 理由 |
|--------|------|------|
| P0 | Review 分级决策 | 直接影响迭代效率，Haskell 9 轮的根因 |
| P0 | Preflight check | 避免方向性错误浪费数小时 |
| P1 | Section 增量迭代 | 大文档全量重写太慢 |
| P1 | Checkpoint 机制 | 长任务可靠性 |
| P1 | 优雅停止 | 基本可用性 |
| P2 | Reviewer prompt 分离 | 质量改进，不紧急 |
