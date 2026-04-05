"""Tests for dashboard parsers: LogParser, FeedbackParser, WorktreeParser."""
from shadowcoder.dashboard.parsers import LogEntry, LogParser
from shadowcoder.dashboard.parsers import FeedbackSummary, FeedbackParser
from shadowcoder.dashboard.parsers import ChangedFile, WorktreeParser


class TestLogParser:
    def test_parse_single_entry(self):
        raw = "[2026-03-26 11:42:10] Issue 创建: Add auth\n"
        entries = LogParser.parse_all(raw)
        assert len(entries) == 1
        assert entries[0].timestamp == "11:42"
        assert entries[0].text == "Issue 创建: Add auth"
        assert entries[0].category == "info"

    def test_parse_multiline_entry(self):
        raw = (
            "[2026-03-26 11:45:00] Design Review\n"
            "PASSED (CRITICAL=0, HIGH=0, 7 comments)\n"
            "  [MEDIUM] (src/main.py) refactor needed\n"
        )
        entries = LogParser.parse_all(raw)
        assert len(entries) == 1
        assert "Design Review" in entries[0].text
        assert len(entries[0].continuation) == 2

    def test_parse_gate_fail(self):
        raw = "[2026-03-26 11:48:00] Gate FAIL R1: 3 tests failed\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "error"

    def test_parse_gate_pass(self):
        raw = "[2026-03-26 11:50:00] Gate PASS R2\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "success"

    def test_parse_usage(self):
        raw = "[2026-03-26 11:45:03] Usage: 1200+800 tokens, $0.08\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "info"

    def test_parse_metric_gate_fail(self):
        raw = "[2026-03-26 11:50:00] Metric gate FAIL: recall 0.32 < 0.50\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "error"

    def test_parse_revert(self):
        raw = "[2026-03-26 11:50:00] 代码回滚至 checkpoint\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "warning"

    def test_parse_developing_start(self):
        raw = "[2026-03-26 11:46:00] Develop R1 开始\n"
        entries = LogParser.parse_all(raw)
        assert entries[0].category == "active"

    def test_tail_new_lines(self):
        raw_initial = "[2026-03-26 11:42:10] Line 1\n"
        parser = LogParser()
        entries = parser.parse_tail(raw_initial)
        assert len(entries) == 1
        raw_appended = raw_initial + "[2026-03-26 11:43:00] Line 2\n"
        new_entries = parser.parse_tail(raw_appended)
        assert len(new_entries) == 1
        assert new_entries[0].text == "Line 2"

    def test_empty_log(self):
        entries = LogParser.parse_all("")
        assert entries == []


class TestFeedbackParser:
    def test_parse_empty(self):
        data = {"items": [], "proposed_tests": [], "acceptance_tests": [], "supplementary_tests": []}
        summary = FeedbackParser.summarize(data)
        assert summary.verdict is None
        assert summary.critical == 0
        assert summary.high == 0

    def test_parse_with_items(self):
        data = {
            "items": [
                {"id": "1", "category": "bug", "description": "fix it",
                 "severity": "CRITICAL", "resolved": False},
                {"id": "2", "category": "style", "description": "rename",
                 "severity": "MEDIUM", "resolved": True},
                {"id": "3", "category": "bug", "description": "another",
                 "severity": "HIGH", "resolved": False},
            ],
            "proposed_tests": [{"name": "test_a", "passed": True}],
            "acceptance_tests": [],
            "supplementary_tests": [],
        }
        summary = FeedbackParser.summarize(data)
        assert summary.critical == 1
        assert summary.high == 1
        assert summary.medium == 0
        assert summary.total == 2

    def test_missing_severity_key(self):
        data = {
            "items": [{"id": "1", "category": "bug", "description": "x"}],
            "proposed_tests": [], "acceptance_tests": [], "supplementary_tests": [],
        }
        summary = FeedbackParser.summarize(data)
        assert summary.total == 0


class TestWorktreeParser:
    def test_parse_name_status(self):
        output = "A\tsrc/auth.py\nM\tsrc/main.py\nD\told.py\n"
        files = WorktreeParser.parse_name_status(output)
        assert len(files) == 3
        assert files[0].status == "A"
        assert files[0].path == "src/auth.py"
        assert files[1].status == "M"
        assert files[2].status == "D"

    def test_parse_empty(self):
        files = WorktreeParser.parse_name_status("")
        assert files == []

    def test_parse_stat(self):
        output = " src/auth.py | 142 +++\n src/main.py |  15 ++-\n 2 files changed\n"
        stats = WorktreeParser.parse_stat(output)
        assert stats["src/auth.py"] == "142 +++"
        assert stats["src/main.py"] == "15 ++-"
