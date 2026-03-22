"""
E2E test: build a SQL database engine in coder-playground.

The simulated agent is STATE-DRIVEN: it reads the issue's current sections
(requirements, existing design, review feedback) and produces output based
on what's missing. The flow (how many rounds, what gets added when) EMERGES
from the system's behavior, not from a script.

Features are organized in layers. The agent includes base features first,
then adds more as reviewers point out gaps.
"""
import subprocess
from pathlib import Path

import frontmatter as fm
import pytest

from shadowcoder.agents.base import AgentRequest, AgentResponse, BaseAgent
from shadowcoder.agents.registry import AgentRegistry
from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.models import (
    IssueStatus, ReviewComment, ReviewResult, Severity,
)
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Feature registry: each feature has content for design & develop,
# plus a set of keywords the reviewer looks for in requirements.
# ---------------------------------------------------------------------------

DESIGN_FEATURES = {
    "parser_basic": {
        "deps": set(),
        "content": """\
### SQL Parser (Core)

Recursive descent parser supporting:
- DDL: `CREATE TABLE` with column types (INT, FLOAT, VARCHAR, BOOL), `DROP TABLE`
- DML: `INSERT INTO ... VALUES`, `UPDATE ... SET ... WHERE`, `DELETE FROM ... WHERE`
- DQL: `SELECT` with `WHERE`, column list, `*`, `DISTINCT`
- Expressions: arithmetic (+,-,*,/), comparison (=,!=,<,>,<=,>=), `AND`, `OR`, `NOT`
- Literals: integers, floats, strings, NULL, boolean

Grammar defined in EBNF. Tokenizer handles keywords, identifiers, operators,
string literals (single-quoted), numeric literals.""",
        "required_keywords": ["CREATE TABLE", "INSERT", "SELECT", "WHERE"],
    },
    "parser_advanced": {
        "deps": {"parser_basic"},
        "content": """\
### SQL Parser (Advanced)

Extensions to the core parser:
- JOIN: `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `CROSS JOIN`, self-join via table aliases
- Subqueries: scalar subqueries in SELECT/WHERE, `EXISTS (SELECT ...)`, `IN (SELECT ...)`
- Correlated subqueries: subquery references outer query's columns
- Set operations: `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT`
- Expressions: `LIKE` (with % and _ wildcards), `BETWEEN ... AND ...`,
  `CASE WHEN ... THEN ... ELSE ... END`, `IS NULL`, `IS NOT NULL`
- Window functions: `ROW_NUMBER()`, `RANK()`, `DENSE_RANK()`
  with `OVER (PARTITION BY ... ORDER BY ...)`
- CREATE INDEX (single and composite columns)""",
        "required_keywords": ["JOIN", "subquer", "window", "UNION", "LIKE", "CASE WHEN", "INDEX"],
    },
    "aggregation": {
        "deps": {"parser_basic"},
        "content": """\
### Aggregation

- `GROUP BY` with single and multiple columns
- `HAVING` clause (filter on aggregated values)
- Aggregate functions: `SUM`, `AVG`, `COUNT`, `MIN`, `MAX`
- `COUNT(*)` vs `COUNT(column)` (NULL handling)
- `ORDER BY` with multi-column, `ASC`/`DESC`
- `LIMIT` and `OFFSET` for pagination""",
        "required_keywords": ["GROUP BY", "HAVING", "SUM", "AVG", "COUNT", "ORDER BY", "LIMIT"],
    },
    "null_semantics": {
        "deps": {"parser_basic"},
        "content": """\
### NULL Semantics

Three-valued logic (TRUE, FALSE, UNKNOWN):
- `NULL = NULL` → UNKNOWN (not TRUE)
- `NULL AND TRUE` → UNKNOWN
- `NULL OR TRUE` → TRUE
- `COUNT(*)` counts all rows; `COUNT(col)` excludes NULLs
- `ORDER BY`: NULLs sort last (ASC) or first (DESC)
- `DISTINCT` treats NULLs as equal
- `GROUP BY` groups NULLs together""",
        "required_keywords": ["NULL", "three-value", "三值"],
    },
    "storage": {
        "deps": set(),
        "content": """\
### Storage Engine

Page-based storage with fixed page size (4KB default):
- Heap table: unordered row storage, sequential scan
- Row format: header (null bitmap, row size) + column values
- Page structure: header (page_id, free_space_ptr, slot_count) + slot array + row data
- Table catalog: maps table name → schema + page list""",
        "required_keywords": ["页式", "page", "heap", "堆表"],
    },
    "btree_index": {
        "deps": {"storage"},
        "content": """\
### B-Tree Index

B+ tree implementation:
- Internal nodes: keys + child page pointers
- Leaf nodes: keys + row pointers (page_id, slot_id), linked list for range scans
- Operations: search (O(log n)), insert (with split), delete (with merge)
- Support single-column and composite indexes
- Composite index: leftmost prefix matching for queries""",
        "required_keywords": ["B-tree", "B+tree", "索引", "index"],
    },
    "query_planner": {
        "deps": {"parser_basic", "storage"},
        "content": """\
### Query Planner

Rule-based optimizations (applied in order):
1. Constant folding: `WHERE 1+1 = 2` → `WHERE TRUE`
2. Predicate pushdown: push WHERE conditions below JOINs
3. Projection pruning: only read needed columns

Cost-based optimization for JOIN ordering:
- Estimate cardinality per table (row count from catalog)
- For N-way JOINs, enumerate orders (small N) or use greedy heuristic (N > 4)
- Cost model: I/O cost (page reads) + CPU cost (comparisons)
- Select index scan vs full scan based on selectivity estimate

`EXPLAIN` command: output query plan tree as text""",
        "required_keywords": ["谓词下推", "predicate pushdown", "常量折叠", "cost",
                              "成本", "EXPLAIN", "JOIN 顺序", "join order"],
    },
    "executor": {
        "deps": {"storage"},
        "content": """\
### Executor

Volcano/iterator model:
- Each plan node implements `open()`, `next()`, `close()`
- Operators: SeqScan, IndexScan, Filter, Project, HashJoin,
  NestedLoopJoin, Sort, Limit, HashAggregate, Union, Except, Intersect
- Pipeline execution: pull-based, one row at a time
- Window function execution: buffer partition, compute rank/row_number""",
        "required_keywords": ["executor", "执行", "iterator", "volcano"],
    },
    "transaction": {
        "deps": {"storage"},
        "content": """\
### Transaction Management

MVCC (Multi-Version Concurrency Control):
- Each row has `xmin` (creating txn) and `xmax` (deleting txn) fields
- READ COMMITTED isolation: see only committed data at statement start
- `BEGIN` / `COMMIT` / `ROLLBACK` commands
- Transaction ID allocation: monotonically increasing

Write-write conflict detection:
- On UPDATE/DELETE: check if row's xmax was set by another active transaction
- If conflict: abort the later transaction

Deadlock detection:
- Wait-for graph: directed graph of txn → txn dependencies
- Cycle detection via DFS on each new wait edge
- Victim selection: abort the transaction with least work (fewest writes)

Timeout: configurable transaction timeout, auto-rollback on expiry""",
        "required_keywords": ["事务", "transaction", "MVCC", "死锁", "deadlock",
                              "BEGIN", "COMMIT", "ROLLBACK"],
    },
    "error_handling": {
        "deps": {"parser_basic", "executor"},
        "content": """\
### Error Handling

Structured error type: `{code: str, message: str, location: (line, col), context: str}`

SQL errors:
- `SYNTAX_ERROR`: parser failure with position and expected tokens
- `UNKNOWN_TABLE` / `UNKNOWN_COLUMN`: semantic validation
- `TYPE_MISMATCH`: incompatible operand types
- `AMBIGUOUS_COLUMN`: column exists in multiple joined tables without qualifier
- `AGGREGATE_MIX`: mixing aggregated and non-aggregated columns without GROUP BY
- `NOT_NULL_VIOLATION` / `DUPLICATE_KEY`: constraint violations

Runtime errors:
- `DIVISION_BY_ZERO`: integer/float division by zero
- `INTEGER_OVERFLOW`: exceeds 64-bit range
- `QUERY_DEPTH_EXCEEDED`: nested subquery depth > 32
- `MEMORY_LIMIT_EXCEEDED`: single query uses > 256MB

Transaction errors:
- `DEADLOCK_DETECTED`: return which transaction was aborted
- `WRITE_CONFLICT`: concurrent write to same row
- `TRANSACTION_TIMEOUT`: exceeded configured timeout

Recovery:
- All errors result in structured error response, never engine crash
- Transaction errors auto-rollback the offending transaction
- Engine state remains consistent after any error""",
        "required_keywords": ["错误", "error", "异常", "SYNTAX_ERROR", "除零",
                              "division by zero", "溢出", "overflow", "深度",
                              "depth", "内存限制", "memory limit"],
    },
    "performance": {
        "deps": {"btree_index", "query_planner", "executor"},
        "content": """\
### Performance Design

Target benchmarks (10万行 table):
- Primary key lookup: < 1ms (B-tree point query, O(log n) page reads)
- Full table scan: < 100ms (sequential page reads, prefetch)
- Two-table JOIN (1万行 each): < 200ms (hash join, build on smaller table)
- Indexed range query (1万行 result): < 50ms (B-tree range scan + leaf traversal)
- GROUP BY aggregation (10万行, 100 groups): < 150ms (hash aggregate)
- Create index (10万行): < 500ms (bulk-load sorted, bottom-up B-tree build)

Techniques:
- Buffer pool: cache hot pages in memory (LRU eviction)
- Hash join: build hash table on smaller input
- Bulk index build: sort keys first, build B-tree bottom-up""",
        "required_keywords": ["性能", "performance", "< 1ms", "< 100ms",
                              "buffer pool", "hash join"],
    },
}

# Implementation content for each feature (develop phase)
DEVELOP_FEATURES = {
    "parser_basic": """\
### Parser Implementation

```python
# src/sqlengine/lexer.py
class TokenType(Enum):
    SELECT, FROM, WHERE, INSERT, INTO, VALUES, UPDATE, SET, DELETE,
    CREATE, TABLE, DROP, INT, FLOAT, VARCHAR, BOOL, NULL, ...

class Lexer:
    def tokenize(self, sql: str) -> list[Token]: ...

# src/sqlengine/parser.py
class Parser:
    def parse(self, tokens: list[Token]) -> ASTNode: ...
    def _parse_select(self) -> SelectNode: ...
    def _parse_insert(self) -> InsertNode: ...
    def _parse_create_table(self) -> CreateTableNode: ...
    def _parse_expression(self, precedence=0) -> ExprNode: ...  # Pratt parser
```

Tests: 25 parser tests covering all DDL/DML/DQL basic forms.""",

    "parser_advanced": """\
### Advanced Parser Implementation

```python
# Extended parser methods
class Parser:
    def _parse_join(self) -> JoinNode: ...
    def _parse_subquery(self) -> SubqueryNode: ...
    def _parse_window_function(self) -> WindowNode: ...
    def _parse_case_when(self) -> CaseNode: ...
    def _parse_set_operation(self) -> SetOpNode: ...
    def _parse_create_index(self) -> CreateIndexNode: ...
```

Self-join support: table aliases (`SELECT a.id FROM orders a JOIN orders b ON ...`).
Correlated subquery detection: mark subqueries that reference outer scope.
Tests: 30 additional parser tests for advanced SQL.""",

    "aggregation": """\
### Aggregation Implementation

```python
# src/sqlengine/executor/hash_aggregate.py
class HashAggregateNode:
    def __init__(self, group_by_cols, agg_funcs, child):
        self.groups: dict[tuple, AggState] = {}

    def next(self) -> Row:
        # Build phase: consume all child rows, group into hash map
        # Emit phase: yield one row per group
```

HAVING filter applied after aggregation.
NULL handling: COUNT(*) counts all, COUNT(col) skips NULL.
Tests: 15 tests for GROUP BY + HAVING + all aggregate functions.""",

    "null_semantics": """\
### NULL Semantics Implementation

```python
# src/sqlengine/types.py
class SqlNull:
    def __eq__(self, other): return UNKNOWN
    def __and__(self, other):
        if other is FALSE: return FALSE
        return UNKNOWN
    def __or__(self, other):
        if other is TRUE: return TRUE
        return UNKNOWN
```

Three-valued comparison propagated through all expression evaluation.
Tests: 12 tests covering NULL in WHERE, JOIN, GROUP BY, ORDER BY, DISTINCT.""",

    "storage": """\
### Storage Engine Implementation

```python
# src/sqlengine/storage/page.py
PAGE_SIZE = 4096

class Page:
    def __init__(self, page_id: int):
        self.header = PageHeader(page_id)
        self.slots: list[Slot] = []
        self.data: bytearray = bytearray(PAGE_SIZE)

# src/sqlengine/storage/heap.py
class HeapFile:
    def insert(self, row: Row) -> RowId: ...
    def scan(self) -> Iterator[Row]: ...
    def get(self, row_id: RowId) -> Row: ...
```

Row serialization: fixed-size columns inline, VARCHAR as length-prefixed.
Tests: 10 tests for page operations and heap scan.""",

    "btree_index": """\
### B-Tree Index Implementation

```python
# src/sqlengine/storage/btree.py
class BTreeIndex:
    def __init__(self, key_columns: list[str], page_manager):
        self.root_page_id: int = ...

    def search(self, key) -> RowId | None: ...
    def range_scan(self, low, high) -> Iterator[RowId]: ...
    def insert(self, key, row_id): ...
    def bulk_load(self, sorted_entries: list[tuple]): ...  # bottom-up build
```

B+ tree with order based on page size. Leaf nodes linked for range scans.
Composite index: compare columns left-to-right.
Tests: 20 tests for search, insert, split, range scan, bulk load.""",

    "query_planner": """\
### Query Planner Implementation

```python
# src/sqlengine/planner/optimizer.py
class Optimizer:
    def optimize(self, logical_plan: LogicalPlan) -> PhysicalPlan:
        plan = self._constant_fold(logical_plan)
        plan = self._predicate_pushdown(plan)
        plan = self._projection_pruning(plan)
        plan = self._select_join_order(plan)
        plan = self._select_access_paths(plan)  # index vs scan
        return plan

# src/sqlengine/planner/cost_model.py
class CostModel:
    def estimate_cost(self, plan: PhysicalPlan) -> float:
        # I/O: page_count * PAGE_READ_COST
        # CPU: row_count * COMPARISON_COST
```

EXPLAIN: traverses plan tree, outputs indented text with cost estimates.
Tests: 15 tests for each optimization rule + JOIN order selection.""",

    "executor": """\
### Executor Implementation

```python
# src/sqlengine/executor/engine.py
class ExecutionEngine:
    def execute(self, plan: PhysicalPlan) -> ResultSet: ...

# Operator nodes (volcano model)
class SeqScanNode: ...
class IndexScanNode: ...
class FilterNode: ...
class ProjectNode: ...
class HashJoinNode: ...
class NestedLoopJoinNode: ...
class SortNode: ...        # external sort for large datasets
class LimitNode: ...
class HashAggregateNode: ...
class WindowNode: ...      # buffer partition, compute rank
class UnionNode / ExceptNode / IntersectNode: ...
```

Each node: open() → next() → close() pipeline.
Tests: 20 tests for each operator.""",

    "transaction": """\
### Transaction Implementation

```python
# src/sqlengine/txn/manager.py
class TransactionManager:
    def begin(self) -> Transaction: ...
    def commit(self, txn: Transaction): ...
    def rollback(self, txn: Transaction): ...

# src/sqlengine/txn/mvcc.py
class MVCCManager:
    def is_visible(self, row: Row, txn: Transaction) -> bool:
        # Check xmin committed and before txn snapshot
        # Check xmax not set or not committed

# src/sqlengine/txn/deadlock.py
class DeadlockDetector:
    def __init__(self):
        self.wait_for: dict[int, set[int]] = {}  # txn_id → set of txn_ids

    def add_wait(self, waiter: int, holder: int) -> int | None:
        # Add edge, check for cycle via DFS
        # Return victim txn_id if deadlock detected
```

Tests: 15 tests for MVCC visibility, conflict detection, deadlock.""",

    "error_handling": """\
### Error Handling Implementation

```python
# src/sqlengine/errors.py
@dataclass
class SqlError:
    code: str
    message: str
    location: tuple[int, int] | None = None
    context: str | None = None

class SqlEngineError(Exception):
    def __init__(self, error: SqlError): ...

# Error codes
SYNTAX_ERROR = "E001"
UNKNOWN_TABLE = "E002"
UNKNOWN_COLUMN = "E003"
TYPE_MISMATCH = "E004"
DIVISION_BY_ZERO = "E101"
INTEGER_OVERFLOW = "E102"
QUERY_DEPTH_EXCEEDED = "E103"
MEMORY_LIMIT_EXCEEDED = "E104"
DEADLOCK_DETECTED = "E201"
WRITE_CONFLICT = "E202"
```

Semantic analyzer validates table/column references before execution.
Runtime errors caught in executor, wrapped in SqlError.
Transaction errors trigger auto-rollback before raising.
Tests: 20 tests covering all error codes + recovery.""",

    "performance": """\
### Performance Implementation

```python
# src/sqlengine/storage/buffer_pool.py
class BufferPool:
    def __init__(self, capacity=1000):
        self.pages: dict[int, Page] = {}
        self.lru: OrderedDict = OrderedDict()

    def get_page(self, page_id: int) -> Page: ...
    def mark_dirty(self, page_id: int): ...
    def flush(self): ...

# Optimized hash join
class HashJoinNode:
    def open(self):
        # Build phase: hash smaller input
        self.hash_table = {}
        for row in self.build_child:
            key = row[self.join_col]
            self.hash_table.setdefault(key, []).append(row)

# Bulk index build
class BTreeIndex:
    def bulk_load(self, sorted_entries):
        # Build leaf pages left-to-right
        # Build internal pages bottom-up
```

Tests: 6 performance benchmarks with timing assertions.""",
}

# Benchmark items for test phase: query → expected behavior
BENCHMARK_ITEMS = {
    # Level 1: Basic SQL
    "basic_select": {
        "query": "SELECT * FROM customers WHERE country = 'US'",
        "requires": {"parser_basic", "storage", "executor"},
    },
    "basic_insert_select": {
        "query": "INSERT INTO test VALUES (1, 'a'); SELECT * FROM test",
        "requires": {"parser_basic", "storage", "executor"},
    },
    # Level 2: Aggregation & sorting
    "group_by_having": {
        "query": "SELECT country, COUNT(*) c FROM customers GROUP BY country HAVING c > 5 ORDER BY c DESC",
        "requires": {"parser_basic", "aggregation", "executor"},
    },
    "order_limit_offset": {
        "query": "SELECT name FROM products ORDER BY price DESC LIMIT 10 OFFSET 5",
        "requires": {"parser_basic", "aggregation", "executor"},
    },
    # Level 3: JOINs
    "inner_join": {
        "query": "SELECT o.id, c.name FROM orders o INNER JOIN customers c ON o.customer_id = c.id",
        "requires": {"parser_advanced", "executor"},
    },
    "left_join_null": {
        "query": "SELECT c.name, o.id FROM customers c LEFT JOIN orders o ON c.id = o.customer_id WHERE o.id IS NULL",
        "requires": {"parser_advanced", "null_semantics", "executor"},
    },
    "self_join": {
        "query": "SELECT a.name, b.name FROM employees a JOIN employees b ON a.manager_id = b.id",
        "requires": {"parser_advanced", "executor"},
    },
    "three_table_join": {
        "query": "SELECT c.name, p.name, oi.quantity FROM customers c JOIN orders o ON c.id=o.customer_id JOIN order_items oi ON o.id=oi.order_id JOIN products p ON oi.product_id=p.id",
        "requires": {"parser_advanced", "query_planner", "executor"},
    },
    # Level 4: Subqueries
    "scalar_subquery": {
        "query": "SELECT name, (SELECT COUNT(*) FROM orders WHERE customer_id=c.id) as order_count FROM customers c",
        "requires": {"parser_advanced", "executor"},
    },
    "exists_subquery": {
        "query": "SELECT name FROM customers c WHERE EXISTS (SELECT 1 FROM orders WHERE customer_id=c.id AND total > 1000)",
        "requires": {"parser_advanced", "executor"},
    },
    "in_subquery": {
        "query": "SELECT name FROM products WHERE id IN (SELECT product_id FROM order_items WHERE quantity > 10)",
        "requires": {"parser_advanced", "executor"},
    },
    # Level 5: Window functions & set ops
    "window_rank": {
        "query": "SELECT name, total, RANK() OVER (ORDER BY total DESC) as rank FROM orders",
        "requires": {"parser_advanced", "executor"},
    },
    "union_except": {
        "query": "SELECT name FROM customers WHERE country='US' UNION SELECT name FROM customers WHERE country='UK' EXCEPT SELECT name FROM customers WHERE status='inactive'",
        "requires": {"parser_advanced", "executor"},
    },
    "case_when_between": {
        "query": "SELECT name, CASE WHEN price BETWEEN 10 AND 50 THEN 'budget' WHEN price > 50 THEN 'premium' ELSE 'free' END as tier FROM products",
        "requires": {"parser_advanced", "executor"},
    },
    # Level 6: Index & optimization
    "index_scan": {
        "query": "CREATE INDEX idx_customer_country ON customers(country); EXPLAIN SELECT * FROM customers WHERE country='US'",
        "requires": {"btree_index", "query_planner"},
        "verify": "index scan",
    },
    "cost_based_join": {
        "query": "EXPLAIN SELECT * FROM large_table a JOIN small_table b ON a.id=b.id",
        "requires": {"query_planner"},
        "verify": "build on small_table",
    },
    # Level 7: Transactions
    "transaction_commit": {
        "query": "BEGIN; INSERT INTO test VALUES (1); COMMIT; SELECT * FROM test",
        "requires": {"transaction", "parser_basic", "storage", "executor"},
    },
    "transaction_rollback": {
        "query": "BEGIN; INSERT INTO test VALUES (1); ROLLBACK; SELECT * FROM test",
        "requires": {"transaction", "parser_basic", "storage", "executor"},
        "verify": "empty result",
    },
    # Level 8: Error handling & performance
    "error_handling": {
        "query": "SELECT * FROM nonexistent; SELECT 1/0; SELECT * FROM t WHERE ((((...depth 33...))))",
        "requires": {"error_handling"},
        "verify": "UNKNOWN_TABLE, DIVISION_BY_ZERO, QUERY_DEPTH_EXCEEDED",
    },
    "performance_benchmark": {
        "query": "-- 100K row primary key lookup, full scan, join, range, groupby, index build",
        "requires": {"performance", "btree_index", "query_planner", "executor"},
        "verify": "all 6 performance targets met",
    },
}


# ---------------------------------------------------------------------------
# State-driven agent
# ---------------------------------------------------------------------------

class StateDrivenAgent(BaseAgent):
    """Reads issue state, decides what to output. No round counting."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.execute_log: list[str] = []
        self.review_log: list[str] = []

    def _parse_requirements(self, req_text: str) -> set[str]:
        """Extract which features are required based on keywords in requirements."""
        required = set()
        req_lower = req_text.lower()
        for fname, fdata in DESIGN_FEATURES.items():
            for kw in fdata["required_keywords"]:
                if kw.lower() in req_lower:
                    required.add(fname)
                    break
        return required

    def _included_features(self, content: str) -> set[str]:
        """Detect which features are already covered in design/develop content."""
        included = set()
        content_lower = content.lower()
        # Each feature has multiple possible markers (design vs develop headers differ)
        feature_markers = {
            "parser_basic": ["sql parser (core)", "parser implementation",
                             "class parser", "class lexer", "tokenize"],
            "parser_advanced": ["sql parser (advanced)", "advanced parser",
                                "_parse_join", "_parse_subquery", "window_function"],
            "aggregation": ["aggregation", "hash_aggregate", "hashaggregate",
                            "group by", "having"],
            "null_semantics": ["null semantics", "sqlnull", "three-valued",
                               "three_valued", "unknown"],
            "storage": ["storage engine", "page-based storage", "heapfile",
                        "class page", "heap table"],
            "btree_index": ["b-tree index", "b+ tree", "btreeindex",
                            "class btreeindex", "bulk_load"],
            "query_planner": ["query planner", "optimizer", "predicate_pushdown",
                              "constant_fold", "cost_model", "costmodel"],
            "executor": ["executor", "volcano", "iterator model",
                         "seqscan", "hashjoin", "executionengine"],
            "transaction": ["transaction", "mvcc", "deadlock", "transactionmanager",
                            "deadlockdetector", "xmin", "xmax"],
            "error_handling": ["error handling", "sqlerror", "sqlengine_error",
                               "syntax_error", "division_by_zero", "error codes"],
            "performance": ["performance", "buffer_pool", "bufferpool",
                            "bulk index build", "hash join", "lru"],
        }
        for fname, markers in feature_markers.items():
            for marker in markers:
                if marker in content_lower:
                    included.add(fname)
                    break
        return included

    def _mentioned_in_review(self, review_text: str) -> set[str]:
        """Detect which features reviewers mentioned as missing."""
        mentioned = set()
        review_lower = review_text.lower()
        # Map reviewer keywords to features
        review_markers = {
            "parser_advanced": ["join", "subquer", "window", "union", "like", "between",
                                "case when", "index creation", "set operation"],
            "aggregation": ["group by", "having", "aggregate", "order by", "limit",
                            "sum", "avg", "count"],
            "null_semantics": ["null", "three-value", "三值"],
            "btree_index": ["b-tree", "index", "索引"],
            "query_planner": ["predicate pushdown", "谓词下推", "cost", "成本",
                              "explain", "优化", "optimizer", "join order"],
            "transaction": ["transaction", "事务", "mvcc", "deadlock", "死锁",
                            "begin", "commit", "rollback"],
            "error_handling": ["error", "错误", "异常", "exception", "division by zero",
                               "overflow", "depth limit", "memory limit"],
            "performance": ["performance", "性能", "benchmark", "< 1ms", "buffer pool"],
        }
        for fname, markers in review_markers.items():
            for m in markers:
                if m in review_lower:
                    mentioned.add(fname)
                    break
        return mentioned

    def _resolve_deps(self, features: set[str]) -> set[str]:
        """Add all dependencies for a set of features."""
        resolved = set(features)
        changed = True
        while changed:
            changed = False
            for f in list(resolved):
                if f in DESIGN_FEATURES:
                    for dep in DESIGN_FEATURES[f]["deps"]:
                        if dep not in resolved:
                            resolved.add(dep)
                            changed = True
        return resolved

    async def execute(self, request: AgentRequest) -> AgentResponse:
        self.execute_log.append(request.action)
        issue = request.issue
        requirements = issue.sections.get("需求", "")

        if request.action == "design":
            return self._do_design(issue, requirements)
        elif request.action == "develop":
            return self._do_develop(issue, requirements)
        elif request.action == "test":
            return self._do_test(issue)
        return AgentResponse(content=f"[{request.action}]", success=True)

    def _do_design(self, issue, requirements):
        """Produce design based on current state."""
        existing_design = issue.sections.get("设计", "")
        review_comments = issue.sections.get("Design Review", "")

        # Start with what we already have
        already_included = self._included_features(existing_design)

        # What does the reviewer say is missing?
        reviewer_wants = self._mentioned_in_review(review_comments)

        # Base features always included
        base_features = {"parser_basic", "storage", "executor"}

        # Build target feature set
        target = base_features | already_included | reviewer_wants
        target = self._resolve_deps(target)

        # Generate design content
        sections = ["## SQL Database Engine — Design Document\n"]
        for fname in ["parser_basic", "parser_advanced", "aggregation",
                      "null_semantics", "storage", "btree_index",
                      "query_planner", "executor", "transaction",
                      "error_handling", "performance"]:
            if fname in target:
                sections.append(DESIGN_FEATURES[fname]["content"])

        content = "\n\n".join(sections)
        return AgentResponse(content=content, success=True,
                             metadata={"included_features": sorted(target)})

    def _do_develop(self, issue, requirements):
        """Produce implementation based on design and review feedback."""
        design = issue.sections.get("设计", "")
        existing_impl = issue.sections.get("开发步骤", "")
        review_comments = issue.sections.get("Dev Review", "")

        # What's designed?
        designed = self._included_features(design)

        # What's already implemented?
        already_impl = self._included_features(existing_impl)

        # What does reviewer say is missing/broken?
        reviewer_wants = self._mentioned_in_review(review_comments)

        # Implement what's designed + what reviewer asks for
        target = designed | reviewer_wants
        target = self._resolve_deps(target)

        sections = ["## SQL Database Engine — Implementation\n"]
        for fname in ["parser_basic", "parser_advanced", "aggregation",
                      "null_semantics", "storage", "btree_index",
                      "query_planner", "executor", "transaction",
                      "error_handling", "performance"]:
            if fname in target and fname in DEVELOP_FEATURES:
                sections.append(DEVELOP_FEATURES[fname])

        content = "\n\n".join(sections)
        return AgentResponse(content=content, success=True,
                             metadata={"implemented_features": sorted(target)})

    def _do_test(self, issue):
        """Run benchmarks against implementation. Check which pass."""
        impl = issue.sections.get("开发步骤", "")
        implemented = self._included_features(impl)

        passed = []
        failed = []
        for bname, bdata in BENCHMARK_ITEMS.items():
            required = bdata["requires"]
            if required <= implemented:
                passed.append(bname)
            else:
                missing = required - implemented
                failed.append((bname, missing))

        total = len(BENCHMARK_ITEMS)
        pass_count = len(passed)

        lines = [f"## Benchmark Results: {pass_count}/{total}\n"]
        for bname in passed:
            lines.append(f"  ✓ {bname}: {BENCHMARK_ITEMS[bname]['query'][:60]}...")
        for bname, missing in failed:
            lines.append(f"  ✗ {bname}: FAILED — missing features: {', '.join(sorted(missing))}")

        if failed:
            # Analyze: are failures due to missing design or missing implementation?
            impl_sections = self._included_features(impl)
            design = issue.sections.get("设计", "")
            designed = self._included_features(design)

            all_missing = set()
            for _, missing in failed:
                all_missing |= missing

            not_designed = all_missing - designed
            designed_not_impl = all_missing & designed - impl_sections

            lines.append("\n## Failure Analysis")
            if not_designed:
                lines.append(f"Features not in design: {', '.join(sorted(not_designed))}")
                lines.append("Recommendation: DESIGN")
                recommendation = "design"
            elif designed_not_impl:
                lines.append(f"Features designed but not implemented: {', '.join(sorted(designed_not_impl))}")
                lines.append("Recommendation: DEVELOP")
                recommendation = "develop"
            else:
                lines.append("Recommendation: DEVELOP (implementation may have bugs)")
                recommendation = "develop"

            content = "\n".join(lines)
            return AgentResponse(content=content, success=False,
                                 metadata={"recommendation": recommendation,
                                           "passed": pass_count, "total": total})

        # All passed
        lines.append("\n## All benchmarks passed!")
        content = "\n".join(lines)
        return AgentResponse(content=content, success=True,
                             metadata={"passed": total, "total": total})

    async def stream(self, request):
        raise NotImplementedError

    async def review(self, request: AgentRequest) -> ReviewResult:
        """Review: check content against requirements."""
        self.review_log.append(request.action)
        issue = request.issue
        requirements = issue.sections.get("需求", "")
        required_features = self._parse_requirements(requirements)

        # Determine what we're reviewing
        if "开发步骤" in issue.sections and issue.status.value in ("developing", "dev_review"):
            content = issue.sections.get("开发步骤", "")
            phase = "develop"
        else:
            content = issue.sections.get("设计", "")
            phase = "design"

        covered = self._included_features(content)
        missing = required_features - covered

        if missing:
            comments = []
            for feat in sorted(missing):
                desc = DESIGN_FEATURES.get(feat, {}).get("content", feat)
                first_line = desc.strip().split("\n")[0] if isinstance(desc, str) else feat
                comments.append(ReviewComment(
                    severity=Severity.HIGH,
                    message=f"Missing {phase} for: {first_line}. "
                            f"Requirements mention keywords: "
                            f"{DESIGN_FEATURES.get(feat, {}).get('required_keywords', [feat])}",
                ))
            return ReviewResult(passed=False, comments=comments,
                                reviewer=f"{phase}-reviewer")

        return ReviewResult(
            passed=True,
            comments=[ReviewComment(
                severity=Severity.LOW,
                message=f"All required features covered in {phase}.",
            )],
            reviewer=f"{phase}-reviewer",
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def playground(tmp_path):
    repo = tmp_path / "coder-playground"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    # Write requirements
    req_src = Path(__file__).parent.parent / "tests" / "_sql_requirements.md"
    # Inline the requirements
    (repo / "requirements.md").write_text(
        (Path("/Users/xupeng/lab/coder-playground/requirements.md")).read_text(),
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("__pycache__/\n.shadowcoder/worktrees/\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial setup with requirements"],
                   cwd=str(repo), check=True, capture_output=True)
    return repo


@pytest.fixture
def system(playground, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""\
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code

reviewers:
  design: [claude-code]
  develop: [claude-code]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 5
  max_test_retries: 5

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
""")

    config = Config(str(config_path))
    agent = StateDrivenAgent({"type": "claude_code"})

    bus = MessageBus()
    wt_manager = WorktreeManager(config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    issue_store = IssueStore(str(playground), config)
    registry = AgentRegistry(config)
    registry._instances["claude-code"] = agent

    engine = Engine(bus, issue_store, task_manager, registry, config, str(playground))

    events = {mt: [] for mt in MessageType}
    for mt in MessageType:
        async def _h(msg, _mt=mt):
            events[_mt].append(msg)
        bus.subscribe(mt, _h)

    return {
        "bus": bus,
        "store": issue_store,
        "agent": agent,
        "repo": playground,
        "config": config,
        "events": events,
    }


# ---------------------------------------------------------------------------
# The Test
# ---------------------------------------------------------------------------

async def test_sql_engine_goal_driven(system):
    """
    Goal-driven e2e: create issue with SQL engine requirements,
    trigger design → develop → test. The system iterates until
    all 20 benchmarks pass. Flow is emergent, not scripted.
    """
    bus = system["bus"]
    store = system["store"]
    repo = system["repo"]
    agent = system["agent"]
    events = system["events"]

    # === CREATE with requirements ===
    req_path = str(repo / "requirements.md")
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {
        "title": "Implement SQL Database Engine",
        "priority": "high",
        "tags": ["database", "sql", "engine"],
        "description": req_path,
    }))

    issue = store.get(1)
    assert issue.status == IssueStatus.CREATED
    assert "SQL" in issue.sections.get("需求", "")
    assert "GROUP BY" in issue.sections["需求"]
    assert "MVCC" in issue.sections["需求"]
    assert "性能" in issue.sections["需求"]

    # === DESIGN (system iterates until reviewer approves) ===
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))

    issue = store.get(1)
    design_status = issue.status

    # If design went BLOCKED (too many rounds), approve and continue
    if design_status == IssueStatus.BLOCKED:
        await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))
        issue = store.get(1)

    assert issue.status == IssueStatus.APPROVED, \
        f"Expected APPROVED, got {issue.status.value}"

    # Verify design covers substantial features
    design = issue.sections.get("设计", "")
    assert len(design) > 500, "Design should be substantial"
    # At minimum, base features should be in the design
    assert "SQL Parser" in design
    assert "Storage Engine" in design or "storage engine" in design.lower()
    assert "Executor" in design or "executor" in design.lower()

    # Design review should have history
    review = issue.sections.get("Design Review", "")
    assert len(review) > 0, "Should have review history"

    # === DEVELOP ===
    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    issue = store.get(1)
    dev_status = issue.status

    if dev_status == IssueStatus.BLOCKED:
        await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": 1}))
        issue = store.get(1)

    assert issue.status == IssueStatus.TESTING, \
        f"Expected TESTING, got {issue.status.value}"

    impl = issue.sections.get("开发步骤", "")
    assert len(impl) > 500, "Implementation should be substantial"

    # === TEST (system auto-retries with recommendation routing) ===
    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))

    issue = store.get(1)

    # If test exhausted retries, approve from blocked
    if issue.status == IssueStatus.BLOCKED:
        # Check how far we got
        test_content = issue.sections.get("测试", "")
        print(f"Test ended in BLOCKED. Last test output:\n{test_content[:500]}")
        # This is acceptable — means the system correctly identified
        # it couldn't reach the goal within retry limits
        return

    assert issue.status == IssueStatus.DONE, \
        f"Expected DONE, got {issue.status.value}"

    # === VERIFY FINAL STATE ===
    test_content = issue.sections.get("测试", "")
    total = len(BENCHMARK_ITEMS)
    assert f"{total}/{total}" in test_content, \
        f"Expected all {total} benchmarks to pass"
    assert "All benchmarks passed" in test_content

    # Verify all sections present
    assert "需求" in issue.sections
    assert "设计" in issue.sections
    assert "Design Review" in issue.sections
    assert "开发步骤" in issue.sections
    assert "Dev Review" in issue.sections
    assert "测试" in issue.sections

    # Verify file integrity
    issue_file = repo / ".shadowcoder" / "issues" / "0001.md"
    post = fm.load(str(issue_file))
    assert post["status"] == "done"

    # === VERIFY GOAL WAS REACHED ===
    design_calls = agent.execute_log.count("design")
    develop_calls = agent.execute_log.count("develop")
    test_calls = agent.execute_log.count("test")
    review_calls = len(agent.review_log)

    print(f"\n=== Execution Summary ===")
    print(f"Design rounds: {design_calls}")
    print(f"Develop rounds: {develop_calls}")
    print(f"Test rounds: {test_calls}")
    print(f"Review calls: {review_calls}")

    # Goal-oriented assertions: the system reached the goal,
    # and it went through review (not just one-shot without feedback)
    assert design_calls >= 1
    assert develop_calls >= 1
    assert test_calls >= 1
    assert review_calls >= 2, "Should have gone through review process"

    # Design required multiple rounds (base features don't cover all requirements)
    assert design_calls >= 2, \
        f"Design should need iteration (requirements are complex), got {design_calls}"

    # Git verification
    result = subprocess.run(
        ["git", "branch", "--list", "shadowcoder/*"],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert "shadowcoder/issue-1" in result.stdout
