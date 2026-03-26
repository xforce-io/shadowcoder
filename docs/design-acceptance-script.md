# Design: Acceptance Script — 可证伪的验收断言

## 问题

当前流程中，designer 定义的 acceptance criteria 只是自然语言，没有被系统验证。导致：
1. 开发前没有明确的"什么算完"的可执行定义
2. Bugfix 场景下，没有证明问题被复现就开始修
3. Acceptance criteria 可能太弱——描述的行为本来就是 pass 的

## 方案

在 develop 之前，由一个独立角色（acceptance_writer）根据设计文档生成一个 **shell 脚本**，作为可执行的验收断言。这个脚本：

- develop 前必须 **FAIL**（红）——证明问题存在或功能缺失
- develop 后必须 **PASS**（绿）——证明问题已解决或功能已实现

```
design → review → [acceptance_writer 写脚本 → pre-gate 必须 FAIL] → develop → [验收 必须 PASS] → gate → review
```

## 核心概念

### Acceptance script vs Test suite

| | Acceptance script | Test suite |
|---|---|---|
| 目的 | 这个 issue 解决了吗 | 整个项目没坏吧 |
| 生命周期 | 随 issue 创建，done 后可丢弃 | 持续积累，永久保留 |
| 谁写 | acceptance_writer agent | developer agent |
| 存在哪 | `.shadowcoder/issues/NNNN.acceptance.sh` | 项目的 `tests/` 目录 |
| 形式 | shell 脚本，几行断言 | pytest/go test 等框架代码 |
| 什么时候跑 | pre-gate + post-develop | gate |

两者独立，互不干扰。

### Acceptance script 格式

纯 shell 脚本，在 worktree 目录下执行。每个断言是一行命令，非零退出码 = 失败。

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Bugfix: division by zero should output ERROR, not crash
result=$(echo "2 / 0" | python calc.py 2>&1)
test "$result" = "ERROR"

# Feature: parentheses should work
result=$(echo "(2 + 3) * 4" | python calc.py 2>&1)
test "$result" = "20"
```

脚本由 acceptance_writer agent 生成，不需要任何测试框架，只依赖 shell。

## 详细流程

### 1. Acceptance writer

在 design review 通过后、develop 开始前执行。

**输入**：设计文档（含 acceptance criteria）+ worktree 路径

**行为**：
- 读取设计文档中的 goal & acceptance criteria
- 在 worktree 中探索现有代码（理解项目结构、入口点）
- 生成 acceptance script

**输出**：shell 脚本，写入 `.shadowcoder/issues/NNNN.acceptance.sh`

**角色定义**：新增 `data/roles/acceptance_writer/instructions.md`。

核心人格：**防御性体质——你的职责是证伪，不是配合**。

- 只写验收断言，不写实现代码
- 用最简单的 shell 命令验证行为（test、grep、diff、curl 等）
- 每个 acceptance criterion 对应至少一个断言
- 脚本必须可独立运行（`bash NNNN.acceptance.sh`）
- **你的脚本必须在当前代码上 FAIL。如果你写的断言在改动前就能 PASS，说明你没有抓到真正的问题。这是你的失职，不是成功。**
- **不要写永远会 fail 的无意义断言（如 `test 1 = 0`）来绕过检查。每个断言必须对应一个具体的、可通过正确实现来满足的行为。**
- **如果 pre-gate 告诉你脚本已经 PASS，你必须分析为什么——是断言太宽松？是测的不是真正的问题？找到真正的 gap 并加强断言，而不是换一种方式写同样弱的测试。**

### 2. Pre-gate（验证脚本是 failing 的）

执行 `bash .shadowcoder/issues/NNNN.acceptance.sh`，在 worktree cwd 下运行。

- **脚本不存在**：跳过 pre-gate，正常继续（兼容无 acceptance criteria 的场景）
- **脚本执行失败（非零退出码）**：正确，说明问题存在/功能缺失，继续 develop
- **脚本执行成功（零退出码）**：**BLOCKED** — criteria 太弱，或问题已解决

如果 PASS，自动重试（最多 2 轮）：
- 将脚本内容 + 执行输出（stdout/stderr）反馈给 acceptance_writer
- 明确告知："你的脚本在未改动的代码上通过了，这意味着你的断言没有测到真正的问题。分析原因，写出更强的断言。"
- acceptance_writer 必须分析 gap 并加强脚本，不能简单换个写法

2 轮后仍 PASS → BLOCKED，人类介入（resume/approve）

### 3. Post-develop 验收

develop 完成后、跑 gate（test_command）之前，再执行一次 acceptance script。

- **PASS**：验收通过，继续跑 gate
- **FAIL**：验收未通过，等同于 gate fail，反馈给 developer 重试

### 4. Gate（不变）

Gate 继续跑 `test_command`，检查完整测试套件。和 acceptance script 无关。

## 文件变更

### 新增

| 文件 | 说明 |
|------|------|
| `data/roles/acceptance_writer/instructions.md` | acceptance writer 角色指令 |
| `tests/core/test_acceptance_script.py` | 单元测试 |

### 修改

| 文件 | 说明 |
|------|------|
| `src/shadowcoder/core/engine.py` | `_run_develop_cycle` 中加入 acceptance 写入 + pre-gate + post-develop 验收 |
| `src/shadowcoder/agents/base.py` | 新增 `write_acceptance_script()` action 方法 |
| `src/shadowcoder/agents/types.py` | 新增 `AcceptanceOutput` 类型 |
| `src/shadowcoder/core/config.py` | dispatch 新增 `acceptance` phase（可选） |

### 不变

| 文件 | 说明 |
|------|------|
| `src/shadowcoder/core/issue_store.py` | acceptance script 存为 `NNNN.acceptance.sh`，复用现有路径约定 |
| `src/shadowcoder/agents/claude_code.py` | 不变，`_run()` 通用 |
| `src/shadowcoder/agents/codex.py` | 不变 |

## Engine 伪代码

```python
async def _run_develop_cycle(self, issue, task):
    # --- Phase 0: Write acceptance script ---
    acceptance_path = self._acceptance_script_path(issue.id)

    if not acceptance_path.exists():
        agent = self.agents.get(self.config.get_agent_for_phase("acceptance"))
        script_output = await agent.write_acceptance_script(request)
        acceptance_path.write_text(script_output.script)

    # --- Phase 1: Pre-gate (must FAIL) ---
    for attempt in range(3):  # initial + 2 retries
        if not acceptance_path.exists():
            break
        passed, output = await self._run_command(
            f"bash {acceptance_path}", cwd=task.worktree_path)
        if not passed:
            self._log(issue.id, "Pre-gate: acceptance script FAIL (expected) ✓")
            break
        if attempt < 2:
            # Feedback to acceptance_writer: your script passed, strengthen it
            self._log(issue.id,
                f"Pre-gate: acceptance script PASS — too weak (attempt {attempt+1}/3)")
            request.context["pre_gate_failure"] = (
                f"Your acceptance script PASSED on unchanged code. "
                f"This means your assertions don't test the actual problem.\n\n"
                f"Script content:\n{acceptance_path.read_text()}\n\n"
                f"Execution output:\n{output}\n\n"
                f"Analyze WHY it passed. Write stronger assertions that "
                f"will FAIL until the fix/feature is implemented.")
            script_output = await agent.write_acceptance_script(request)
            acceptance_path.write_text(script_output.script)
        else:
            # 3 attempts exhausted → BLOCKED
            self._log(issue.id,
                "Pre-gate: acceptance script still PASS after 3 attempts → BLOCKED")
            # ... transition to BLOCKED, return
            return

    # --- Phase 2: Develop loop (existing logic) ---
    for round_num in range(1, max_rounds + 1):
        # ... existing develop logic ...

        # --- Phase 3: Post-develop acceptance check ---
        if acceptance_path.exists():
            passed, output = await self._run_command(
                f"bash {acceptance_path}", cwd=task.worktree_path)
            if not passed:
                # Acceptance not met — treat like gate fail, retry develop
                ...
                continue

        # --- Phase 4: Gate (existing logic, unchanged) ---
        gate_passed, reason, gate_output = await self._gate_check(...)
        # ... rest of existing logic ...
```

## Config 扩展

```yaml
dispatch:
  design: opus
  develop: sonnet
  acceptance: sonnet          # 新增：acceptance writer 用哪个 agent
  design_review: [opus]
  develop_review: [opus]
```

不配置时，fallback 到 develop 用的 agent。

## 边界情况

| 场景 | 行为 |
|------|------|
| 设计中没有 acceptance criteria | acceptance_writer 根据需求自行推导，或输出空脚本 |
| 空脚本（无断言） | 跳过 pre-gate，等同于没有 acceptance script |
| 新项目，worktree 为空 | 脚本大概率 fail（命令找不到等），符合预期 |
| 脚本语法错误 | 非零退出码，和 fail 一样处理 |
| Pre-gate fail 但原因是环境问题不是业务问题 | 不区分——只要 fail 就继续。develop 阶段修完后 acceptance 会 pass |
| Acceptance passes 但 gate fails | 说明修复引入了回归，developer 需要同时满足两者 |
| 用户 approve blocked issue | 跳过 pre-gate 检查，直接进 develop |

## 测试策略

1. **acceptance_writer agent 测试**：mock `_run()`，验证输出的 `AcceptanceOutput` 格式正确
2. **pre-gate 测试**：用 tmp_path 创建脚本，验证 pass/fail/不存在三种情况
3. **post-develop 验收测试**：验证 fail 时重试 develop，pass 时继续 gate
4. **集成测试**：完整 design → acceptance → develop → gate 流程
5. **边界测试**：空脚本、语法错误、无 acceptance criteria

```yaml
test_command: "python -m pytest tests/ -v"
```
