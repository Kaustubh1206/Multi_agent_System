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
    run_name="supervisor_decision", metadata={"prompt_version": "retail-v5"}
)
identity_llm = specialist_model.with_structured_output(IdentityOutput).with_config(
    run_name="identity_extraction", metadata={"prompt_version": "retail-v5"}
)
order_llm = specialist_model.with_structured_output(OrderOutput).with_config(
    run_name="order_classification", metadata={"prompt_version": "retail-v5"}
)
policy_llm = specialist_model.with_structured_output(PolicyOutput).with_config(
    run_name="policy_classification", metadata={"prompt_version": "retail-v5"}
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
                    "customer_request": state.get("customer_request", ""),
                    "order_topic": state.get("order_topic"), "clarification_question": None}
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
    decision["customer_request"] = next((message.content for message in reversed(messages)
                                         if isinstance(message, HumanMessage)), "")
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
    if response.intent in {"order", "delivery"} and response.next_agent != "policy":
        decision["next_agent"] = "order"
    if decision["next_agent"] == "order":
        if decision["order_topic"] is None:
            decision["order_topic"] = {"order": "status", "delivery": "status",
                                       "refund/return": "returns"}.get(response.intent)
        # Required fields are checked in code, not in a model-generated question.
        decision["clarification_question"] = None if order_id else "What is your order ID?"
        pending = "order" if not order_id else None
    else:
        decision["order_topic"] = None
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



def active_human_texts(state, original_request):
    messages = [m.content for m in state["messages"] if isinstance(m, HumanMessage) and isinstance(m.content, str)]
    start = next((i for i in range(len(messages) - 1, -1, -1) if messages[i] == original_request), 0)
    return messages[start:]


def return_inputs(order, texts):
    """Read explicit selectors/condition answers; never guess a multi-item target."""
    inputs = {}
    for text in texts:
        if match := re.search(r"\bITEM-\d+(?:-[A-Z])?\b", text, re.I):
            inputs["item_id"] = match.group().upper()
        else:
            named = [i for i in order["items"] if i["product_name"].casefold() in text.casefold()]
            if len(named) == 1:
                inputs["item_id"] = named[0]["item_id"]
        if match := re.search(r"\b(?:return|quantity|qty)\s*:?\s*(\d+)\b|\b(\d+)\s+units?\b", text, re.I):
            inputs["quantity"] = int(match.group(1) or match.group(2))
        # An email's local part is not an item-condition declaration.
        text = EMAIL.sub("", text).casefold()
        if re.search(r"\bdamaged\b", text) and not re.search(r"\bnot damaged\b", text):
            inputs["condition"] = "damaged"
        elif re.search(r"\bnot unused\b", text):
            inputs["condition"] = "used"
        elif re.search(r"\bunused\b|\bnot used\b", text):
            inputs["condition"] = "unused"
        elif re.search(r"\btried on\b", text):
            inputs["condition"] = "tried_on"
        elif re.search(r"\bused\b", text):
            inputs["condition"] = "used"
        if re.search(r"\b(?:no|without|not in|not with) (?:the )?original packaging\b", text):
            inputs["packaging"] = False
        elif "original packaging" in text:
            inputs["packaging"] = True
        if re.search(r"\b(?:unsealed|opened)\b", text):
            inputs["seal"] = "opened"
        elif re.search(r"\b(?:sealed|unopened)\b", text):
            inputs["seal"] = "sealed"
    return inputs


def order_status_message(order_id, order):
    if "items" not in order:
        return f"Order {order_id} ({order['product']}) is {order['status']}."
    message = f"Order {order_id} is {order['status']}."
    if order['status'] == 'cancelled':
        return message
    for item in order["items"]:
        message += f" {item['item_id']} ({item['product_name']}) is {item['fulfillment_status']}."
        if item["delivered_on"]:
            message += f" Delivered on {item['delivered_on']}."
    for shipment in order["shipments"]:
        message += f" Shipment {shipment['shipment_id']}: {shipment['tracking_status']}."
        if shipment.get("estimated_delivery_on"):
            message += f" Recorded delivery estimate: {shipment['estimated_delivery_on']} (not a guarantee)."
    return message


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
        original_request = state.get("customer_request") or next(
            (message.content for message in state["messages"] if isinstance(message, HumanMessage)), ""
        )
        assigned_topic = state.get("order_topic")
        topic_source = "supervisor"
        if assigned_topic is None:
            # Compatibility with older checkpoints that stored only the broad intent.
            assigned_topic = {"order": "status", "delivery": "status", "refund/return": "returns"}.get(state.get("intent"))
            topic_source = "supervisor_intent"
        context = {"original_request": original_request, "assigned_topic": assigned_topic,
                   "order_id": order_id}
        request = order_llm.invoke([
            SystemMessage(content=order_system_prompt + "\nAssigned task: " + json.dumps(context)),
            HumanMessage(content=original_request),
        ])
        # Clarification answers and a smaller model's classification cannot change
        # the saved task. Preserve its proposed topic so disagreements remain visible.
        topic = assigned_topic or request.topic
        order_state = {"topic": topic, "model_topic": request.topic,
                       "topic_source": topic_source if assigned_topic else "order_model", "record": {}}
        if topic == "unsupported":
            return finish(AgentResult(
                agent="order", status="denied", reason_code="unsupported_order_request",
                customer_message="I can check order status and return or cancellation eligibility, but cannot access refund-payment status or perform actions.",
            ), order_state=order_state)
        lookup = call_tool(get_order, references, events)
        if lookup["status"] != "found":
            return finish(AgentResult(
                agent="order", status="denied", reason_code="order_unavailable",
                customer_message="This order is unavailable for the identified customer.",
                references=references, evidence=[events[-1]["id"]],
            ), events, order_state=order_state)
        order = lookup["order"]
        status, reason = "completed", "order_status_found"
        if topic == "status":
            message = order_status_message(order_id, order)
        else:
            selectors = return_inputs(order, active_human_texts(state, original_request)) if topic == "returns" and "items" in order else {}
            eligibility = call_tool(check_order_eligibility,
                                    {**references, "topic": topic, **selectors}, events)
            if eligibility["status"] != "checked":
                raise ValueError("Eligibility lookup did not complete")
            references["policy_version"] = eligibility["policy_version"]
            if "outcome" in eligibility:
                status = {"eligible": "completed", "ineligible": "denied",
                          "needs_information": "needs_information", "needs_review": "needs_review"}[eligibility["outcome"]]
                if "item_id" in selectors:
                    references["item_id"] = selectors["item_id"]
                return finish(AgentResult(
                    agent="order", status=status, reason_code=eligibility["reason_code"],
                    customer_message=eligibility["explanation"], question=eligibility.get("question"),
                    references=references, evidence=[event["id"] for event in events],
                ), events, order_state={**order_state, "record": order, "eligibility": eligibility})
            allowed = eligibility["eligible"]
            status = "completed" if allowed else "denied"
            reason = "return_eligible" if allowed else "return_not_eligible"
            if topic == "returns":
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
        ), events, order_state={**order_state, "record": order})
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
