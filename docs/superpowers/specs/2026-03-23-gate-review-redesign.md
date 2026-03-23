# Gate + Review 重新设计

## 问题

1. **Score 不可解释**：reviewer 拍脑袋给 0-100 分，没有依据，不可复现
2. **develop 后无检查直接进 review**：编译不过、测试挂了也去浪费 reviewer token
3. **test 阶段和 review 重复**：都是在验证代码对不对，但时机不同
4. **reviewer 职责不清**：既要检查"能不能跑"又要评价"好不好"
5. **reviewer 看不到实际代码变更**：只看 agent 的文字摘要

## 方案

### 取消独立 test 阶段

```
之前：design → develop → test → done（3 个阶段）
之后：design → develop ⇄ [gate + review] → done（2 个阶段）
```

Test 变成 develop 循环内的 gate，不再是单独的 stage。

### Benchmark 也是 acceptance test

Design reviewer 看到 requirements 里有量化指标（如"胜率 > 90%"），会提出 benchmark 类型的 TestCase spec：

```json
{
  "name": "test_vs_baseline_100_games",
  "description": "AI vs baseline 对战 100 局",
  "expected_behavior": "胜率 > 90%，失败时输出关键失误原因"
}
```

Developer 实现这个测试（含 benchmark 逻辑和 assert）。Gate 跑它，exit code 判定。
Benchmark 不是特殊概念——就是一种 acceptance test，走完全相同的流程。

### Gate（symbolic，develop 后）

```
develop 产出代码
  → Gate
    [1] build + 测试通过（test_command exit code = 0）
    [2] acceptance tests 全部实现（test name 出现在输出中）
  → 不过 → gate 输出给 developer，自修一次
    → 再不过 → 升级给 reviewer 分析原因
  → 过了 → 进 review
```

Gate 是 Engine 自己跑的，不需要 agent 参与。
判定依据是 test command 的 **exit code**（0=通过，非0=失败），不做输出内容解析。

#### Gate 失败升级机制

Gate 失败时，developer 先自己看 gate 输出修一次。连续修不好才请 reviewer：

```
Gate FAIL (第1次) → gate 输出给 developer，自修
Gate FAIL (第2次，同类) → 升级给 reviewer 分析
```

和 feedback 升级机制一致——先自修，不行再请人。

#### Test command 来源

优先级从高到低：
1. config 里配了 `build.test_command` → 用配置的
2. 没配 → 自动检测项目类型：

```python
async def _detect_test_command(self, worktree_path: str) -> str:
    p = Path(worktree_path)
    if (p / "Cargo.toml").exists():  return "cargo test 2>&1"
    if (p / "go.mod").exists():      return "go test ./... 2>&1"
    if (p / "package.json").exists(): return "npm test 2>&1"
    if (p / "pyproject.toml").exists() or (p / "setup.py").exists():
                                      return "python -m pytest -v 2>&1"
    if (p / "Makefile").exists():     return "make test 2>&1"
    raise RuntimeError(
        f"Cannot detect test command for {worktree_path}. "
        f"Set build.test_command in ~/.shadowcoder/config.yaml")
```

**检测不到 → 报错退出**，不跳过。没有 gate 的研发过程不可接受。

#### Gate 实现

```python
async def _gate_check(self, issue_id, worktree_path, proposed_tests):
    """Symbolic gate: build + test + acceptance check."""
    test_cmd = self.config.get_test_command()
    if not test_cmd:
        test_cmd = await self._detect_test_command(worktree_path)

    # Run test command, check exit code
    passed, output = await self._run_command(test_cmd, cwd=worktree_path)
    if not passed:
        return False, "build/tests failed", output

    # Check acceptance test names in output
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

#### Review 基于 git diff

Reviewer 看的是 **实际代码变更**，不是 agent 的文字摘要：

```python
async def _get_code_diff(self, worktree_path: str) -> str:
    """Get git diff + untracked file contents."""
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "HEAD", cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    diff = stdout.decode("utf-8", errors="replace")

    proc2 = await asyncio.create_subprocess_exec(
        "git", "ls-files", "--others", "--exclude-standard",
        cwd=worktree_path, stdout=asyncio.subprocess.PIPE)
    stdout2, _ = await proc2.communicate()
    for fpath in stdout2.decode().strip().splitlines():
        full = Path(worktree_path) / fpath
        if full.exists() and full.stat().st_size < 50000:
            diff += f"\n\n=== NEW FILE: {fpath} ===\n{full.read_text(errors='replace')}"
    return diff
```

Reviewer prompt：
```
You are reviewing a code change (git diff provided below).
The code has already passed build and all tests.
Focus on: logic correctness, design quality, potential issues that tests don't catch.
Review the DIFF, not the full codebase. Flag issues specific to this change.
```

#### Review 输出

```json
{
  "comments": [{"severity": "high", "message": "...", "location": "file.py:42"}],
  "resolved_item_ids": ["F1", "F3"],
  "proposed_tests": [{"name": "...", "description": "...", "expected_behavior": "..."}]
}
```

没有 `passed`，没有 `score`。

### 决策逻辑（Engine，symbolic）

```python
def _review_decision(self, review) -> str:
    critical = sum(1 for c in review.comments if c.severity == Severity.CRITICAL)
    high = sum(1 for c in review.comments if c.severity == Severity.HIGH)
    if critical > 0: return "retry"
    if high == 0: return "pass"
    if high <= 2: return "conditional_pass"
    return "retry"
```

日志可解释：
```
Gate: ✓ build 通过 | ✓ 测试 71/71 | ✓ acceptance 12/12
Review 判定: 带条件通过
  CRITICAL = 0, HIGH = 2 (≤ 2), MEDIUM = 3
```

### Benchmark 作为 acceptance test 的完整链路

```
用户 requirements: "AI vs baseline 胜率 > 90%"
  ↓
Design Reviewer: 提出 TestCase spec
  name: test_vs_baseline_100_games
  description: AI vs baseline 100 局
  expected_behavior: 胜率 > 90%，失败时输出关键失误原因
  → 存入 feedback.json
  ↓
Developer: 读到 spec，写测试代码（含 benchmark 逻辑 + assert）
  def test_vs_baseline_100_games():
      wins = play_100_games(ai, baseline)
      assert wins/100 >= 0.90, f"win_rate={wins}%, details: ..."
  → 放在项目的 test 目录里，遵守项目约定
  ↓
Gate: 跑 test command → exit code 判定
  exit code ≠ 0（胜率不够）→ 输出给 developer 自修
  exit code = 0（胜率达标）→ 进 review
  ↓
Review: 审代码质量（和其他 acceptance test 一样的流程）
```

Developer 写的测试位置由项目约定决定（Go: *_test.go, Rust: tests/, Python: tests/ 等）。
不侵入项目结构。

### 完整 develop 循环

```python
async def _run_develop_cycle(self, issue, task):
    max_rounds = self.config.get_max_review_rounds()
    gate_fail_count = 0  # 连续 gate 失败计数

    for round_num in range(1, max_rounds + 1):
        # 1. Agent 写代码
        output = await agent.develop(request)

        # 2. Gate（symbolic）
        gate_ok, gate_msg, gate_output = await self._gate_check(
            issue.id, task.worktree_path, proposed_tests)
        if not gate_ok:
            gate_fail_count += 1
            self._log(issue.id, f"Gate FAIL ({gate_fail_count}): {gate_msg}")
            if gate_fail_count >= 2:
                # 升级：给 reviewer 分析 gate 失败原因
                # reviewer 看 gate_output，给改进建议
                ...
                gate_fail_count = 0
            continue  # 回到 develop

        gate_fail_count = 0  # 重置

        # 3. Review（neural，基于 git diff）
        code_diff = await self._get_code_diff(task.worktree_path)
        review = await reviewer.review(request_with_diff)
        self._update_feedback(issue.id, review, round_num)

        # 4. 决策（symbolic）
        decision = self._review_decision(review)
        if decision in ("pass", "conditional_pass"):
            → DONE
            return
        # else: retry → 下一轮 develop

    # 超过 max_rounds → BLOCKED
```

### IssueStatus 简化（已实现）

```
CREATED → DESIGNING → DESIGN_REVIEW → APPROVED → DEVELOPING → DEV_REVIEW → DONE
```

`TESTING` 已删除。

## 改动范围（已实现部分标注）

| 文件 | 改动 | 状态 |
|------|------|------|
| `agents/types.py` | 删除 TestOutput, score | ✅ 已实现 |
| `agents/base.py` | 删除 test() | ✅ 已实现 |
| `agents/claude_code.py` | 删除 test(), 简化 review prompt | ✅ 已实现 |
| `core/engine.py` | gate_check, detect_test_command, get_code_diff, review_decision, _on_run | ✅ 已实现 |
| `core/bus.py` | 删除 CMD_TEST, 新增 CMD_RUN | ✅ 已实现 |
| `core/models.py` | 删除 TESTING | ✅ 已实现 |
| `core/engine.py` | gate 失败升级机制 | 待实现 |
| `core/engine.py` | review 基于 git diff | 待实现 |
