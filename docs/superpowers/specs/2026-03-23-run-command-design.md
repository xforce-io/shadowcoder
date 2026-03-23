# Run 命令设计

## 问题

当前每个阶段需要手动触发：
```bash
python scripts/run_real.py ... create "task" --from requirements.md
python scripts/run_real.py ... design 1
python scripts/run_real.py ... develop 1
python scripts/run_real.py ... test 1
```

实际运行中（SQL 引擎、五子棋 AI），全部由对话中的 Claude 手动串接。
ShadowCoder 自主驱动的只有每个命令内部的循环，命令间的衔接完全靠人。

## 方案

### `run` 命令

一条命令走完整个流程：create → preflight → design ⇄ review → develop ⇄ review → test → done。

```bash
# 创建并跑完
python scripts/run_real.py /path/to/repo run "五子棋 AI" --from requirements.md

# 对已有 issue 跑完
python scripts/run_real.py /path/to/repo run 5
```

### Engine 实现

```python
async def _on_run(self, msg):
    issue_id = msg.payload["issue_id"]

    # 如果传了 title，先 create
    if "title" in msg.payload:
        await self._on_create(msg)
        issues = self.issue_store.list_all()
        issue_id = issues[-1].id

    # design（含 preflight）
    issue = self.issue_store.get(issue_id)
    if issue.status in (IssueStatus.CREATED, IssueStatus.BLOCKED, IssueStatus.FAILED):
        await self._on_design(Message(MessageType.CMD_DESIGN, {"issue_id": issue_id}))
        issue = self.issue_store.get(issue_id)
        if issue.status == IssueStatus.BLOCKED:
            self._log(issue_id, "run 暂停: design BLOCKED，需人类介入")
            return
        if issue.status != IssueStatus.APPROVED:
            self._log(issue_id, f"run 停止: design 后 status={issue.status.value}")
            return

    # develop
    if issue.status == IssueStatus.APPROVED:
        await self._on_develop(Message(MessageType.CMD_DEVELOP, {"issue_id": issue_id}))
        issue = self.issue_store.get(issue_id)
        if issue.status == IssueStatus.BLOCKED:
            self._log(issue_id, "run 暂停: develop BLOCKED，需人类介入")
            return
        if issue.status != IssueStatus.TESTING:
            self._log(issue_id, f"run 停止: develop 后 status={issue.status.value}")
            return

    # test（含自动路由 develop/design）
    if issue.status == IssueStatus.TESTING:
        await self._on_test(Message(MessageType.CMD_TEST, {"issue_id": issue_id}))

    # 最终状态
    issue = self.issue_store.get(issue_id)
    self._log(issue_id, f"run 结束: status={issue.status.value}")
```

### 状态感知

`run` 是幂等的——检查 issue 当前状态，从该阶段继续。中断后重跑 `run` 不会重复已完成的阶段：
- status=CREATED → 从 design 开始
- status=APPROVED → 跳过 design，从 develop 开始
- status=TESTING → 跳过 design+develop，从 test 开始
- status=DONE → 什么都不做
- status=BLOCKED/FAILED → 尝试从当前阶段恢复

### MessageBus

```python
CMD_RUN = "cmd.run"
```

### TUI

```
run #id
run "task title" --from requirements.md
```

### scripts/run_real.py

```python
elif command == "run":
    if args and args[0].isdigit():
        # run existing issue
        payload = {"issue_id": int(args[0])}
    else:
        # create + run
        title_parts = []
        description = None
        i = 0
        while i < len(args):
            if args[i] == "--from" and i + 1 < len(args):
                description = str(Path(repo_path) / args[i + 1])
                i += 2
            else:
                title_parts.append(args[i])
                i += 1
        payload = {"title": " ".join(title_parts)}
        if description:
            payload["description"] = description
    await bus.publish(Message(MessageType.CMD_RUN, payload))
```

## 未来：Skill 层

`run` 命令是 skill 层的基础。未来作为 Claude Code skill：

```
用户: /shadowcoder "实现五子棋 AI" --from requirements.md

Skill 内部:
  1. 调用 ShadowCoder Engine 的 run 命令
  2. 监听事件流，实时输出进度
  3. BLOCKED 时向用户请求决策（approve/resume/cancel）
  4. DONE 时报告结果
```

Skill 层不改 Engine 逻辑，只加一个交互外壳。

## 未来：并行 run

```python
# 多 issue 并行
await asyncio.gather(
    run(issue_1),
    run(issue_2),
    run(issue_3),
)
```

架构已支持（per-issue worktree + per-issue state），限制在 API 并发和成本。
并行版不在本次 scope 内，`run` 先做串行。

## 改动范围

| 文件 | 改动 |
|------|------|
| `core/engine.py` | 新增 `_on_run`，订阅 `CMD_RUN` |
| `core/bus.py` | 新增 `CMD_RUN` |
| `cli/tui/app.py` | 新增 `run` 命令解析 |
| `scripts/run_real.py` | 新增 `run` 命令处理 |
| `tests/core/test_engine.py` | 新增 `test_run_full_lifecycle` |

## 不做的

- 不做并行 run（先串行）
- 不做 skill 层（单独设计）
- 不做 BLOCKED 时自动 approve（人类决策）
