from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MultiAgentRequest(BaseModel):
    objective: str = Field(..., min_length=3)
    operation: Optional[str] = None
    operation_id: Optional[int] = None
    operation_kind: Optional[str] = None
    source_message_id: Optional[str] = None
    transaction: Dict[str, Any] = Field(default_factory=dict)
    market_snapshot: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    live_context: Dict[str, Any] = Field(default_factory=dict)
    rounds: int = Field(default=2, ge=1, le=4)


class AgentMessage(BaseModel):
    agent: str
    role: str
    round: int
    confidence: float
    insights: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    alerts: List[str] = Field(default_factory=list)


class MultiAgentResponse(BaseModel):
    summary: str
    decisions: List[str]
    risks: List[str]
    recommendations: List[str]
    transcript: List[AgentMessage]


@dataclass
class AgentContext:
    request: MultiAgentRequest
    transcript: List[AgentMessage]


class BaseAgent:
    name = "base"
    role = "generic"

    def analyze(self, ctx: AgentContext, round_number: int) -> AgentMessage:
        raise NotImplementedError