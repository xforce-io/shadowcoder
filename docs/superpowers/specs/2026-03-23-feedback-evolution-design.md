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

#### 语言无关的测试规约

Reviewer 不写测试代码，只描述"测什么"和"期望结果"。Developer 负责实现具体测试代码。

```python
@dataclass
class TestCase:
    name: str                  # 测试名（如 "test_null_in_subquery"）
    description: str           # 测试什么（如 "SELECT 2 IN (1, NULL) 应返回 UNKNOWN"）
    expected_behavior: str     # 期望行为（如 "返回 UNKNOWN，不是 TRUE 或 FALSE"）
    category: str              # "acceptance" / "edge_case" / "regression"
    # 没有 code 字段 —— 语言无关
```

职责分离：

| 角色 | 做什么 | 语言相关？ |
|------|--------|----------|
| Reviewer | 提出测试：测什么、期望结果 | 否 |
| Developer | 写测试代码、让它通过 | 是 |
| Engine | 验证测试名出现在测试输出中且通过 | 否（只看 exit code + test name） |

ReviewOutput 扩展：

```python
@dataclass
class ReviewOutput:
    score: int
    comments: list[ReviewComment]     # 新发现的问题
    resolved_item_ids: list[str]      # 哪些旧 feedback items 已解决
    proposed_tests: list[TestCase]    # reviewer 提出的测试（语言无关规约）
    reviewer: str
    usage: AgentUsage | None = None
```

Reviewer 的 system prompt 扩展：

```
In addition to reviewing, propose 1-3 test cases that would catch
the issues you found. Describe WHAT to test and EXPECTED BEHAVIOR,
not the test code. The developer will implement the actual test.

Format each test as:
  name: a_descriptive_test_name
  description: what this test verifies
  expected_behavior: the correct result or behavior

These tests will be added to the acceptance suite. The developer
MUST implement and pass them. They cannot be removed.
```

#### Developer 实现测试

Developer agent 的 context 中包含 reviewer 提出的 test cases：

```
Acceptance tests to implement (from reviewer):
  - test_null_in_subquery: SELECT 2 IN (1, NULL) 应返回 UNKNOWN
  - test_deadlock_victim: 两个事务互锁时应选择代价最小的回滚

You must write executable tests for each of these.
Place them in the project's existing test directory, following the project's
test conventions and style. Do not create a separate acceptance/ directory.
Do not skip any.
```

#### 测试放在项目原有结构中（不侵入）

Reviewer 提出的 test specs 只存在 `.shadowcoder/issues/0001.feedback.json` 中。
实际测试代码由 developer **按项目约定放到项目的 tests/ 目录里**：

```
repo/
  tests/                    # 项目原有测试（不动）
    test_parser.rs          # 原有
    test_executor.rs        # 原有
    test_null_semantics.rs  # developer 新增（来自 reviewer 规约）
    test_deadlock.rs        # developer 新增（来自 reviewer 规约）
  .shadowcoder/
    issues/
      0001.feedback.json    # reviewer 的 test specs（规约，不是代码）
```

不额外创建 `tests/acceptance/` 目录。新增的测试和原有测试混在一起，
遵守同样的风格和约定。issue DONE 后，这些测试就是项目的一部分。

这对从零开始和成熟 repo 都适用：
- **从零**：tests/ 目录本来就是空的，developer 创建并组织
- **成熟 repo**：tests/ 已有结构和风格，developer 按原有约定添加

#### Engine 验证

Engine 做两层 symbolic 验证：

```python
async def _verify_after_develop(self, worktree_path, proposed_tests):
    # 1. 运行全部测试（原有 + 新增），检查 exit code
    passed, output = await self._verify_tests(worktree_path)
    if not passed:
        return False, "tests failed", output

    # 2. 检查 reviewer 规约的每个 test name 是否出现在测试输出中
    missing = []
    for tc in proposed_tests:
        if tc.name not in output:
            missing.append(tc.name)
    if missing:
        return False, f"acceptance tests not implemented: {missing}", output

    return True, "all tests passed", output
```

- 第一层：exit code = 0（原有测试不回归 + 新测试通过）
- 第二层：每个 acceptance test name 出现在输出中（developer 确实实现了）

缺失测试或回归失败 → 自动回到 develop，不进入 review。

#### 测试只增不减

所有轮次 reviewer 提出的 TestCase 累积存储在 `0001.feedback.json` 中。
每轮 develop 都必须通过全部历史 acceptance tests，不只是最新一轮的。
这是防止 catastrophic forgetting 的 symbolic 约束。

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
- 不做跨 issue 知识沉淀——后续单独设计
- 不做 acceptance 测试的自动清理——积累到 20 个测试不算多
- 不让 reviewer 写测试代码——只写语言无关的规约，developer 实现
- 不改 pipeline 架构——在现有 Engine 方法上做，不搞 Verifier 抽象
