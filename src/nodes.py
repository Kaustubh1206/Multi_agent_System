import json
from .models import llm
from .prompts import supervisor_system_prompt
from .state import RetailState
from .objects import SupervisorOutput

from uuid import uuid4


from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph import START , END , StateGraph
from langgraph.types import interrupt

# Supervisor llm
supervisor_llm=llm.with_structured_output(SupervisorOutput)


def supervisor_node(state: RetailState)-> dict:
    # Give the supervisor identifiers retained from earlier turns

    known_information ={
        "order_id" : state.get("order_id"),
        "user_id" : state.get("user_id"),
    }


    system_prompt = f"""
    {supervisor_system_prompt}

    Output instructions:
    - Select next_agent from identity, order, or policy.
    - Read the conversation, including answers to earlier questions.
    - Use known identifiers when relevant.
    - If required information is missing, populate clarification_question.
    - Otherwise, set clarification_question to null.

    Previously extracted identifiers:
    {json.dumps(known_information)}
    """

    response = supervisor_llm.invoke([
    SystemMessage(content=system_prompt),
    *state["messages"],
    ])

    return {
        "intent": response.intent,
        "next_agent": response.next_agent,
        "order_id": (
            response.order_id
            if response.order_id is not None
            else state.get("order_id")
        ),
        "user_id": (
            response.user_id
            if response.user_id is not None
            else state.get("user_id")
        ),
        "customer_input": response.customer_input,
    }


def customer_input_node(state: RetailState) -> dict:
    question = state["customer_input"]

    # Pause execution. The application displays this question.
    customer_reply = interrupt({"question": question})

    # Runs after the application resumes with the customer's answer.
    return {
        "messages": [
            AIMessage(content=question),
            HumanMessage(content=customer_reply),
        ],
        "customer_input": None,
    }

# Placeholders: replace these with actual specialist implementations.
def identity_node(state: RetailState) -> dict:
    return {
        "messages": [
            AIMessage(content="Your request reached the identity agent.")
        ]
    }


def order_node(state: RetailState) -> dict:
    return {
        "messages": [
            AIMessage(content="Your request reached the order agent.")
        ]
    }


def policy_node(state: RetailState) -> dict:
    return {
        "messages": [
            AIMessage(content="Your request reached the policy agent.")
        ]
    }