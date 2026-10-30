from langgraph.graph import add_messages
from typing import Annotated, Literal ,List, NotRequired 
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage

# State - Retil task
class RetailTask(TypedDict):
    task_id: str
    intent: Literal['refund',"return","delivery","product","order"]
    order_id: str | None 
    item_ids: List[str]
    messages: Annotated[List[AnyMessage], add_messages]
    missing_fields: NotRequired[List[str]]



# Supervisor State
class WorkflowState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    tasks : NotRequired[List[RetailTask]]

    supervisior_state: NotRequired[
        Literal['needs_information','ready','unsupported']
    ]
    order_status : Literal['pending','awaiting_customer','delivered']

