from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph

from src.state import RetailState
from src.nodes import (
    supervisor_node,
    clarification_node,
    identity_node,
    order_node,
    policy_node,
)


def route_supervisor(state: RetailState) -> str:
    if state.get("clarification_question"):
        return "clarification"

    return state["next_agent"]


builder = StateGraph(RetailState)

builder.add_node("supervisor", supervisor_node)
builder.add_node("clarification", clarification_node)
builder.add_node("identity", identity_node)
builder.add_node("order", order_node)
builder.add_node("policy", policy_node)

builder.add_edge(START, "supervisor")

builder.add_conditional_edges(
    "supervisor",
    route_supervisor,
    {
        "clarification": "clarification",
        "identity": "identity",
        "order": "order",
        "policy": "policy",
    },
)

# Once the customer answers, reconsider the request.
builder.add_edge("clarification", "supervisor")

builder.add_edge("identity", END)
builder.add_edge("order", END)
builder.add_edge("policy", END)

# Suitable for local development; state survives within this process.
graph = builder.compile()