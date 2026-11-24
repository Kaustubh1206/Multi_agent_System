"""A four-role LangGraph adapter for the real τ-bench orchestrator.

Only the orchestrator executes tools. This agent sees conversation, source policy
and tool schemas; it never receives a task, reference actions or gold database.
"""

import copy
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from tau2.agent.base_agent import HalfDuplexAgent, is_valid_agent_history_message
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.utils.text_match import fold_for_match

from .objects import Route, Session, Turn
from .prompts import COMMON, SPECIALISTS, SUPERVISOR, VERSION

AUTH_TOOLS = {"find_user_id_by_email", "find_user_id_by_name_zip"}
WRITE_TOOLS = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "return_delivered_order_items",
    "modify_pending_order_items",
    "modify_pending_order_address",
    "modify_pending_order_payment",
    "modify_user_address",
}
CONFIRM = re.compile(
    r"(?:yes(?:,?\s+(?:please(?:\s+(?:proceed|do (?:it|that)))?|go ahead|proceed|that(?:'s| is) (?:correct|right)|I confirm))?|I confirm|confirmed|confirm|go ahead|please proceed|proceed)[.! ]*",
    re.I,
)


def action_key(name, arguments):
    return name + ":" + json.dumps(arguments, sort_keys=True)


def lc_history(history):
    result = []
    for message in history:
        if isinstance(message, UserMessage):
            result.append(HumanMessage(content=message.content or ""))
        elif isinstance(message, AssistantMessage):
            calls = [
                {"id": c.id, "name": c.name, "args": c.arguments} for c in message.tool_calls or []
            ]
            result.append(AIMessage(content=message.content or "", tool_calls=calls))
        elif isinstance(message, ToolMessage):
            result.append(
                LCToolMessage(
                    content=message.content or "",
                    tool_call_id=message.id,
                    status="error" if message.error else "success",
                )
            )
    return result


def confirmation_text(name, arguments):
    labels = {
        "cancel_pending_order": "cancel this order",
        "return_delivered_order_items": "request this return",
        "exchange_delivered_order_items": "request this exchange",
        "modify_user_address": "update your default address",
        "modify_pending_order_address": "update this order's address",
        "modify_pending_order_items": "change these order items",
        "modify_pending_order_payment": "change this order's payment method",
    }
    fields = {
        "order_id": "Order",
        "item_ids": "Items",
        "new_item_ids": "Replacement items",
        "payment_method_id": "Payment method",
        "reason": "Reason",
        "user_id": "Customer",
    }
    details = "; ".join(
        f"{fields.get(k, k.replace('_', ' ').capitalize())}: "
        + (", ".join(map(str, v)) if isinstance(v, list) else str(v))
        for k, v in arguments.items()
    )
    complete = (
        " Please confirm this includes all items you want to exchange."
        if name == "exchange_delivered_order_items"
        else ""
    )
    return f"I can {labels[name]}. {details}.{complete} Reply yes to confirm, or tell me what to change."


class NativeRetailAgent(HalfDuplexAgent[Session]):
    """Native role routing, source tool calls, and confirmation/ownership guards."""

    def __init__(
        self,
        tools,
        domain_policy,
        supervisor_model="qwen3:4b",
        specialist_model="qwen3:0.6b",
        base_url="http://localhost:11434",
        num_ctx=16384,
        progress=False,
    ):
        super().__init__(tools, domain_policy)
        self.by_name = {t.name: t for t in tools}
        self.last_session = None
        self.progress = progress
        supervisor = ChatOllama(
            model=supervisor_model,
            base_url=base_url,
            temperature=0,
            num_ctx=num_ctx,
            num_predict=512,
            reasoning=False,
        )
        specialist = ChatOllama(
            model=specialist_model,
            base_url=base_url,
            temperature=0,
            num_ctx=num_ctx,
            num_predict=1024,
            reasoning=False,
        )
        self.models = (supervisor, specialist)
        self.router = supervisor.with_structured_output(Route)
        self.role_tools = {
            "identity": set(self.by_name) & (AUTH_TOOLS | {"transfer_to_human_agents"}),
            "order": set(self.by_name) - AUTH_TOOLS,
            "policy": set(self.by_name) & {"transfer_to_human_agents"},
        }
        self.specialists = {
            role: specialist.bind_tools([self.by_name[n].openai_schema for n in sorted(names)])
            for role, names in self.role_tools.items()
        }
        graph = StateGraph(Turn)
        graph.add_node("supervisor", self.supervise)
        for role in SPECIALISTS:
            graph.add_node(role, lambda turn, role=role: self.specialize(turn, role))
            graph.add_edge(role, END)
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor", lambda turn: turn["route"], {role: role for role in SPECIALISTS}
        )
        self.graph = graph.compile()

    def get_init_state(self, message_history=None):
        history = list(message_history or [])
        if not all(is_valid_agent_history_message(m) for m in history):
            raise ValueError("Invalid native agent history")
        if any(not isinstance(m, AssistantMessage) or m.tool_calls for m in history):
            # Do not silently treat an old claimed ID/tool output as a new login.
            raise ValueError("This adapter currently requires a fresh conversation")
        return Session(history=history)

    def set_seed(self, seed):
        """Apply the native per-trial seed to both local models."""
        for model in self.models:
            model.seed = seed

    def event(self, session, kind, **values):
        agent = "supervisor" if kind == "route" else session.active_agent
        session.events.append(
            {
                "step": len(session.events),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "agent": agent,
                "version": VERSION,
                **values,
            }
        )
        if self.progress and kind in {
            "route",
            "tool_requested",
            "confirmation_requested",
            "guard_denied",
        }:
            detail = (
                values.get("tool")
                or values.get("reason")
                or values.get("route", {}).get("agent", "")
            )
            print(f"  {agent}: {kind} {detail}", flush=True)

    def text(self, content):
        return AssistantMessage(role="assistant", content=content)

    def emit(self, session, name, arguments):
        self.by_name[name].params.model_validate(arguments)
        call = ToolCall(id=str(uuid4()), name=name, arguments=copy.deepcopy(arguments))
        session.outstanding = call.model_dump()
        self.event(session, "tool_requested", tool=name, arguments=arguments, call_id=call.id)
        return AssistantMessage(role="assistant", tool_calls=[call])

    def ingest(self, session, message):
        messages = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
        for item in messages:
            if isinstance(item, UserMessage):
                if item.is_audio or item.tool_calls:
                    raise ValueError("Native retail adapter accepts text customer messages only")
            elif isinstance(item, ToolMessage):
                call = session.outstanding
                if not call or item.id != call["id"]:
                    raise ValueError("Tool result does not match the outstanding request")
                session.outstanding = None
                self.event(
                    session,
                    "tool_result",
                    tool=call["name"],
                    error=item.error,
                    execution_status=item.execution_status,
                    result=item.content,
                    call_id=item.id,
                )
                if not item.error:
                    try:
                        data = json.loads(item.content or "null")
                    except json.JSONDecodeError:
                        data = (
                            item.content
                        )  # Upstream serializes string results without JSON quotes.
                    name = call["name"]
                    if name in AUTH_TOOLS and isinstance(data, str) and data:
                        if session.user_id and data != session.user_id:
                            raise ValueError("A conversation cannot change customer")
                        session.user_id = data
                    elif name in {"get_user_details", "modify_user_address"}:
                        if data.get("user_id") != session.user_id:
                            raise ValueError("Unexpected user result")
                        session.profile = data
                        if session.active_agent == "identity":
                            session.active_agent = "supervisor"
                    elif isinstance(data, dict) and "order_id" in data:
                        if data.get("user_id") != session.user_id:
                            raise ValueError("Unexpected order owner")
                        session.orders[data["order_id"]] = data
                elif session.pending and session.pending.get("phase") == "rechecking":
                    session.pending = None
            else:
                raise ValueError("Unexpected native agent input")
            session.history.append(item)

    def supervise(self, turn):
        session = turn["session"]
        if not session.user_id:
            route = Route(agent="identity")
        elif isinstance(session.history[-1], ToolMessage) and session.active_agent in SPECIALISTS:
            route = Route(agent=session.active_agent)
        else:
            route = self.router.invoke(
                [
                    SystemMessage(content=SUPERVISOR + "\nSource policy:\n" + self.domain_policy),
                    *lc_history(session.history),
                ]
            )
            if route.agent == "identity":
                route.agent = "order"  # One authenticated customer per conversation.
        session.active_agent = route.agent
        self.event(session, "route", route=route.model_dump())
        return {"session": session, "route": route.agent}

    def specialize(self, turn, role):
        session = turn["session"]
        context = json.dumps(
            {"authenticated_user_id": session.user_id, "known_order_ids": list(session.orders)},
            sort_keys=True,
        )
        proposal = self.specialists[role].invoke(
            [
                SystemMessage(
                    content=COMMON
                    + "\n"
                    + SPECIALISTS[role]
                    + "\nSource policy:\n"
                    + self.domain_policy
                    + "\nKnown context:\n"
                    + context
                ),
                *lc_history(session.history),
            ]
        )
        if proposal.invalid_tool_calls:
            raise ValueError("Invalid structured tool call from specialist")
        if proposal.tool_calls:
            call = proposal.tool_calls[0]  # Serialize calls so no write bypasses a turn boundary.
            self.event(session, "proposal", proposed_tools=proposal.tool_calls)
            response = self.guarded_call(session, role, call["name"], call["args"])
        else:
            if not isinstance(proposal.content, str) or not proposal.content.strip():
                raise ValueError("Specialist produced no customer response")
            response = self.text(proposal.content)
        return {"session": session, "response": response}

    def guarded_call(self, session, role, name, arguments):
        if name not in self.role_tools[role]:
            self.event(session, "guard_denied", reason="tool_outside_role", tool=name)
            return self.text("I cannot perform that operation through this specialist.")
        self.by_name[name].params.model_validate(arguments)
        if name == "transfer_to_human_agents":
            return self.emit(session, name, arguments)
        if name in AUTH_TOOLS:
            supplied = fold_for_match(
                " ".join(m.content or "" for m in session.history if isinstance(m, UserMessage))
            )
            if session.user_id or not all(
                fold_for_match(str(v)) in supplied for v in arguments.values()
            ):
                self.event(session, "guard_denied", reason="identity_not_supplied")
                return self.text(
                    "Please provide your email address, or your first name, last name and zip code."
                )
            return self.emit(session, name, arguments)
        if not session.user_id or not session.profile:
            return self.text("Please identify yourself by email, or by name and zip code, first.")
        if "user_id" in arguments and arguments["user_id"] != session.user_id:
            return self.text("I can only help with the identified customer's profile and orders.")
        oid = arguments.get("order_id")
        if oid and oid not in session.profile.get("orders", []):
            self.event(session, "guard_denied", reason="order_ownership", order_id=oid)
            return self.text("This order is unavailable for the identified customer.")
        if name in WRITE_TOOLS:
            key = action_key(name, arguments)
            if key in session.attempted_writes:
                return self.text(
                    "That exact change was already attempted. I cannot repeat it without verifying its outcome."
                )
            if oid and oid not in session.orders:
                return self.emit(session, "get_order_details", {"order_id": oid})
            snapshot = session.orders[oid] if oid else session.profile
            session.pending = {
                "name": name,
                "arguments": copy.deepcopy(arguments),
                "phase": "awaiting_confirmation",
                "snapshot": copy.deepcopy(snapshot),
            }
            self.event(session, "confirmation_requested", tool=name, arguments=arguments)
            return self.text(confirmation_text(name, arguments))
        return self.emit(session, name, arguments)

    def confirmed_action(self, session, message):
        pending = session.pending
        if not pending:
            return None
        if isinstance(message, UserMessage):
            if not CONFIRM.fullmatch((message.content or "").strip()):
                session.pending = (
                    None  # A correction, refusal, or question never confirms old arguments.
                )
                self.event(session, "confirmation_not_given")
                if re.fullmatch(
                    r"(?:no|no thanks|cancel that|do not proceed)[.! ]*",
                    message.content or "",
                    re.I,
                ):
                    return self.text("I have not made that change.")
                return None
            pending["phase"] = "rechecking"
            args = pending["arguments"]
            self.event(session, "confirmation_given", tool=pending["name"], arguments=args)
            if "order_id" in args:
                return self.emit(session, "get_order_details", {"order_id": args["order_id"]})
            return self.emit(session, "get_user_details", {"user_id": session.user_id})
        if pending["phase"] == "rechecking":
            args = pending["arguments"]
            current = (
                session.orders.get(args["order_id"]) if "order_id" in args else session.profile
            )
            session.pending = None
            if current != pending["snapshot"]:
                self.event(session, "guard_denied", reason="state_changed_before_write")
                return self.text(
                    "The record changed after I described the action. I have not made the change; it needs a new check and confirmation."
                )
            session.attempted_writes.add(action_key(pending["name"], args))
            return self.emit(session, pending["name"], args)
        return None

    def generate_next_message(self, message, state):
        self.ingest(state, message)
        response = self.confirmed_action(state, message)
        if response is None and isinstance(message, ToolMessage) and not message.error:
            latest = next((e for e in reversed(state.events) if e["kind"] == "tool_result"), {})
            if latest.get("tool") == "transfer_to_human_agents":
                response = self.text("YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.")
        if response is None and state.user_id and state.profile is None:
            response = self.emit(state, "get_user_details", {"user_id": state.user_id})
        if response is None:
            response = self.graph.invoke({"session": state})["response"]
        state.history.append(response)
        self.last_session = state
        return response, state


def create_native_agent(tools, domain_policy, **kwargs):
    """Ignore the registry's task argument; only public policy/tools reach the MAS."""
    settings = kwargs.get("llm_args") or {}
    return NativeRetailAgent(
        tools,
        domain_policy,
        supervisor_model=kwargs.get("llm", "ollama_chat/qwen3:4b").removeprefix("ollama_chat/"),
        specialist_model=settings.get("specialist_model", "qwen3:0.6b"),
        base_url=settings.get("base_url", "http://localhost:11434"),
        num_ctx=settings.get("num_ctx", 16384),
        progress=settings.get("progress", False),
    )
