from operator import add
from typing import Annotated, Literal, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import TypedDict


class RetailState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    intent: NotRequired[Literal["refund/return", "delivery", "product", "order"]]
    order_id: NotRequired[str | None]
    user_id: NotRequired[str | None]
    # Supplied by the application, never inferred from customer messages.
    verified_user_id: NotRequired[str | None]
    next_agent: NotRequired[Literal["identity", "order", "policy"]]
    clarification_question: NotRequired[str | None]
    # Append each classification, including decisions that ask for clarification.
    supervisor_history: Annotated[list[dict], add]
