from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal["identity", "order", "policy"]
OrderTopic = Literal["status", "returns", "cancellations", "unsupported"]
Intent = Literal["identity", "refund/return", "delivery", "product", "order", "unsupported"]


class SupervisorOutput(BaseModel):
    next_agent: AgentName
    intent: Intent
    order_topic: OrderTopic | None = None
    order_id: str | None = None
    user_id: str | None = None
    clarification_question: str | None = None


class IdentityOutput(BaseModel):
    """The small model extracts lookup inputs, not a customer ID."""
    name: str | None = None
    email: str | None = None


class OrderOutput(BaseModel):
    topic: OrderTopic


class PolicyOutput(BaseModel):
    topic: Literal["returns", "refunds", "cancellations", "unsupported"]


class AgentResult(BaseModel):
    """A small shared result for evaluation and future recovery."""
    model_config = ConfigDict(extra="forbid")

    agent: Literal["supervisor", "identity", "order", "policy"]
    status: Literal["completed", "needs_information", "denied", "failed"]
    reason_code: str
    customer_message: str
    question: str | None = None
    references: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    error: str | None = None
