from typing import Literal, NotRequired
from pydantic import BaseModel, Field

class SupervisorOutput(BaseModel):
    next_agent : Literal['identity','order','policy']
    intent: Literal["refund/return","delivery","product","order",]
    order_id: str | None = None
    user_id: str | None = None
    clarification_question:str | None = None