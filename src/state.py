from langgraph.graph import add_messages
from typing import Annotated, Literal ,List, NotRequired 
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage

# State - Retil task
class RetailState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    intent: Literal["refund/return","delivery","product","order"]
    order_id: str | None 
    user_id: str | None
    next_agent: NotRequired[Literal["identity", "order", "policy"]]
    clarification_question: NotRequired[str | None]