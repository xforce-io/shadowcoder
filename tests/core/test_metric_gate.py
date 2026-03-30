"""Tests for metric gate v2 — Pareto improvement detection."""
import json
import math
import pytest
from pathlib import Path
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore


class TestMetricGateConfig:
    def test_not_configured(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
        )
        c = Config(str(config_path))
        assert c.get_metric_gate() is None
        assert c.get_metric_targets() is None

    def test_configured_new_format(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
            "metric_gate:\n"
            "  targets:\n"
            "    recall: \">= 0.90\"\n"
            "    precision: \">= 0.50\"\n"
            "  max_stagnant_rounds: 3\n"
        )
        c = Config(str(config_path))
        assert c.get_metric_gate() is not None
        assert c.get_metric_targets() == {"recall": ">= 0.90", "precision": ">= 0.50"}
        assert c.get_max_stagnant_rounds() == 3
        assert c.get_improvement_threshold() == 0.01

    def test_defaults(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
            "metric_gate:\n"
            "  targets:\n"
            "    recall: \">= 0.90\"\n"
        )
        c = Config(str(config_path))
        assert c.get_max_stagnant_rounds() == 2
        assert c.get_improvement_threshold() == 0.01

    def test_custom_threshold(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "models:\n  m:\n    model: sonnet\n"
            "agents:\n  a:\n    type: claude_code\n    model: m\n"
            "metric_gate:\n"
            "  targets:\n"
            "    recall: \">= 0.90\"\n"
            "  improvement_threshold: 0.02\n"
        )
        c = Config(str(config_path))
        assert c.get_improvement_threshold() == 0.02


class TestMetricsHistory:
    def test_load_empty(self, tmp_repo, tmp_config):
        config = Config(str(tmp_config))
        store = IssueStore(str(tmp_repo), config)
        store.create("Test")
        history = store.load_metrics_history(1)
        assert history == {"rounds": []}

    def test_save_and_load(self, tmp_repo, tmp_config):
        config = Config(str(tmp_config))
        store = IssueStore(str(tmp_repo), config)
        store.create("Test")
        store.save_metrics_entry(1, round_num=1, metrics={"recall": 0.79, "precision": 0.73})
        store.save_metrics_entry(1, round_num=2, metrics={"recall": 0.80, "precision": 0.74})
        history = store.load_metrics_history(1)
        assert len(history["rounds"]) == 2
        assert history["rounds"][0]["metrics"]["recall"] == 0.79
        assert history["rounds"][1]["round"] == 2

    def test_get_last_metrics(self, tmp_repo, tmp_config):
        config = Config(str(tmp_config))
        store = IssueStore(str(tmp_repo), config)
        store.create("Test")
        assert store.get_last_metrics(1) is None
        store.save_metrics_entry(1, 1, {"recall": 0.5})
        assert store.get_last_metrics(1) == {"recall": 0.5}
        store.save_metrics_entry(1, 2, {"recall": 0.8})
        assert store.get_last_metrics(1) == {"recall": 0.8}
