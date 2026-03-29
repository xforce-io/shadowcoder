"""Tests for metric gate feature."""
import pytest
from pathlib import Path
from shadowcoder.core.engine import Engine


class TestMetricGateConfig:
    def test_not_configured(self, tmp_path):
        from shadowcoder.core.config import Config
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
        )
        c = Config(str(config_path))
        assert c.get_metric_gate() is None

    def test_configured(self, tmp_path):
        from shadowcoder.core.config import Config
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
            "metric_gate:\n  recall: \">= 0.50\"\n  precision: \">= 0.20\"\n"
        )
        c = Config(str(config_path))
        assert c.get_metric_gate() == {"recall": ">= 0.50", "precision": ">= 0.20"}

    def test_max_metric_retries_default(self, tmp_path):
        from shadowcoder.core.config import Config
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
        )
        c = Config(str(config_path))
        assert c.get_max_metric_retries() == 3

    def test_max_metric_retries_custom(self, tmp_path):
        from shadowcoder.core.config import Config
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
            "review_policy:\n  max_metric_retries: 5\n"
        )
        c = Config(str(config_path))
        assert c.get_max_metric_retries() == 5

class TestMetricReading:
    def test_read_metrics_valid(self, tmp_path):
        p = tmp_path / "metrics.json"
        p.write_text('{"recall": 0.96, "precision": 0.55}')
        ok, metrics, err = Engine._read_metrics(str(tmp_path))
        assert ok is True
        assert metrics == {"recall": 0.96, "precision": 0.55}
        assert err == ""

    def test_read_metrics_missing_file(self, tmp_path):
        ok, metrics, err = Engine._read_metrics(str(tmp_path))
        assert ok is False
        assert "not found" in err.lower()

    def test_read_metrics_malformed_json(self, tmp_path):
        (tmp_path / "metrics.json").write_text("not json{")
        ok, metrics, err = Engine._read_metrics(str(tmp_path))
        assert ok is False

    def test_read_metrics_extra_keys_preserved(self, tmp_path):
        (tmp_path / "metrics.json").write_text('{"recall": 0.9, "f1": 0.8, "custom": 0.7}')
        ok, metrics, err = Engine._read_metrics(str(tmp_path))
        assert ok is True
        assert len(metrics) == 3


class TestMetricValidation:
    def test_all_pass(self):
        targets = {"recall": ">= 0.50", "precision": ">= 0.20"}
        metrics = {"recall": 0.96, "precision": 0.55}
        ok, failures = Engine._validate_metrics(metrics, targets)
        assert ok is True
        assert failures == []

    def test_below_baseline(self):
        targets = {"recall": ">= 0.50"}
        metrics = {"recall": 0.05}
        ok, failures = Engine._validate_metrics(metrics, targets)
        assert ok is False
        assert any("recall" in f for f in failures)

    def test_missing_metric(self):
        targets = {"recall": ">= 0.50", "precision": ">= 0.20"}
        metrics = {"recall": 0.96}
        ok, failures = Engine._validate_metrics(metrics, targets)
        assert ok is False
        assert any("precision" in f and "missing" in f.lower() for f in failures)

    def test_operators(self):
        targets = {"loss": "<= 0.05", "accuracy": "> 0.95"}
        metrics = {"loss": 0.03, "accuracy": 0.96}
        ok, _ = Engine._validate_metrics(metrics, targets)
        assert ok is True

    def test_rejects_dirty_spec(self):
        targets = {"recall": ">= 0.90 garbage"}
        metrics = {"recall": 0.96}
        ok, failures = Engine._validate_metrics(metrics, targets)
        assert ok is False
        assert any("invalid" in f for f in failures)


from unittest.mock import AsyncMock, MagicMock
from shadowcoder.core.bus import MessageBus
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.config import Config


class TestMetricGateFeedback:
    @pytest.fixture
    def engine_with_store(self, tmp_repo, tmp_config):
        bus = MessageBus()
        config = Config(str(tmp_config))
        store = IssueStore(str(tmp_repo), config)
        wt = AsyncMock()
        wt.ensure = AsyncMock(return_value="/tmp/wt")
        task_mgr = TaskManager(wt)
        reg = MagicMock()
        engine = Engine(bus, store, task_mgr, reg, config, str(tmp_repo))
        store.create("Test")
        return engine, store

    def test_update_metric_feedback_creates_item(self, engine_with_store):
        engine, store = engine_with_store
        engine._update_metric_gate_feedback(
            1, round_num=2,
            metrics_str="recall=0.05, precision=0.10",
            failures=["recall: 0.0500 not >= 0.5000 (baseline)"])
        fb = store.load_feedback(1)
        mg_items = [i for i in fb["items"] if i.get("source") == "metric_gate"]
        assert len(mg_items) == 1
        assert "recall" in mg_items[0]["description"]
        assert mg_items[0]["id"].startswith("F")

    def test_update_metric_feedback_replaces_previous(self, engine_with_store):
        engine, store = engine_with_store
        engine._update_metric_gate_feedback(1, 1, "recall=0.05", ["recall fail"])
        engine._update_metric_gate_feedback(1, 2, "recall=0.10", ["recall fail again"])
        fb = store.load_feedback(1)
        mg_items = [i for i in fb["items"] if i.get("source") == "metric_gate"]
        assert len(mg_items) == 1  # replaced, not accumulated

    def test_resolve_metric_feedback(self, engine_with_store):
        engine, store = engine_with_store
        engine._update_metric_gate_feedback(1, 1, "recall=0.05", ["fail"])
        engine._resolve_metric_gate_feedback(1, round_num=2)
        fb = store.load_feedback(1)
        mg_items = [i for i in fb["items"] if i.get("source") == "metric_gate"]
        assert all(i["resolved"] for i in mg_items)

    def test_metric_feedback_does_not_break_next_num(self, engine_with_store):
        """Metric gate feedback with F-prefix ID doesn't break _update_feedback's next_num."""
        engine, store = engine_with_store
        engine._update_metric_gate_feedback(1, 1, "recall=0.05", ["fail"])
        fb = store.load_feedback(1)
        items = fb["items"]
        next_num = max(
            (int(item["id"][1:]) for item in items if item["id"].startswith("F")),
            default=0) + 1
        assert next_num > 0
