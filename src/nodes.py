import json
import re
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import ValidationError

from .models import specialist_model, supervisor_model
from .objects import AgentResult, IdentityOutput, OrderOutput, PolicyOutput, SupervisorOutput
from .prompts import (identity_system_prompt, order_system_prompt,
                      policy_system_prompt, supervisor_system_prompt)
from .state import RetailState
from .tools import check_order_eligibility, get_order, get_policy, identify_customer


supervisor_llm = supervisor_model.with_structured_output(SupervisorOutput).with_config(
    run_name="supervisor_decision", metadata={"prompt_version": "retail-v3"}
)
identity_llm = specialist_model.with_structured_output(IdentityOutput).with_config(
    run_name="identity_extraction", metadata={"prompt_version": "retail-v3"}
)
order_llm = specialist_model.with_structured_output(OrderOutput).with_config(
    run_name="order_classification", metadata={"prompt_version": "retail-v3"}
)
policy_llm = specialist_model.with_structured_output(PolicyOutput).with_config(
    run_name="policy_classification", metadata={"prompt_version": "retail-v3"}
)


def call_tool(tool, arguments, events):
    """Native tool spans plus a compact state record linked to the agent result."""
    event = {"id": str(uuid4()), "tool": tool.name, "arguments": arguments,
             "started_at": datetime.now(timezone.utc).isoformat()}
    started = perf_counter()
    events.append(event)
    try:
        event["result"] = tool.invoke(arguments, config={"metadata": {"evidence_id": event["id"]}})
        return event["result"]
    except Exception as exc:
        event["error"] = type(exc).__name__
        event["error_message"] = str(exc)
        raise
    finally:
        event["duration_ms"] = round((perf_counter() - started) * 1000, 3)


def finish(result: AgentResult, events=None, **updates):
    events = events or []
    data = result.model_dump()
    return {
        "agent_result": data,
        "agent_history": [data],
        "tool_history": events,
        "messages": [AIMessage(content=result.customer_message)],
        "clarification_question": result.question,
        "pending_agent": result.agent if result.status == "needs_information" else None,
        **updates,
    }


def failure(agent, exc, events):
    reason = "model_error"
    if events and "error" in events[-1]:
        reason = "tool_error"
    elif isinstance(exc, (ValidationError, OutputParserException)):
        reason = "invalid_model_output"
    return finish(AgentResult(
        agent=agent, status="failed", reason_code=reason,
        customer_message="I could not complete this step. Please try again later.",
        evidence=[event["id"] for event in events], error=type(exc).__name__,
    ), events)


def customer_id(state):
    return state.get("verified_user_id") or state.get("identity_state", {}).get("customer_id")


ORDER_ID = re.compile(r"\bORD-\d+\b", re.IGNORECASE)
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def latest_order_id(messages):
    # Only human text supplies identifiers; assistant/model text is not evidence.
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            if match := ORDER_ID.search(message.content):
                return match.group().upper()
    return None


def supervisor_node(state: RetailState) -> dict:
    new_messages = []
    if question := state.get("clarification_question"):
        reply = interrupt({"question": question})
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Resume with a non-empty human reply string.")
        new_messages.append(HumanMessage(content=reply.strip()))

    messages = [*state["messages"], *new_messages]
    order_id = latest_order_id(messages) or state.get("order_id")
    pending = state.get("pending_agent")
    previous_result = state.get("agent_result") or {}
    if not pending and previous_result.get("status") == "needs_information":
        pending = previous_result["agent"]  # Also resume checkpoints made before this field existed.
    if new_messages and pending:
        # A name/email/order-ID answer belongs to the saved task, not a new intent.
        decision = {"intent": state.get("intent"), "next_agent": state["next_agent"],
                    "order_id": order_id, "user_id": state.get("user_id"),
                    "clarification_question": None}
        return {**decision, "messages": new_messages, "agent_result": None,
                "pending_agent": pending, "supervisor_history": [{**decision, "source": "human_resume", "resume_agent": pending}]}

    context = {"order_id": order_id, "user_id": state.get("user_id"),
               "identified_customer_id": customer_id(state)}
    try:
        response = supervisor_llm.invoke([
            SystemMessage(content=supervisor_system_prompt + "\nKnown state: " + json.dumps(context)),
            *messages,
        ])
    except Exception as exc:
        update = failure("supervisor", exc, [])
        update["messages"] = new_messages + update["messages"]
        return {**update, "order_id": order_id}

    decision = response.model_dump()
    # Preserve explicit identifiers even if the classifier omits/hallucinates them.
    decision["order_id"] = order_id
    decision["user_id"] = response.user_id or state.get("user_id")
    if response.intent == "unsupported":
        update = finish(AgentResult(
            agent="supervisor", status="denied", reason_code="unsupported_request",
            customer_message="I can help with retail identity, order, and policy questions.",
        ))
        update["messages"] = new_messages + update["messages"]
        return {**decision, **update, "supervisor_history": [decision]}
    if response.intent in {"order", "delivery"}:
        decision["next_agent"] = "order"
    if decision["next_agent"] == "order":
        # Required fields are checked in code, not in a model-generated question.
        decision["clarification_question"] = None if order_id else "What is your order ID?"
        pending = "order" if not order_id else None
    else:
        pending = None
    if decision["clarification_question"]:
        new_messages.append(AIMessage(content=decision["clarification_question"]))
    return {**decision, "messages": new_messages, "agent_result": None,
            "pending_agent": pending, "supervisor_history": [decision]}


def identity_node(state: RetailState) -> dict:
    events = []
    try:
        human_messages = [message for message in state["messages"]
                          if isinstance(message, HumanMessage) and isinstance(message.content, str)]
        # A correction replaces earlier lookup inputs instead of combining stale data.
        inputs = human_messages[-1:] if state.get("pending_agent") == "identity" else human_messages
        email_message = next((message for message in reversed(inputs) if EMAIL.search(message.content)), None)
        email = EMAIL.search(email_message.content).group() if email_message else None
        if email_message and EMAIL.fullmatch(email_message.content.strip(" <>")):
            identity = {"name": None, "email": email}
        else:
            lookup = identity_llm.invoke([
                SystemMessage(content=identity_system_prompt), *inputs,
            ])
            # Explicit email wins over omitted/mistyped model extraction. A model
            # name must occur in the input, and alongside that email if both exist.
            name_sources = [email_message] if email_message else inputs
            name = lookup.name
            if not name or not any(name.casefold() in message.content.casefold() for message in name_sources):
                name = None
            identity = {"name": name, "email": email}
        found = call_tool(identify_customer, identity, events)
        if found["status"] != "matched":
            question = ("Please provide your full name or email address."
                        if found["status"] == "missing_information" else
                        "I could not identify a unique customer. Please provide your email or correct full name.")
            return finish(AgentResult(
                agent="identity", status="needs_information", reason_code=found["status"],
                customer_message=question, question=question, evidence=[events[-1]["id"]],
            ), events, identity_state={**identity, "customer_id": None})
        return finish(AgentResult(
            agent="identity", status="completed", reason_code="customer_identified",
            customer_message="I found your customer record.",
            references={"customer_id": found["customer_id"]}, evidence=[events[-1]["id"]],
        ), events, identity_state={**identity, "customer_id": found["customer_id"]})
    except Exception as exc:
        return failure("identity", exc, events)


def order_node(state: RetailState) -> dict:
    events = []
    owner = customer_id(state)
    order_id = state.get("order_id")
    if not owner or not order_id:
        question = ("Please provide your full name or email address." if not owner
                    else "What is your order ID?")
        return finish(AgentResult(
            agent="order", status="needs_information", reason_code="missing_order_context",
            customer_message=question, question=question,
        ))
    references = {"customer_id": owner, "order_id": order_id}
    try:
        request = order_llm.invoke([
            SystemMessage(content=order_system_prompt), *state["messages"],
        ])
        if request.topic == "unsupported":
            return finish(AgentResult(
                agent="order", status="denied", reason_code="unsupported_order_request",
                customer_message="I can check order status and return or cancellation eligibility, but cannot access refund-payment status or perform actions.",
            ))
        lookup = call_tool(get_order, references, events)
        if lookup["status"] != "found":
            return finish(AgentResult(
                agent="order", status="denied", reason_code="order_unavailable",
                customer_message="This order is unavailable for the identified customer.",
                references=references, evidence=[events[-1]["id"]],
            ), events, order_state={"topic": request.topic, "record": {}})
        order = lookup["order"]
        status, reason = "completed", "order_status_found"
        if request.topic == "status":
            message = f"Order {order_id} ({order['product']}) is {order['status']}."
        else:
            eligibility = call_tool(check_order_eligibility,
                                    {**references, "topic": request.topic}, events)
            if eligibility["status"] != "checked":
                raise ValueError("Eligibility lookup did not complete")
            references["policy_version"] = eligibility["policy_version"]
            allowed = eligibility["eligible"]
            status = "completed" if allowed else "denied"
            reason = "return_eligible" if allowed else "return_not_eligible"
            if request.topic == "returns":
                age = eligibility["days_since_delivery"]
                if age is not None and age > 30:
                    reason = "return_window_expired"
                detail = f" It was delivered {age} days ago." if age is not None else " It has not been delivered."
                message = f"Order {order_id} is {'eligible' if allowed else 'not eligible'} for return." + detail
                message += " " + eligibility["rule"] + " No return has been requested."
            else:
                reason = "cancellation_eligible" if allowed else "cancellation_not_eligible"
                message = f"Order {order_id} {'can' if allowed else 'cannot'} be cancelled under the policy."
                message += " " + eligibility["rule"] + " No cancellation has been performed."
        return finish(AgentResult(
            agent="order", status=status, reason_code=reason, customer_message=message,
            references=references, evidence=[event["id"] for event in events],
        ), events, order_state={"topic": request.topic, "record": order})
    except Exception as exc:
        return failure("order", exc, events)


def policy_node(state: RetailState) -> dict:
    events = []
    try:
        request = policy_llm.invoke([
            SystemMessage(content=policy_system_prompt), *state["messages"],
        ])
        policy = call_tool(get_policy, {"topic": request.topic}, events)
        if policy["status"] != "found":
            return finish(AgentResult(
                agent="policy", status="denied", reason_code="policy_not_available",
                customer_message="The current policy covers returns, refunds, and cancellations; it does not answer that question.",
                evidence=[events[-1]["id"]],
            ), events)
        return finish(AgentResult(
            agent="policy", status="completed", reason_code="policy_answered",
            customer_message=policy["answer"], references={"policy_version": policy["version"]},
            evidence=[events[-1]["id"]],
        ), events, policy_state={"topic": request.topic, "version": policy["version"]})
    except Exception as exc:
        return failure("policy", exc, events)
