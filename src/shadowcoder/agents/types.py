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
    phase: str = ""
    round_num: int = 0


@dataclass
class AgentRequest:
    action: str
    issue: Issue
    context: dict
    prompt_override: str | None = None


@dataclass
class DesignOutput:
    document: str
    test_command: str | None = None
    usage: AgentUsage | None = None


@dataclass
class DevelopOutput:
    summary: str
    files_changed: list[str] = field(default_factory=list)
    usage: AgentUsage | None = None


@dataclass
class FeedbackItem:
    id: str
    category: str
    description: str
    round_introduced: int
    times_raised: int = 1
    resolved: bool = False
    escalation_level: int = 1


@dataclass
class TestCase:
    name: str
    description: str
    expected_behavior: str
    category: str = "acceptance"


@dataclass
class ReviewOutput:
    comments: list[ReviewComment] = field(default_factory=list)
    resolved_item_ids: list[str] = field(default_factory=list)
    proposed_tests: list[TestCase] = field(default_factory=list)
    reviewer: str = ""
    usage: AgentUsage | None = None


@dataclass
class PreflightOutput:
    feasibility: str              # "high" / "medium" / "low"
    estimated_complexity: str     # "simple" / "moderate" / "complex" / "very_complex"
    risks: list[str] = field(default_factory=list)
    tech_stack_recommendation: str | None = None
    usage: AgentUsage | None = None


@dataclass
class AcceptanceOutput:
    script: str
    usage: AgentUsage | None = None


class AgentActionFailed(Exception):
    """Agent tried but could not complete the action."""
    def __init__(self, message: str, partial_output: str = ""):
        self.partial_output = partial_output
        super().__init__(message)
