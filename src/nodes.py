from .models import llm
from langgraph.graph import START , END , StateGraph




def greeting(state: str)->str:
    """This is a function to greet people"""
    return state + "Welcome to MAS"


# Langgraph 

builder=StateGraph(str)

# add nodes to our graph
builder.add_node("greeting",greeting)

# add edges
builder.add_edge(START,"greeting")
builder.add_edge("greeting",END)


graph = builder.compile()
