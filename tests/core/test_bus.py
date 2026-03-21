import pytest
from shadowcoder.core.bus import MessageBus, MessageType, Message


async def test_publish_subscribe():
    bus = MessageBus()
    received = []
    async def handler(msg: Message):
        received.append(msg)
    bus.subscribe(MessageType.EVT_ISSUE_CREATED, handler)
    await bus.publish(Message(MessageType.EVT_ISSUE_CREATED, {"id": 1}))
    assert len(received) == 1
    assert received[0].payload["id"] == 1


async def test_no_subscribers():
    bus = MessageBus()
    await bus.publish(Message(MessageType.EVT_ERROR, {"message": "test"}))


async def test_multiple_subscribers():
    bus = MessageBus()
    results = []
    async def h1(msg): results.append("h1")
    async def h2(msg): results.append("h2")
    bus.subscribe(MessageType.CMD_LIST, h1)
    bus.subscribe(MessageType.CMD_LIST, h2)
    await bus.publish(Message(MessageType.CMD_LIST, {}))
    assert results == ["h1", "h2"]


async def test_handler_exception_isolated():
    bus = MessageBus()
    results = []
    async def bad_handler(msg): raise RuntimeError("boom")
    async def good_handler(msg): results.append("ok")
    bus.subscribe(MessageType.CMD_LIST, bad_handler)
    bus.subscribe(MessageType.CMD_LIST, good_handler)
    await bus.publish(Message(MessageType.CMD_LIST, {}))
    assert results == ["ok"]


async def test_message_with_task_id():
    msg = Message(MessageType.CMD_DESIGN, {"issue_id": 1}, task_id="abc123")
    assert msg.task_id == "abc123"


async def test_different_types_isolated():
    bus = MessageBus()
    results = []
    async def handler(msg): results.append(msg.type)
    bus.subscribe(MessageType.CMD_LIST, handler)
    await bus.publish(Message(MessageType.CMD_DESIGN, {}))
    assert results == []
