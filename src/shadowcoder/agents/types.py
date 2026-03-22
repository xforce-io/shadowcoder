from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from shadowcoder.core.models import Issue


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ReviewComment:
    severity: Severity
    message: str
    location: str | None = None


@dataclass
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None


@dataclass
class AgentRequest:
    action: str
    issue: Issue
    context: dict
    prompt_override: str | None = None


@dataclass
class DesignOutput:
    document: str
    usage: AgentUsage | None = None


@dataclass
class DevelopOutput:
    summary: str
    files_changed: list[str] = field(default_factory=list)
    usage: AgentUsage | None = None


@dataclass
class ReviewOutput:
    passed: bool                    # kept for backward compat, Engine overrides based on score
    score: int = 50                 # 0-100 confidence score
    comments: list[ReviewComment] = field(default_factory=list)
    reviewer: str = ""
    usage: AgentUsage | None = None


@dataclass
class TestOutput:
    report: str
    success: bool
    passed_count: int | None = None
    total_count: int | None = None
    recommendation: str | None = None
    usage: AgentUsage | None = None


class AgentActionFailed(Exception):
    """Agent tried but could not complete the action."""
    def __init__(self, message: str, partial_output: str = ""):
        self.partial_output = partial_output
        super().__init__(message)
