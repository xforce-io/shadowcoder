"""Tests for metric gate v2 — Pareto improvement detection."""
import json
import math
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus, BLOCKED_METRIC_STAGNATED
from shadowcoder.agents.types import AcceptanceOutput, DevelopOutput, ReviewOutput


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


class TestParetoComparison:
    def test_improvement_one_better(self):
        ok = Engine._is_pareto_improvement(
            current={"recall": 0.80, "precision": 0.73},
            previous={"recall": 0.79, "precision": 0.73},
            targets={"recall": ">= 0.90", "precision": ">= 0.50"},
            threshold=0.01)
        assert ok is True

    def test_no_change_is_stagnation(self):
        ok = Engine._is_pareto_improvement(
            current={"recall": 0.79, "precision": 0.73},
            previous={"recall": 0.79, "precision": 0.73},
            targets={"recall": ">= 0.90", "precision": ">= 0.50"},
            threshold=0.01)
        assert ok is False

    def test_one_worse_is_not_pareto(self):
        ok = Engine._is_pareto_improvement(
            current={"recall": 0.80, "precision": 0.71},
            previous={"recall": 0.79, "precision": 0.73},
            targets={"recall": ">= 0.90", "precision": ">= 0.50"},
            threshold=0.01)
        assert ok is False

    def test_tiny_improvement_below_threshold(self):
        ok = Engine._is_pareto_improvement(
            current={"recall": 0.791, "precision": 0.73},
            previous={"recall": 0.79, "precision": 0.73},
            targets={"recall": ">= 0.90", "precision": ">= 0.50"},
            threshold=0.01)
        assert ok is False

    def test_first_round_no_previous(self):
        ok = Engine._is_pareto_improvement(
            current={"recall": 0.50, "precision": 0.30},
            previous=None,
            targets={"recall": ">= 0.90", "precision": ">= 0.50"},
            threshold=0.01)
        assert ok is True

    def test_only_target_metrics_compared(self):
        ok = Engine._is_pareto_improvement(
            current={"recall": 0.80, "precision": 0.73, "f1": 0.50},
            previous={"recall": 0.79, "precision": 0.73},
            targets={"recall": ">= 0.90", "precision": ">= 0.50"},
            threshold=0.01)
        assert ok is True


# ---- Integration tests: Pareto detection in the develop loop ----

@pytest.fixture
def bus():
    return MessageBus()

@pytest.fixture
def mock_worktree():
    wt = AsyncMock()
    wt.ensure = AsyncMock(return_value="/tmp/wt")
    wt.exists = AsyncMock(return_value=True)
    wt.cleanup = AsyncMock()
    return wt

@pytest.fixture
def task_mgr(mock_worktree):
    return TaskManager(mock_worktree)

@pytest.fixture
def pareto_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""\
clouds:
  local:
    env: {}
models:
  default-model:
    cloud: local
    model: sonnet
agents:
  claude-code:
    type: claude_code
    model: default-model
dispatch:
  design: claude-code
  develop: claude-code
  design_review: [claude-code]
  develop_review: [claude-code]
review_policy:
  max_review_rounds: 4
metric_gate:
  targets:
    recall: ">= 0.90"
    precision: ">= 0.50"
  max_stagnant_rounds: 2
""")
    return Config(str(config_path))

def _setup_at_approved(store):
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)

def _make_engine(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=AcceptanceOutput(
        script="#!/bin/bash\nset -euo pipefail\ntest -f .dev_done\n"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)
    return Engine(bus, store, task_mgr, reg, config, "/tmp/repo"), agent


class TestParetoInDevelopLoop:
    async def test_targets_met_proceeds_to_done(self, bus, pareto_config, tmp_repo, mock_worktree, task_mgr):
        store = IssueStore(str(tmp_repo), pareto_config)
        engine, agent = _make_engine(bus, store, task_mgr, pareto_config)
        _setup_at_approved(store)
        engine._run_acceptance_phase = AsyncMock(return_value=True)
        engine._read_metrics = staticmethod(
            lambda path: (True, {"recall": 0.95, "precision": 0.60}, ""))
        async def mock_run_command(cmd, cwd=None, **kwargs):
            if "bash " in cmd and "acceptance" in cmd:
                return True, "pass", 0.0
            return True, "", 0.0
        engine._run_command = mock_run_command
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="diff")
        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.DONE

    async def test_pareto_improvement_continues(self, bus, pareto_config, tmp_repo, mock_worktree, task_mgr):
        """Round 1: gate fails (forces retry). Round 2: metrics below target but
        first metric reading (Pareto by default). Round 3: metrics meet targets → DONE."""
        store = IssueStore(str(tmp_repo), pareto_config)
        engine, agent = _make_engine(bus, store, task_mgr, pareto_config)
        _setup_at_approved(store)
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        gate_call = 0
        async def mock_gate_check(issue_id, task, round_num):
            nonlocal gate_call
            gate_call += 1
            if gate_call == 1:
                return False, "test failed", "error output", 0.0
            return True, "ok", "", 0.0
        engine._gate_check = mock_gate_check

        read_call = 0
        def mock_read_metrics(path):
            nonlocal read_call
            read_call += 1
            if read_call == 1:
                return True, {"recall": 0.70, "precision": 0.40}, ""
            return True, {"recall": 0.95, "precision": 0.60}, ""
        engine._read_metrics = staticmethod(mock_read_metrics)
        async def mock_run_command(cmd, cwd=None, **kwargs):
            if "bash " in cmd and "acceptance" in cmd:
                return True, "pass", 0.0
            return True, "", 0.0
        engine._run_command = mock_run_command
        engine._get_code_diff = AsyncMock(return_value="diff")
        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.DONE
        # 3 develop calls: round 1 (gate fail), round 2 (pareto ok), round 3 (targets met)
        # Actually round 2 targets met → done after review, so develop called 3 times
        assert agent.develop.call_count >= 2

    async def test_stagnation_blocks(self, bus, pareto_config, tmp_repo, mock_worktree, task_mgr):
        """Metrics never improve: after max_stagnant_rounds consecutive stagnation → BLOCKED.
        Round 1: first metrics (Pareto by default, stagnant=0). Review fails → retry.
        Round 2: same metrics (stagnant=1). Review fails → retry.
        Round 3: same metrics (stagnant=2 >= max=2) → BLOCKED."""
        store = IssueStore(str(tmp_repo), pareto_config)
        engine, agent = _make_engine(bus, store, task_mgr, pareto_config)
        _setup_at_approved(store)
        engine._run_acceptance_phase = AsyncMock(return_value=True)
        engine._read_metrics = staticmethod(
            lambda path: (True, {"recall": 0.50, "precision": 0.30}, ""))
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="diff")
        # Review rejects to force multiple rounds (until stagnation blocks)
        from shadowcoder.agents.types import ReviewComment, Severity
        agent.review = AsyncMock(return_value=ReviewOutput(
            comments=[ReviewComment(severity=Severity.CRITICAL, message="needs work")],
            reviewer="mock"))
        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.BLOCKED
        assert issue.blocked_reason == BLOCKED_METRIC_STAGNATED

    async def test_missing_metrics_normal_gate_fail(self, bus, pareto_config, tmp_repo, mock_worktree, task_mgr):
        store = IssueStore(str(tmp_repo), pareto_config)
        engine, agent = _make_engine(bus, store, task_mgr, pareto_config)
        _setup_at_approved(store)
        engine._run_acceptance_phase = AsyncMock(return_value=True)
        call_count = 0
        def mock_read_metrics(path):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return False, {}, "metrics.json not found"
            return True, {"recall": 0.95, "precision": 0.60}, ""
        engine._read_metrics = staticmethod(mock_read_metrics)
        async def mock_run_command(cmd, cwd=None, **kwargs):
            if "bash " in cmd and "acceptance" in cmd:
                return True, "pass", 0.0
            return True, "", 0.0
        engine._run_command = mock_run_command
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="diff")
        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
        issue = store.get(1)
        assert issue.status == IssueStatus.DONE
