from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from shadowcoder.core.models import Issue, ReviewResult


@dataclass
class AgentRequest:
    action: str
    issue: Issue
    context: dict
    prompt_override: str | None = None


@dataclass
class AgentResponse:
    content: str
    success: bool
    metadata: dict | None = None


class AgentStream:
    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


class BaseAgent(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def execute(self, request: AgentRequest) -> AgentResponse:
        ...

    @abstractmethod
    async def stream(self, request: AgentRequest) -> AgentStream:
        ...

    @abstractmethod
    async def review(self, request: AgentRequest) -> ReviewResult:
        ...
