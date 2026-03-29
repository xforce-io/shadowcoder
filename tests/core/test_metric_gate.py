"""Tests for metric gate feature."""
import pytest


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
