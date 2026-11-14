from langgraph.graph import END, START, StateGraph

from src.nodes import identity_node, order_node, policy_node, supervisor_node
from src.state import RetailState


def route_supervisor(state: RetailState) -> str:
    if state.get("clarification_question"):
        return "supervisor"
    return state["next_agent"]


def build_graph(checkpointer=None):
    builder = StateGraph(RetailState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("identity", identity_node)
    builder.add_node("order", order_node)
    builder.add_node("policy", policy_node)
    builder.add_edge(START, "supervisor")
    # Loop back only to pause for a human reply; no separate input node.
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {name: name for name in ("supervisor", "identity", "order", "policy")},
    )
    for name in ("identity", "order", "policy"):
        builder.add_edge(name, END)
    return builder.compile(checkpointer=checkpointer)


# LangGraph's development server supplies its own checkpointer.
# Direct Python callers use build_graph(InMemorySaver()) for interrupt/resume.
graph = build_graph()
