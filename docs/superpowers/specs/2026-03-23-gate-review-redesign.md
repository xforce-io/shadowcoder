# Gate + Review 重新设计

## 问题

1. **Score 不可解释**：reviewer 拍脑袋给 0-100 分，没有依据，不可复现
2. **develop 后无检查直接进 review**：编译不过、测试挂了也去浪费 reviewer token
3. **test 阶段和 review 重复**：都是在验证代码对不对，但时机不同
4. **reviewer 职责不清**：既要检查"能不能跑"又要评价"好不好"

## 方案

### 取消独立 test 阶段

```
之前：design → develop → test → done（3 个阶段）
之后：design → develop ⇄ [gate + review] → done（2 个阶段）
```

Test 变成 develop 循环内的 gate，不再是单独的 stage。

### Gate（symbolic，develop 后 review 前）

```
develop 产出代码
  → Gate
    [1] build 通过（test_command exit code = 0）
    [2] 现有测试全过（包含在 test_command 中）
    [3] acceptance tests 全部实现（test name 出现在输出中）
  → 任一不过 → 直接回 develop（不消耗 review token）
  → 全过 → 进 review
```

Gate 是 Engine 自己跑的，不需要 agent 参与。纯 symbolic。

```python
async def _gate_check(self, issue_id, worktree_path, proposed_tests):
    """Symbolic gate: build + test + acceptance check."""
    # 1+2: run test command
    passed, output = await self._verify_tests(worktree_path)
    if not passed:
        return False, "build/tests failed", output

    # 3: check acceptance test names in output
    missing = []
    for tc in proposed_tests:
        if tc["name"] not in output:
            missing.append(tc["name"])
    if missing:
        return False, f"acceptance tests not implemented: {missing}", output

    return True, "gate passed", output
```

### Review（neural，Gate 过了才到这里）

Reviewer 只做一件事：**评审设计和代码质量**。不再打分。

输出简化：

```json
{
  "comments": [
    {"severity": "high", "message": "...", "location": "..."}
  ],
  "resolved_item_ids": ["F1", "F3"],
  "proposed_tests": [{"name": "...", "description": "...", "expected_behavior": "..."}]
}
```

没有 `passed`，没有 `score`。

### 决策逻辑（Engine，symbolic）

```python
def _review_decision(self, review) -> str:
    """Decide based on comment severity counts. Pure symbolic."""
    critical = sum(1 for c in review.comments if c.severity == Severity.CRITICAL)
    high = sum(1 for c in review.comments if c.severity == Severity.HIGH)

    if critical > 0:
        return "retry"
    if high == 0:
        return "pass"
    if high <= 2:
        return "conditional_pass"
    return "retry"
```

日志可解释：
```
Gate: ✓ build 通过 | ✓ 测试 71/71 | ✓ acceptance 12/12
Review 判定: 带条件通过
  CRITICAL = 0
  HIGH = 2 (≤ 2)
  MEDIUM = 3
  遗留 HIGH issues 记入下一阶段
```

### ReviewOutput 简化

```python
@dataclass
class ReviewOutput:
    comments: list[ReviewComment]
    resolved_item_ids: list[str] = field(default_factory=list)
    proposed_tests: list[TestCase] = field(default_factory=list)
    reviewer: str = ""
    usage: AgentUsage | None = None
    # 删除: passed, score
```

Engine 根据 comments 的 severity 分布做决策，不依赖 reviewer 的主观判断。

### Reviewer Prompt 简化

```
You are a code reviewer. The code has already passed build and all tests.
Focus on design quality, correctness of logic, and potential issues
that tests don't catch.

For each issue, classify severity:
- critical: breaks core functionality, security vulnerability, data corruption
- high: missing required feature, significant logic bug
- medium: code quality, minor missing feature, style
- low: naming, minor improvement

Also check if previously unresolved feedback items are now addressed.
Propose 1-3 new test cases if you find issues worth testing.

Output ONLY JSON:
{
  "comments": [{"severity": "...", "message": "...", "location": "..."}],
  "resolved_item_ids": ["F1", "F3"],
  "proposed_tests": [{"name": "...", "description": "...", "expected_behavior": "..."}]
}
```

不再要求 `passed` 和 `score`。

### 完整 develop 循环

```python
async def _run_develop_cycle(self, issue, task):
    max_rounds = self.config.get_max_review_rounds()
    for round_num in range(1, max_rounds + 1):
        # 1. Agent 写代码
        output = await agent.develop(request)

        # 2. Gate（symbolic）
        gate_ok, gate_msg, gate_output = await self._gate_check(
            issue.id, task.worktree_path, proposed_tests)
        if not gate_ok:
            self._log(issue.id, f"Gate FAIL: {gate_msg}")
            continue  # 回到 develop，不进 review

        # 3. Review（neural）
        review = await reviewer.review(request)
        self._update_feedback(issue.id, review, round_num)

        # 4. 决策（symbolic）
        decision = self._review_decision(review)
        if decision in ("pass", "conditional_pass"):
            return  # done
        # else: retry → 下一轮 develop

    # 超过 max_rounds → BLOCKED
```

### IssueStatus 简化

```
之前: CREATED → DESIGNING → DESIGN_REVIEW → APPROVED → DEVELOPING → DEV_REVIEW → TESTING → DONE
之后: CREATED → DESIGNING → DESIGN_REVIEW → APPROVED → DEVELOPING → DEV_REVIEW → DONE
```

删除 `TESTING`。相应更新 `VALID_TRANSITIONS`。

**注意**：这是一个较大的改动，影响状态机、Engine、所有测试。
可以分步做：先加 gate，再取消 test 阶段。

### 分步实施

**Phase 1**：加 gate（develop 后 review 前检查 build + test + acceptance）
- 改动小，不影响现有流程
- gate 不过直接回 develop

**Phase 2**：取消独立 test 阶段
- 删除 `_on_test`、`CMD_TEST`、`IssueStatus.TESTING`
- develop review 通过即 DONE
- 较大改动，影响所有测试

建议先做 Phase 1 验证效果，再做 Phase 2。

## 改动范围

### Phase 1（gate）
| 文件 | 改动 |
|------|------|
| `core/engine.py` | 新增 `_gate_check`，在 review 前调用 |

### Phase 2（取消 test + score）
| 文件 | 改动 |
|------|------|
| `agents/types.py` | ReviewOutput 删除 `passed`, `score`；删除 `TestOutput` |
| `agents/base.py` | 删除 `test()` 抽象方法 |
| `agents/claude_code.py` | 删除 `test()` 实现，简化 review prompt |
| `core/engine.py` | 删除 `_on_test`，`_run_with_review` 改为 `_run_develop_cycle` 含 gate |
| `core/bus.py` | 删除 `CMD_TEST` |
| `core/models.py` | 删除 `IssueStatus.TESTING`，更新 `VALID_TRANSITIONS` |
| `cli/tui/app.py` | 删除 `test` 命令 |
| `tests/` | 全量更新 |
