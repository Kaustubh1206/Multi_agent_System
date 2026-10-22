from langgraph.graph import add_messages
from typing import Annotated, Literal ,List, NotRequired
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage

# State - Retil task
class RetailTask(TypedDict):
    task_id: str
    intent: Literal['refund',"return","delivery","product","order"]
    order_id: str | None 
    item_id: List[str]

# Supervisor State

class SupervisorState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    tasks : NotRequired[List[RetailTask]]
    missing_fields: NotRequired[List[str]]
    supervisior_state: NotRequired[
        Literal['needs_information','ready','unsupported']
    ]