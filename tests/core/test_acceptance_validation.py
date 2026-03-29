"""Tests for acceptance script corruption detection (P0 fixes).

P0-1: Pre-gate distinguishes script-self-error from business assertion failure.
P0-2: Resume validates existing acceptance.sh before reuse.
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from shadowcoder.core.engine import Engine
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus
from shadowcoder.core.config import Config
from shadowcoder.agents.types import (
    AcceptanceOutput, DevelopOutput, PreflightOutput, ReviewOutput,
)

_GOOD_SCRIPT = AcceptanceOutput(
    script="#!/bin/bash\nset -euo pipefail\ntest -f .dev_done\n")

_CORRUPTED_SCRIPT = AcceptanceOutput(
    script=(
        "#!/bin/bash\nset -euo pipefail\n\n"
        "这是中文思考过程#!/bin/bash\nset -euo pipefail\n"
        "python3 -c 'assert False'\n"
    ))


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def config(tmp_config):
    return Config(str(tmp_config))


@pytest.fixture
def store(tmp_repo, config):
    return IssueStore(str(tmp_repo), config)


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


def make_engine(bus, store, task_mgr, config):
    agent = AsyncMock()
    agent.write_acceptance_script = AsyncMock(return_value=_GOOD_SCRIPT)
    agent.preflight = AsyncMock(return_value=PreflightOutput(
        feasibility="high", estimated_complexity="moderate"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="code"))
    agent.review = AsyncMock(return_value=ReviewOutput(comments=[], reviewer="mock"))
    reg = MagicMock()
    reg.get = MagicMock(return_value=agent)
    engine = Engine(bus, store, task_mgr, reg, config, "/tmp/repo")
    return engine, agent


def _setup_issue_at_approved(store):
    store.create("Test")
    store.transition_status(1, IssueStatus.DESIGNING)
    store.transition_status(1, IssueStatus.DESIGN_REVIEW)
    store.transition_status(1, IssueStatus.APPROVED)


# ---- P0-1: Pre-gate corruption detection ----

async def test_pregate_command_not_found_retries(bus, store, task_mgr, config):
    """Pre-gate: 'command not found' triggers retry, not acceptance."""
    engine, agent = make_engine(bus, store, task_mgr, config)
    _setup_issue_at_approved(store)
    issue = store.get(1)
    task = MagicMock()
    task.worktree_path = "/tmp/wt"

    call_count = 0
    original_run_command = engine._run_command

    async def mock_run_command(cmd, cwd=None, **kwargs):
        nonlocal call_count
        if "bash -n" in cmd:
            return True, "", 0.0
        if "bash " in cmd and "acceptance" in cmd:
            call_count += 1
            if call_count <= 2:
                # First two executions: script corrupted
                return False, "line 3: 这是中文: command not found", 0.0
            else:
                # Third attempt: good script, normal assertion failure
                return False, "AssertionError: expected True", 0.0
        return await original_run_command(cmd, cwd=cwd, **kwargs)

    engine._run_command = mock_run_command

    # First two attempts return corrupted script, third returns good
    attempt_num = 0

    async def cycling_acceptance(request):
        nonlocal attempt_num
        attempt_num += 1
        if attempt_num <= 2:
            return _CORRUPTED_SCRIPT
        return _GOOD_SCRIPT

    agent.write_acceptance_script = AsyncMock(side_effect=cycling_acceptance)

    result = await engine._run_acceptance_phase(issue, task)

    assert result is True
    # Agent was called 3 times: 2 corrupted + 1 good
    assert agent.write_acceptance_script.call_count == 3


async def test_pregate_normal_failure_accepted(bus, store, task_mgr, config):
    """Pre-gate: normal assertion failure is accepted as expected."""
    engine, agent = make_engine(bus, store, task_mgr, config)
    _setup_issue_at_approved(store)
    issue = store.get(1)
    task = MagicMock()
    task.worktree_path = "/tmp/wt"

    async def mock_run_command(cmd, cwd=None, **kwargs):
        if "bash -n" in cmd:
            return True, "", 0.0
        if "bash " in cmd and "acceptance" in cmd:
            return False, "AssertionError: module not found", 0.0
        return True, "", 0.0

    engine._run_command = mock_run_command

    result = await engine._run_acceptance_phase(issue, task)

    assert result is True
    assert agent.write_acceptance_script.call_count == 1


# ---- P0-2: Resume validates existing acceptance.sh ----

async def test_resume_corrupted_acceptance_regenerates(bus, store, task_mgr, config):
    """Resume: corrupted acceptance.sh is deleted and regenerated."""
    engine, agent = make_engine(bus, store, task_mgr, config)
    _setup_issue_at_approved(store)

    # Write a corrupted acceptance.sh to disk
    acceptance_path = engine._acceptance_script_path(1)
    acceptance_path.parent.mkdir(parents=True, exist_ok=True)
    acceptance_path.write_text(
        "#!/bin/bash\nset -euo pipefail\n中文思考\npython3 -c 'assert False'\n")

    # Mock _run_command to return "command not found" for corrupted script
    run_cmd_calls = []

    async def mock_run_command(cmd, cwd=None, **kwargs):
        run_cmd_calls.append(cmd)
        if "bash " in cmd and "acceptance" in cmd:
            if "中文思考" in acceptance_path.read_text():
                return False, "line 3: 中文思考: command not found", 0.0
            return False, "AssertionError: expected", 0.0
        return True, "", 0.0

    engine._run_command = mock_run_command
    engine._run_acceptance_phase = AsyncMock(return_value=True)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", "", 0.0))
    engine._get_code_diff = AsyncMock(return_value="")

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    # Corrupted file should have been deleted and _run_acceptance_phase called
    assert engine._run_acceptance_phase.call_count == 1


async def test_resume_valid_acceptance_skips_regeneration(bus, store, task_mgr, config):
    """Resume: valid acceptance.sh is reused without regeneration."""
    engine, agent = make_engine(bus, store, task_mgr, config)
    _setup_issue_at_approved(store)

    # Write a valid acceptance.sh
    acceptance_path = engine._acceptance_script_path(1)
    acceptance_path.parent.mkdir(parents=True, exist_ok=True)
    acceptance_path.write_text("#!/bin/bash\nset -euo pipefail\ntest -f .dev_done\n")

    async def mock_run_command(cmd, cwd=None, **kwargs):
        if "bash " in cmd and "acceptance" in cmd:
            # Normal failure (file doesn't exist) — not "command not found"
            return False, "test: .dev_done: No such file or directory", 0.0
        return True, "", 0.0

    engine._run_command = mock_run_command
    engine._run_acceptance_phase = AsyncMock(return_value=True)
    engine._gate_check = AsyncMock(return_value=(True, "gate passed", "", 0.0))
    engine._get_code_diff = AsyncMock(return_value="")

    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))

    # Should NOT have called _run_acceptance_phase (script is valid)
    assert engine._run_acceptance_phase.call_count == 0
