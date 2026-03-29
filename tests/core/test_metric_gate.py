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
