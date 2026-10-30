from typing import Literal
from pydantic import BaseModel, Field

class SupervisorOuput(BaseModel):
    next_agent : Literal['identity','order','policy']
    