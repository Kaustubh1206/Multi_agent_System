from .models import llm
from langgraph.graph import START , END , StateGraph




def greeting(state: str)->str:
    """This is a function to greet people"""
    return state + "Welcome to MAS"


def run