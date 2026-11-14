import json

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.types import interrupt

from .models import supervisor_model
from .objects import SupervisorOutput
from .prompts import supervisor_system_prompt
from .state import RetailState


supervisor_llm = supervisor_model.with_structured_output(SupervisorOutput)


def supervisor_node(state: RetailState) -> dict:
    new_messages = []
    question = state.get("clarification_question")
    if question:
        # This decision was checkpointed on the previous supervisor step.
        # Resume this same thread with Command(resume="the human's answer").
        reply = interrupt({"question": question})
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Resume with a non-empty human reply string.")
        new_messages.append(HumanMessage(content=reply))

    known_information = {
        "order_id": state.get("order_id"),
        "user_id": state.get("user_id"),
    }
    system_prompt = f"""{supervisor_system_prompt}

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
        *new_messages,
    ])
    decision = response.model_dump()
    for field in ("order_id", "user_id"):
        if decision[field] is None:
            decision[field] = state.get(field)

    if response.clarification_question:
        new_messages.append(AIMessage(content=response.clarification_question))

    return {
        **decision,
        "messages": new_messages,
        "supervisor_history": [decision],
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
