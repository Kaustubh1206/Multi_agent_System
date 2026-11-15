from operator import add
from typing import Annotated, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import TypedDict

from .objects import AgentName, Intent


class IdentityState(TypedDict, total=False):
    name: str | None
    email: str | None
    customer_id: str | None  # Set only by a successful dataset lookup.


class OrderState(TypedDict, total=False):
    topic: str
    record: dict


class PolicyState(TypedDict, total=False):
    topic: str
    version: str


class RetailState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    intent: NotRequired[Intent]
    order_id: NotRequired[str | None]
    user_id: NotRequired[str | None]  # Conversational ID; not used for authorization.
    verified_user_id: NotRequired[str | None]  # Optional application session input.
    next_agent: NotRequired[AgentName]
    clarification_question: NotRequired[str | None]
    pending_agent: NotRequired[AgentName | None]  # Continue this node after human input.
    identity_state: NotRequired[IdentityState]
    order_state: NotRequired[OrderState]
    policy_state: NotRequired[PolicyState]
    agent_result: NotRequired[dict | None]
    supervisor_history: Annotated[list[dict], add]
    agent_history: Annotated[list[dict], add]
    tool_history: Annotated[list[dict], add]
