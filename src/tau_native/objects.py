"""Small native agent state and structured supervisor output."""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class Route(BaseModel):
    agent: Literal["identity", "order", "policy"]
    plan: list[str] = Field(default_factory=list)


class IdentityFields(BaseModel):
    """Customer-supplied lookup inputs, never an authenticated user ID."""

    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    zip: str | None = None


class Session(BaseModel):
    history: list[Any] = Field(default_factory=list)
    user_id: str | None = None
    profile: dict | None = None
    identity_fields: IdentityFields = Field(default_factory=IdentityFields)
    identity_attempts: set[str] = Field(default_factory=set)
    orders: dict[str, dict] = Field(default_factory=dict)
    active_agent: str = "identity"
    outstanding: dict | None = None
    pending: dict | None = None
    attempted_writes: set[str] = Field(default_factory=set)
    events: list[dict] = Field(default_factory=list)


class Turn(TypedDict):
    session: Session
    route: str
    response: Any
