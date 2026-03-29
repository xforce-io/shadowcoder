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
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.config import Config
from shadowcoder.core.models import IssueStatus
from shadowcoder.agents.types import (
    AcceptanceOutput, DevelopOutput, ReviewOutput,
)


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


_STUB_ACCEPTANCE = AcceptanceOutput(
    script="#!/bin/bash\nset -euo pipefail\ntest -f .dev_done\n")


def _make_metric_config(tmp_path, targets, max_retries=3):
    """Create config with metric_gate section."""
    targets_yaml = "\n".join(f'  {k}: "{v}"' for k, v in targets.items())
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "clouds:\n  local:\n    env: {}\n"
        "models:\n  m:\n    cloud: local\n    model: sonnet\n"
        "agents:\n  a:\n    type: claude_code\n    model: m\n"
        "dispatch:\n  design: a\n  develop: a\n  design_review: [a]\n  develop_review: [a]\n"
        f"review_policy:\n  max_review_rounds: 5\n  max_metric_retries: {max_retries}\n"
        f"  pass_threshold: no_critical\n"
        f"metric_gate:\n{targets_yaml}\n"
    )
    return Config(str(config_path))


def _setup_approved(store):
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)


class TestMetricGateInDevelopLoop:
    """Integration tests for metric gate check inside _run_develop_cycle."""

    @pytest.fixture
    def bus(self):
        return MessageBus()

    @pytest.fixture
    def wt(self):
        wt = AsyncMock()
        wt.ensure = AsyncMock(return_value="/tmp/wt")
        wt.exists = AsyncMock(return_value=True)
        wt.cleanup = AsyncMock()
        wt.save_checkpoint = AsyncMock(return_value="abc123")
        wt.revert_to = AsyncMock(return_value=True)
        return wt

    @pytest.fixture
    def agent(self):
        a = AsyncMock()
        a.write_acceptance_script = AsyncMock(return_value=_STUB_ACCEPTANCE)
        a.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
        a.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
        return a

    async def test_metric_gate_pass(self, bus, wt, agent, tmp_repo, tmp_path):
        """Metrics meet baselines → proceed to review → DONE."""
        config = _make_metric_config(tmp_path, {"recall": ">= 0.50"})
        store = IssueStore(str(tmp_repo), config)
        _setup_approved(store)

        reg = MagicMock()
        reg.get = MagicMock(return_value=agent)
        engine = Engine(bus, store, TaskManager(wt), reg, config, str(tmp_repo))
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)
        engine._read_metrics = staticmethod(
            lambda wp: (True, {"recall": 0.96}, ""))

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE

    async def test_metric_gate_fail_reverts_and_retries(self, bus, wt, agent, tmp_repo, tmp_path):
        """Metrics below baseline → revert checkpoint → retry."""
        config = _make_metric_config(tmp_path, {"recall": ">= 0.50"}, max_retries=2)
        store = IssueStore(str(tmp_repo), config)
        _setup_approved(store)

        reg = MagicMock()
        reg.get = MagicMock(return_value=agent)
        engine = Engine(bus, store, TaskManager(wt), reg, config, str(tmp_repo))
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        call_count = 0
        def mock_read(wp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (True, {"recall": 0.05}, "")
            return (True, {"recall": 0.96}, "")

        engine._read_metrics = staticmethod(mock_read)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        wt.revert_to.assert_called_once()
        assert call_count == 2

    async def test_metric_gate_exhausted_blocks(self, bus, wt, agent, tmp_repo, tmp_path):
        """Metrics fail repeatedly → max retries → BLOCKED."""
        config = _make_metric_config(tmp_path, {"recall": ">= 0.50"}, max_retries=2)
        store = IssueStore(str(tmp_repo), config)
        _setup_approved(store)

        reg = MagicMock()
        reg.get = MagicMock(return_value=agent)
        engine = Engine(bus, store, TaskManager(wt), reg, config, str(tmp_repo))
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)
        engine._read_metrics = staticmethod(
            lambda wp: (True, {"recall": 0.05}, ""))

        failed_events = []
        bus.subscribe(MessageType.EVT_TASK_FAILED, lambda m: failed_events.append(m))

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.BLOCKED
        assert len(failed_events) >= 1
        assert "metric gate" in failed_events[-1].payload["reason"]

    async def test_metric_gate_missing_metrics_normal_fail(self, bus, wt, agent, tmp_repo, tmp_path):
        """Missing metrics.json → treated as normal gate fail, not metric revert."""
        config = _make_metric_config(tmp_path, {"recall": ">= 0.50"})
        store = IssueStore(str(tmp_repo), config)
        _setup_approved(store)

        reg = MagicMock()
        reg.get = MagicMock(return_value=agent)
        engine = Engine(bus, store, TaskManager(wt), reg, config, str(tmp_repo))
        engine._gate_check = AsyncMock(return_value=(True, "ok", "", 0.0))
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        call_count = 0
        def mock_read(wp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (False, {}, "metrics.json not found")
            return (True, {"recall": 0.96}, "")

        engine._read_metrics = staticmethod(mock_read)

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        # revert_to should NOT be called for missing metrics
        wt.revert_to.assert_not_called()

    async def test_acceptance_runs_after_review_pass(self, bus, wt, agent, tmp_repo, tmp_path):
        """Acceptance runs only after review passes, not every round."""
        config = _make_metric_config(tmp_path, {})  # no metric gate
        store = IssueStore(str(tmp_repo), config)
        _setup_approved(store)

        reg = MagicMock()
        reg.get = MagicMock(return_value=agent)
        engine = Engine(bus, store, TaskManager(wt), reg, config, str(tmp_repo))

        gate_calls = 0
        async def gate_side(*args, **kw):
            nonlocal gate_calls
            gate_calls += 1
            if gate_calls == 1:
                return False, "fail", "err", 0.0
            return True, "ok", "", 0.0
        engine._gate_check = AsyncMock(side_effect=gate_side)
        engine._get_code_diff = AsyncMock(return_value="")
        engine._run_acceptance_phase = AsyncMock(return_value=True)

        # Acceptance script exists on disk
        acc_path = engine._acceptance_script_path(1)
        acc_path.parent.mkdir(parents=True, exist_ok=True)
        acc_path.write_text("#!/bin/bash\ntest -f ok\n")

        run_cmd_calls = []
        async def mock_run_cmd(cmd, cwd=None, **kw):
            run_cmd_calls.append(cmd)
            if "acceptance" in cmd:
                return True, "", 0.0
            return True, "", 0.0
        engine._run_command = mock_run_cmd

        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

        assert store.get(1).status == IssueStatus.DONE
        # Acceptance runs: 1 pre-loop validation + 1 post-review check
        # (NOT once per develop round)
        acc_cmds = [c for c in run_cmd_calls if "acceptance" in c]
        assert len(acc_cmds) == 2  # pre-loop validation + post-review
