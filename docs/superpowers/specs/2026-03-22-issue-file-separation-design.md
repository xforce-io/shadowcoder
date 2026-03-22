# Issue 文件格式分离设计

## 问题

Issue 的 `.md` 文件混合了三类信息：
1. 元数据（frontmatter）和关键产出（需求、设计、开发步骤、测试）—— 当前状态
2. 航海日志 —— 不断增长的时间线
3. 完整 review 历史 —— 每轮 review 的详细内容

真实运行中 issue 文件超过 100KB，且日志和产出混在一起难以阅读。
Agent 构建 context 时被迫加载全部内容（含历史 review），浪费 context window。

## 方案

双文件分离：

```
.shadowcoder/issues/
  0001.md        # frontmatter + 关键 sections
  0001.log.md    # 航海日志 + 完整 review 历史
```

### 0001.md（当前状态快照）

```markdown
---
id: 1
title: SQL Database Engine
status: approved
priority: high
created: 2026-03-22T11:29:04
updated: 2026-03-22T15:36:23
tags: [database, sql]
assignee: claude-code
---

<!-- section: 需求 -->
（用户需求，不变）

<!-- section: 设计 -->
（最新版设计文档，覆盖写入）

<!-- section: Design Review -->
（最后一次 review 的摘要：PASSED/NOT PASSED + comment 数量）

<!-- section: 开发步骤 -->
（最新版实现摘要，覆盖写入）

<!-- section: Dev Review -->
（最后一次 review 摘要）

<!-- section: 测试 -->
（最新测试结果）
```

### 0001.log.md（完整时间线，只追加）

```markdown
## [2026-03-22 11:29:04] Issue 创建: SQL Database Engine

## [2026-03-22 11:29:04] Design R1 开始

## [2026-03-22 11:37:31] Design R1 Agent 产出
内容长度: 56147 字符

## [2026-03-22 11:40:32] Design Review R1 — NOT PASSED
**Reviewer: claude-code** — NOT PASSED
- [HIGH] Missing error handling...
- [HIGH] Transaction timeout...
（完整 review 内容）

## [2026-03-22 11:40:32] Design R2 开始
...
```

## 改动

### IssueStore

```python
class IssueStore:
    def _log_path(self, issue_id: int) -> Path:
        return self.base / f"{issue_id:04d}.log.md"

    def append_log(self, issue_id: int, entry: str) -> None:
        """追加到 .log.md（不读写 .md 主文件）"""
        path = self._log_path(issue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"\n\n## [{ts}] {entry}"
        with open(path, "a", encoding="utf-8") as f:
            f.write(log_entry)

    def append_review(self, issue_id: int, section: str, review: ReviewOutput) -> None:
        """review 摘要写入 .md，完整内容写入 .log.md"""
        # .md: 只保留最后一次 review 的摘要
        summary = f"{'PASSED' if review.passed else 'NOT PASSED'} ({len(review.comments)} comments)"
        issue = self.get(issue_id)
        issue.sections[section] = summary
        self._save(issue)

        # .log.md: 追加完整 review 内容
        formatted = self._format_review(review)
        self.append_log(issue_id, f"{section}\n{formatted}")

    def get_log(self, issue_id: int) -> str:
        """读取 log 文件内容（按需加载，不在 get() 中自动加载）"""
        path = self._log_path(issue_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
```

关键变化：
- `append_log` **不再读写 .md 主文件**，直接 append 到 `.log.md`（性能大幅提升）
- `append_review` 写两个地方：`.md`（摘要）和 `.log.md`（完整内容）
- `get()` **不加载 log**——agent 读 issue 时只拿到当前状态
- 新增 `get_log()` 按需加载日志

### Issue 数据模型

`Issue.sections` 不再包含 `航海日志` key。日志独立于 issue 对象。

### Engine

`self._log(issue.id, entry)` 调用 `issue_store.append_log()`，和之前一样，
但现在写入的是 `.log.md` 而不是 `.md` 中的 section。

### 向后兼容

- 旧格式（航海日志在 .md 中）的 issue 文件仍可读取
- `get()` 读到 sections 中有 `航海日志` key 时不报错，只是不再写入
- 不做自动迁移（旧 issue 保持原样，新 issue 用新格式）

## 改动范围

| 文件 | 改动 |
|------|------|
| `core/issue_store.py` | `append_log` 改写为直接 append 文件；`append_review` 拆分写入；新增 `get_log()` |
| `core/engine.py` | 无改动（`self._log()` 调用 `issue_store.append_log()`，接口不变） |
| `scripts/run_real.py` | `get_log()` 读取日志展示 |
| `tests/` | 更新相关测试 |
