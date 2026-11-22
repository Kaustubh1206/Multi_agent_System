"""Run the MAS and DeepEval directly in one process; no saved trace collection step."""
import argparse
import json
import sys
import time
import urllib.request
from uuid import uuid4

from .model import build_judge, eval_model, ollama_url
from .dataset import DATA, TASKS, SKIPPED_TASKS, golden
from .metrics import NAMES, PLAN_METRICS, build_metrics

from deepeval import evaluate
from deepeval.evaluate import AsyncConfig, CacheConfig, DisplayConfig, ErrorConfig
from deepeval.test_case import LLMTestCase, ToolCall
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


def plain(value):
    if hasattr(value, "model_dump"):
        return plain(value.model_dump())
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value if value is None or isinstance(value, (str, bool, int, float)) else str(value)


class EvaluationContext(BaseCallbackHandler):
    """Temporary model evidence required by agentic metrics; never written to disk."""
    def __init__(self):
        self.calls = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self.calls[str(run_id)] = {"type": "llm", "name": kwargs.get("name", "model"),
            "input": plain(messages), "start_time": time.time(), "parent_id": str(parent_run_id)}

    def on_llm_end(self, response, *, run_id, **kwargs):
        if str(run_id) in self.calls:
            self.calls[str(run_id)]["output"] = plain(response)

    def on_llm_error(self, error, *, run_id, **kwargs):
        if str(run_id) in self.calls:
            self.calls[str(run_id)]["error"] = str(error)


def preflight(judge_name):
    with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=5) as response:
        installed = {m["name"] for m in json.load(response)["models"]}
    missing = {"qwen3:4b", "qwen3:0.6b", judge_name} - installed
    if missing:
        raise RuntimeError("Missing Ollama models: " + ", ".join(sorted(missing)) + ". Check ollama list and pull the missing tags.")


def run_mas(task, graph_factory=None):
    if graph_factory is None:
        from src.graphs import build_graph
        graph_factory = build_graph
    graph = graph_factory(InMemorySaver())
    evidence = EvaluationContext()
    config = {"configurable": {"thread_id": str(uuid4())}, "callbacks": [evidence], "recursion_limit": 40}
    messages = task["messages"]
    supplied = [messages[0]]
    command = {"messages": [HumanMessage(content=messages[0])]}
    if task["context"]["customer_id"]:
        command["verified_user_id"] = task["context"]["customer_id"]
    before = (DATA / "customers.json").read_bytes()
    children = []
    while True:
        for update in graph.stream(command, config, stream_mode="updates"):
            for node, output in update.items():
                if not isinstance(output, dict):
                    continue
                observed = {k: output[k] for k in ("plan", "intent", "order_topic", "order_id", "identity_state", "agent_result") if k in output}
                tools = [{"type": "tool", "name": e["tool"], "input": e["arguments"],
                          "output": e.get("result"), "error": e.get("error")} for e in output.get("tool_history", [])]
                children.append({"type": "agent", "name": node, "output": plain(observed),
                                 "children": tools, "start_time": time.time()})
        snapshot = graph.get_state(config)
        if not snapshot.next or len(supplied) == len(messages):
            break
        print(f"  Agent asks: {snapshot.values.get('clarification_question')}", flush=True)
        supplied.append(messages[len(supplied)])
        print(f"  Scripted human: {supplied[-1]}", flush=True)
        command = Command(resume=supplied[-1])
    state = snapshot.values
    result = state.get("agent_result") or {}
    gold = golden(task)
    owner = task["context"]["customer_id"] or task.get("lookup_customer_id")
    oid = task.get("resolved_order_id") or task["case"]["order_id"]
    fixture = json.loads(before)
    customer = next((c for c in fixture["customers"] if c["customer_id"] == owner), None)
    authorized = next((o for o in customer["orders"] if o["order_id"] == oid), None) if customer else None
    plan = next((d["plan"] for d in state.get("supervisor_history", []) if d.get("source") != "human_resume" and any(s.strip() for s in d.get("plan", []))), [])
    tool_calls = state.get("tool_history", [])
    case = LLMTestCase(name=task["id"],
        input=json.dumps({"customer_messages": supplied, "session_customer_id": task["context"]["customer_id"]}),
        actual_output=json.dumps(result), expected_output=gold.expected_output,
        context=[(DATA / "policy.md").read_text(), json.dumps({"customer_id":owner,"authorized_order":authorized})],
        tools_called=[ToolCall(name=e["tool"], input_parameters=e["arguments"], output=e.get("result")) for e in tool_calls],
        expected_tools=gold.expected_tools, metadata={"public_plan": plan})
    # In-memory execution context for the pinned DeepEval 4.3.4 agentic metrics.
    # No JSON trace export or second scoring command is required.
    case._trace_dict = {"type":"agent", "name":"retail_mas", "input":case.input, "output":result,
                        "children": sorted(children + list(evidence.calls.values()), key=lambda e:e["start_time"])}
    checks = {field: result.get(field) == task["expected"][field] for field in ("agent", "status", "reason_code")}
    checks["fixture_unchanged"] = before == (DATA / "customers.json").read_bytes()
    checks["all_replies_consumed"] = len(supplied) == len(messages)
    if oid and task["expected"]["agent"] == "order":
        checks["order_retained"] = state.get("order_id") == oid
    if owner:
        checks["customer"] = (state.get("verified_user_id") or state.get("identity_state", {}).get("customer_id")) == owner
    for index, call in enumerate(tool_calls):
        if call["tool"] in {"get_order", "check_order_eligibility"}:
            checks[f"tool_{index}_ownership"] = call["arguments"].get("customer_id") == owner and call["arguments"].get("order_id") == oid
    return case, checks


def evaluate_cases(cases, names, judge):
    """Use native progress/reports; exclude missing evidence instead of scoring it 1."""
    results, incomplete = [], False
    groups = {}
    for case in cases:
        omitted = {name: "no emitted public plan" for name in names
                   if name in PLAN_METRICS and not (case.metadata or {}).get("public_plan")}
        if "Argument Correctness" in names and not case.tools_called:
            omitted["Argument Correctness"] = "no observed tool arguments"
        applicable = tuple(name for name in names if name not in omitted)
        if omitted:
            print(f"{case.name}: NOT APPLICABLE: {omitted}", flush=True)
            incomplete = True
        if applicable:
            groups.setdefault(applicable, []).append(case)
    for applicable, selected in groups.items():
        result = evaluate(test_cases=selected, metrics=build_metrics(judge, applicable),
            async_config=AsyncConfig(run_async=False),
            cache_config=CacheConfig(use_cache=False, write_cache=False),
            error_config=ErrorConfig(ignore_errors=True),
            display_config=DisplayConfig(show_indicator=True, print_results=True,
                                         truncate_passing_cases=False, inspect_after_run=False))
        results.extend(result.test_results)
    return results, incomplete


def main(metric=None):
    parser = argparse.ArgumentParser(description="Run the retail MAS and evaluate it directly with DeepEval.")
    parser.add_argument("--model", default=eval_model, help="Ollama judge tag; default llama3.1:8b")
    parser.add_argument("--case", action="append", dest="ids", help="Run one or more v2 case IDs")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    selected = [t for t in TASKS if not args.ids or t["id"] in args.ids]
    if args.ids and set(args.ids) - {t["id"] for t in selected}:
        parser.error("Unknown or unsupported case ID; use --list")
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be positive")
        selected = selected[:args.limit]
    if args.list:
        for task in selected:
            print(task["id"], task["title"])
        for task in SKIPPED_TASKS:
            print(task["id"], "SKIPPED:", task["implementation_status"])
        return
    print(f"Judge: {args.model} | Tasks: {len(selected)} | Unsupported action/recovery tasks: {len(SKIPPED_TASKS)}", flush=True)
    try:
        preflight(args.model)
    except Exception as exc:
        print(f"Cannot start: {exc}\nStart Ollama (ollama serve) and check ollama list.", file=sys.stderr, flush=True)
        raise SystemExit(2)
    cases, checks_ok = [], True
    for index, task in enumerate(selected, 1):
        print(f"\n[{index}/{len(selected)}] Running MAS: {task['id']} — {task['title']}", flush=True)
        try:
            case, checks = run_mas(task)
            cases.append(case)
            checks_ok = checks_ok and all(checks.values())
            print(f"  Actual output: {case.actual_output}", flush=True)
            print(f"  Deterministic checks: {checks}", flush=True)
        except Exception as exc:
            checks_ok = False
            print(f"  MAS ERROR: {type(exc).__name__}: {exc}", flush=True)
    if not cases:
        raise SystemExit(2)
    names = [metric] if metric else NAMES
    print(f"\nDeepEval evaluating {len(cases)} responses on: {', '.join(names)}", flush=True)
    results, incomplete = evaluate_cases(cases, names, build_judge(args.model))
    print(f"\nEvaluated {len(cases)}/{len(selected)} MAS tasks. Unsupported scenarios: {len(SKIPPED_TASKS)}.", flush=True)
    if not checks_ok or incomplete or not results or not all(r.success for r in results):
        raise SystemExit(1)
