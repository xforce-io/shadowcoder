from __future__ import annotations

from shadowcoder.agents.base import AgentRequest, AgentResponse, AgentStream, BaseAgent
from shadowcoder.core.models import ReviewResult


class ClaudeCodeAgent(BaseAgent):
    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            content=f"[stub] {request.action} output for: {request.issue.title}",
            success=True,
            metadata={"agent": "claude-code", "stub": True},
        )

    async def stream(self, request: AgentRequest) -> AgentStream:
        raise NotImplementedError("Streaming not yet implemented")

    async def review(self, request: AgentRequest) -> ReviewResult:
        return ReviewResult(
            passed=True,
            comments=[],
            reviewer="claude-code",
        )
