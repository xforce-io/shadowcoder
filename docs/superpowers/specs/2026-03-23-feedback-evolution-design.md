# 反馈演化系统设计

## 问题

当前反馈机制是原始的：每轮 review 的 comments 作为文本塞进下一轮 context。

缺失：
- 不跟踪哪条 feedback 被解决了、哪条没有
- 同一问题重复提出 N 次，agent 没有收到更强的信号
- 测试由 developer agent 自己写——自己出题自己答，可以写弱测试蒙混过关
- 跨 issue 没有知识积累，同样的坑反复踩

## 三层反馈演化

### 第一层：Feedback Item 跟踪（单 issue 内）

每条 review comment 成为可追踪的 FeedbackItem：

```python
@dataclass
class FeedbackItem:
    id: str                    # 唯一标识
    category: str              # "architecture" / "error_handling" / "performance" / ...
    description: str           # 具体问题描述
    round_introduced: int      # 哪轮提出的
    times_raised: int          # 被重复提出了几次（跨轮累计）
    resolved: bool             # 是否已解决
    escalation_level: int      # 升级等级（1=正常, 2=更具体, 3=给代码示例, 4+=上报人类）
```

### Feedback 匹配机制：Reviewer 显式标注

**不做算法匹配。** 让 reviewer 显式判断每个旧 item 是否已解决。

Reviewer 的 prompt 中注入当前 unresolved items：

```
当前未解决的 feedback items:
  #F1: deadlock detection — wait-for graph 环检测未实现
  #F3: NULL handling — NOT IN with NULL 语义错误
  #F4: index scan fallback — 非等值 join 无 fallback

请在 review 中：
1. 对每个未解决 item，判断是否已解决（列入 resolved_item_ids）
2. 发现新问题则作为新 comment 提出
```

ReviewOutput 包含 `resolved_item_ids`（见下方第三层 ReviewOutput 定义）。

Engine 的更新逻辑是纯 symbolic 的，零匹配算法：

```python
def update_feedback(self, feedback_state, review):
    # 标记已解决
    for item_id in review.resolved_item_ids:
        feedback_state.resolve(item_id)

    # 未解决的旧 item → times_raised += 1
    for item in feedback_state.unresolved():
        if item.id not in review.resolved_item_ids:
            item.times_raised += 1
            item.escalation_level = min(item.times_raised, 4)

    # 新 comments → 创建新 FeedbackItem
    for comment in review.comments:
        feedback_state.add(FeedbackItem(
            id=feedback_state.next_id(),
            category=comment.severity.value,
            description=comment.message,
            round_introduced=current_round,
            times_raised=1,
            resolved=False,
            escalation_level=1))
```

**为什么让 reviewer 做而不是自动匹配：**
- Reviewer 在 review 过程中自然会判断"这个问题修了没"——我们只是让它结构化输出这个判断
- 不需要语义相似度算法、嵌入向量、NLP pipeline
- Reviewer 的判断比任何启发式匹配都准确（它理解代码语义）

给 agent 的反馈格式：

```
已解决 (3/5):
  [R1] #F1 NULL handling ✓
  [R1] #F2 error codes ✓
  [R2] #F5 type checking ✓

未解决 (2/5):
  [R1→R3, 3次] #F3 deadlock detection — 升级为 CRITICAL
    你之前两次尝试都没正确处理 wait-for graph 的环检测。
    具体要求：使用 DFS 检测 wait-for graph 中的环，选择代价最小的事务回滚。
  [R2→R3, 2次] #F4 index scan fallback
    上次你加了 index scan 但没处理非等值 join 的 fallback。
```

### 第二层：反馈升级（adaptive）

同一问题多次未解决时自动升级：

| times_raised | escalation_level | 策略 |
|-------------|-----------------|------|
| 1 | 1 | 正常描述问题 |
| 2 | 2 | 更具体：给出期望的修改方向和范围 |
| 3 | 3 | 给出伪代码或代码片段 |
| 4+ | 4 | 标记 CRITICAL，如果 review score < 70 则上报人类 |

升级逻辑在 Engine 中，不依赖 reviewer agent（symbolic 规则）：

```python
def escalate_feedback(self, item: FeedbackItem) -> str:
    if item.times_raised >= 4:
        return f"CRITICAL [第{item.times_raised}次提出，需要人类介入]: {item.description}"
    elif item.times_raised >= 3:
        return f"[第{item.times_raised}次] {item.description}\n请给出具体代码修改。"
    elif item.times_raised >= 2:
        return f"[第{item.times_raised}次] {item.description}\n请明确说明修改方向。"
    return item.description
```

### 第三层：Reviewer 生成对抗性测试

ReviewOutput 扩展：

```python
@dataclass
class TestCase:
    name: str                  # 测试名
    description: str           # 测试什么
    code: str                  # 测试代码（具体语言）
    category: str              # "acceptance" / "edge_case" / "regression"

@dataclass
class ReviewOutput:
    score: int
    comments: list[ReviewComment]     # 新发现的问题
    resolved_item_ids: list[str]      # 哪些旧 feedback items 已解决
    proposed_tests: list[TestCase]    # reviewer 提出的测试
    reviewer: str
    usage: AgentUsage | None = None
```

Reviewer 的 system prompt 扩展：

```
In addition to reviewing, propose 1-3 test cases that would catch
the issues you found. Format as executable test code.
These tests will be added to the acceptance test suite and the
developer MUST make them pass. They cannot be modified or deleted.
```

### 测试文件管理

worktree 中的测试分两层：

```
project/tests/
  acceptance/           # reviewer 提出，只增不删
    round1_q01.rs
    round2_null.rs      # R2 reviewer 提出的 NULL 测试
    round3_deadlock.rs  # R3 reviewer 提出的死锁测试
  unit/                 # developer 写的，可以改
    parser_test.rs
    storage_test.rs
```

Engine 保护 acceptance 测试：

```python
async def _protect_acceptance_tests(self, worktree_path: str,
                                     before_files: set[str]) -> bool:
    """检查 acceptance 测试是否被 developer 篡改。"""
    after_files = set(glob(f"{worktree_path}/tests/acceptance/*"))
    deleted = before_files - after_files
    if deleted:
        self._log(issue.id, f"Developer 删除了 acceptance 测试: {deleted} → 拒绝")
        return False
    # 检查内容是否被修改（git diff）
    for f in before_files:
        # ... check if content changed
    return True
```

### 跨 issue 知识沉淀

Issue DONE 后，系统从 feedback history 提取可复用的 patterns：

```
.shadowcoder/knowledge/
  test_patterns.md      # 通用测试策略
  review_patterns.md    # 常见 review 发现
```

格式：

```markdown
## NULL 语义 [来源: issue #1, #3]
任何 SQL 项目，reviewer 应从第一轮起检查：
- NULL = NULL → UNKNOWN
- NOT IN with NULL → UNKNOWN
- COUNT(*) vs COUNT(col) with NULLs

## 并发事务 [来源: issue #1, #2]
- 至少 2 对写写冲突测试
- 死锁检测验证 victim 选择
```

**不自动积累**——issue DONE 时系统提议 patterns，人类 review 后决定保留哪些。
新 issue 开始时，知识库内容注入 reviewer prompt。

## 数据存储

FeedbackItem 存在 issue 的 `.log.md` 和一个结构化文件中：

```
.shadowcoder/issues/
  0001.md
  0001.log.md
  0001.versions/
  0001.feedback.json    # 新增：结构化的 feedback items
```

```json
{
  "items": [
    {
      "id": "F1",
      "category": "error_handling",
      "description": "NULL three-valued logic missing",
      "round_introduced": 1,
      "times_raised": 3,
      "resolved": true,
      "resolved_round": 3,
      "escalation_level": 3
    }
  ],
  "proposed_tests": [
    {
      "name": "test_null_in_subquery",
      "round_proposed": 2,
      "reviewer": "claude-code",
      "file": "tests/acceptance/round2_null.rs"
    }
  ]
}
```

## 改动范围

| 文件 | 改动 |
|------|------|
| `agents/types.py` | 新增 `FeedbackItem`, `TestCase`; `ReviewOutput` 加 `resolved_item_ids`, `proposed_tests` |
| `core/engine.py` | feedback 跟踪（基于 reviewer 显式标注）、升级逻辑; acceptance 测试保护; 测试注入 |
| `core/issue_store.py` | 新增 `.feedback.json` 读写 |
| `agents/claude_code.py` | reviewer prompt 要求生成测试; 反馈格式化包含升级信息 |
| `tests/` | 更新 |

## 不做的（YAGNI）

- 不做任何匹配算法——reviewer 显式标注 resolved_item_ids，Engine 只做 symbolic 更新
- 不做自动 pattern 提取——人类 review 后手动保存
- 不做 acceptance 测试的自动清理——积累到 20 个测试不算多
- 不改 pipeline 架构——在现有 Engine 方法上做，不搞 Verifier 抽象
