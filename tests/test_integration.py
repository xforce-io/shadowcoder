import pytest
from unittest.mock import AsyncMock
from shadowcoder.core.bus import MessageBus, MessageType, Message
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.models import IssueStatus
from shadowcoder.agents.types import DesignOutput, DevelopOutput, ReviewOutput, TestOutput
from shadowcoder.agents.registry import AgentRegistry


async def test_full_lifecycle(tmp_repo, tmp_config):
    """Test create → design → develop → test → done."""
    config = Config(str(tmp_config))

    agent = AsyncMock()
    agent.design = AsyncMock(return_value=DesignOutput(document="output"))
    agent.develop = AsyncMock(return_value=DevelopOutput(summary="output"))
    agent.test = AsyncMock(return_value=TestOutput(report="output", success=True))
    agent.review = AsyncMock(return_value=ReviewOutput(passed=True, comments=[], reviewer="mock"))

    AgentRegistry.register("claude_code", lambda cfg: agent)
    registry = AgentRegistry(config)
    registry._instances["claude-code"] = agent

    bus = MessageBus()
    mock_wt = AsyncMock()
    mock_wt.create = AsyncMock(return_value="/tmp/wt")
    task_mgr = TaskManager(mock_wt)
    store = IssueStore(str(tmp_repo), config)

    engine = Engine(bus, store, task_mgr, registry, config, str(tmp_repo))

    # Create
    await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, {"title": "Full test"}))
    assert store.get(1).status == IssueStatus.CREATED

    # Design
    await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.APPROVED

    # Develop
    await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.TESTING

    # Test
    await bus.publish(Message(MessageType.CMD_TEST, {"issue_id": 1}))
    assert store.get(1).status == IssueStatus.DONE
