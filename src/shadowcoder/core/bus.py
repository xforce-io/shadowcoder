from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


class MessageType(Enum):
    CMD_CREATE_ISSUE = "cmd.create_issue"
    CMD_DESIGN = "cmd.design"
    CMD_DEVELOP = "cmd.develop"
    CMD_TEST = "cmd.test"
    CMD_LIST = "cmd.list"
    CMD_INFO = "cmd.info"
    CMD_RESUME = "cmd.resume"
    CMD_APPROVE = "cmd.approve"
    CMD_CANCEL = "cmd.cancel"
    CMD_CLEANUP = "cmd.cleanup"

    EVT_ISSUE_CREATED = "evt.issue_created"
    EVT_STATUS_CHANGED = "evt.status_changed"
    EVT_AGENT_OUTPUT = "evt.agent_output"
    EVT_REVIEW_RESULT = "evt.review_result"
    EVT_TASK_STARTED = "evt.task_started"
    EVT_TASK_COMPLETED = "evt.task_completed"
    EVT_TASK_FAILED = "evt.task_failed"
    EVT_ISSUE_LIST = "evt.issue_list"
    EVT_ISSUE_INFO = "evt.issue_info"
    EVT_ERROR = "evt.error"


@dataclass
class Message:
    type: MessageType
    payload: dict
    task_id: str | None = None


class MessageBus:
    def __init__(self):
        self._handlers: dict[MessageType, list[Callable]] = {}

    def subscribe(self, msg_type: MessageType, handler: Callable):
        self._handlers.setdefault(msg_type, []).append(handler)

    async def publish(self, message: Message):
        for handler in self._handlers.get(message.type, []):
            try:
                await handler(message)
            except Exception:
                logger.exception("Handler failed for %s", message.type)
