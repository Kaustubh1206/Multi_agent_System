import os
from uuid import uuid4

# Run locally without sending traces to LangSmith.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.graphs import build_graph


graph = build_graph(InMemorySaver())
config = {"configurable": {"thread_id": str(uuid4())}}

result = graph.invoke(
    {"messages": [HumanMessage(content=input("You: "))]},
    config,
)

while result.get("__interrupt__"):
    question = result["__interrupt__"][0].value["question"]
    print(f"Agent: {question}")
    reply = input("You: ")
    result = graph.invoke(Command(resume=reply), config)

print("Agent:", result["agent_result"]["customer_message"])