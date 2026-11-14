from langgraph.graph import END, START, StateGraph

from src.nodes import customer_id, identity_node, order_node, policy_node, supervisor_node
from src.state import RetailState


def route_supervisor(state: RetailState) -> str:
    result = state.get("agent_result")
    if result and result["agent"] == "supervisor":
        return END  # Unsupported request or a model failure.
    if state.get("clarification_question"):
        return "supervisor"  # Checkpoint first, then pause for the human.
    if state["next_agent"] == "order" and not customer_id(state):
        return "identity"
    return state["next_agent"]


def route_specialist(state: RetailState) -> str:
    result = state["agent_result"]
    if result["status"] == "needs_information":
        return "supervisor"  # The supervisor owns all human interruptions.
    if (result["agent"] == "identity" and result["status"] == "completed"
            and state["next_agent"] == "order"):
        return "order"  # Continue the original order request after identification.
    return END


def build_graph(checkpointer=None):
    builder = StateGraph(RetailState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("identity", identity_node)
    builder.add_node("order", order_node)
    builder.add_node("policy", policy_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_supervisor,
                                 {name: name for name in ("supervisor", "identity", "order", "policy", END)})
    for name in ("identity", "order", "policy"):
        builder.add_conditional_edges(name, route_specialist,
                                     {target: target for target in ("supervisor", "order", END)})
    return builder.compile(checkpointer=checkpointer)


# The development server supplies checkpointing. Direct callers supply their own.
graph = build_graph()
