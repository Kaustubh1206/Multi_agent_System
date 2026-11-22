"""Run current v2 scenarios once; export observations without importing DeepEval."""
import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .common import ollama_preflight

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evaluation" / "retail-v2"
REPORTS = Path(__file__).parent / "reports"


def jsonable(value):
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class Recorder(BaseCallbackHandler):
    """Capture real callback parentage, including tools called directly by nodes."""
    def __init__(self):
        self.spans = {}

    def start(self, kind, inputs, run_id, parent_run_id, name, metadata=None):
        self.spans[str(run_id)] = {
            "id": str(run_id), "parent_id": str(parent_run_id) if parent_run_id else None,
            "type": kind, "name": name or kind, "input": jsonable(inputs),
            "start_time": time.time(), "status": "running",
            "metadata": {k: v for k, v in (metadata or {}).items() if k in {"langgraph_node", "prompt_version", "evidence_id"}},
        }

    def end(self, output, run_id, error=None):
        if span := self.spans.get(str(run_id)):
            span.update(output=jsonable(output), end_time=time.time(),
                        status="error" if error else "completed")
            if error:
                span["error"] = str(error)

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self.start("chain", inputs, run_id, parent_run_id, kwargs.get("name"), kwargs.get("metadata"))

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self.end(outputs, run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        if type(error).__name__ in {"GraphInterrupt", "NodeInterrupt"}:
            self.end(None, run_id)
            if str(run_id) in self.spans:
                self.spans[str(run_id)]["status"] = "interrupted"
        else:
            self.end(None, run_id, error)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self.start("llm", messages, run_id, parent_run_id, kwargs.get("name"), kwargs.get("metadata"))

    def on_llm_end(self, response, *, run_id, **kwargs):
        self.end(response, run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.end(None, run_id, error)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self.start("tool", kwargs.get("inputs") or input_str, run_id, parent_run_id,
                   (serialized or {}).get("name"), kwargs.get("metadata"))

    def on_tool_end(self, output, *, run_id, **kwargs):
        self.end(output, run_id)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.end(None, run_id, error)


def load_scenarios():
    return json.loads((DATA / "scenarios.json").read_text())


def fixture_hash():
    return hashlib.sha256((DATA / "customers.json").read_bytes()).hexdigest()


def run_case(scenario, graph_factory=None):
    # Expectations, expected tools, and fixture records never enter graph inputs.
    if graph_factory is None:
        from src.graphs import build_graph
        graph_factory = build_graph
    graph = graph_factory(InMemorySaver())
    recorder = Recorder()
    config = {"configurable": {"thread_id": str(uuid4())}, "callbacks": [recorder], "recursion_limit": 40}
    supplied = [scenario["messages"][0]]
    initial = {"messages": [HumanMessage(content=supplied[0])]}
    if scenario["context"]["customer_id"]:
        initial["verified_user_id"] = scenario["context"]["customer_id"]
    command = initial
    steps, state, error = [], {}, None
    before = fixture_hash()
    started = time.time()
    try:
        while True:
            for update in graph.stream(command, config, stream_mode="updates"):
                steps.append({"observed_at": time.time(), "update": jsonable(update)})
            snapshot = graph.get_state(config)
            state = snapshot.values
            if not snapshot.next or len(supplied) >= len(scenario["messages"]):
                break
            supplied.append(scenario["messages"][len(supplied)])
            command = Command(resume=supplied[-1])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state = graph.get_state(config).values
    return {
        "status": "error" if error else ("awaiting_input" if graph.get_state(config).next else "completed"),
        "error": error, "thread_id": config["configurable"]["thread_id"],
        "input": {"customer_messages": supplied, "session_customer_id": scenario["context"]["customer_id"]},
        "unused_scripted_messages": scenario["messages"][len(supplied):],
        "state": jsonable(state), "steps": steps, "spans": list(recorder.spans.values()),
        "fixture_unchanged": before == fixture_hash(), "start_time": started, "end_time": time.time(),
    }


def collect(ids=None, repeats=1, graph_factory=None, preflight=True):
    dataset = load_scenarios()
    expected_tools = json.loads(Path(__file__).with_name("expected_tools.json").read_text())
    selected = [s for s in dataset["scenarios"] if not ids or s["id"] in ids]
    if not selected:
        raise ValueError("No scenarios selected")
    if ids and set(ids) - {s["id"] for s in selected}:
        raise ValueError("Unknown scenario ID")
    report = {"schema_version": 1, "run_id": str(uuid4()), "mode": "controlled_test" if graph_factory else "live",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "dataset_version": dataset["dataset_version"], "policy_version": dataset["policy_version"],
              "evaluation_date": dataset["evaluation_date"], "fixture_sha256": fixture_hash(),
              "policy": (DATA / "policy.md").read_text(),
              "models": {"supervisor": "qwen3:4b", "specialist": "qwen3:0.6b"}, "cases": []}
    code = hashlib.sha256()
    for source in sorted((ROOT / "src").glob("*.py")):
        code.update(source.name.encode())
        code.update(source.read_bytes())
    report["code_sha256"] = code.hexdigest()
    report["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip())
    report["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    try:
        if preflight and any(s["implementation_status"] == "read_only_supported" for s in selected):
            ollama_preflight(report["models"].values())
    except Exception as exc:
        report["preflight_error"] = f"{type(exc).__name__}: {exc}"
    for scenario in selected:
        for repetition in range(repeats):
            owner = scenario["context"]["customer_id"] or scenario.get("lookup_customer_id")
            fixture = json.loads((DATA / "customers.json").read_text())
            customer = next((c for c in fixture["customers"] if c["customer_id"] == owner), None)
            oid = scenario.get("resolved_order_id") or scenario["case"]["order_id"]
            authorized = next((o for o in customer["orders"] if o["order_id"] == oid), None) if customer else None
            entry = {"scenario": scenario, "repetition": repetition + 1,
                     "reference_context": {"customer_id": owner, "authorized_order": authorized},
                     "expected_tools": expected_tools.get(scenario["id"])}
            if scenario["implementation_status"] != "read_only_supported":
                entry.update(status="skipped", reason=scenario["implementation_status"])
            elif report.get("preflight_error"):
                entry.update(status="blocked", reason=report["preflight_error"])
            else:
                entry.update(status="executed", execution=run_case(scenario, graph_factory))
            report["cases"].append(entry)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="ids")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for case in load_scenarios()["scenarios"]:
            print(case["id"], case["implementation_status"], case["title"])
        return
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    # Remote tracing is opt-in; local JSON capture does not require LangSmith.
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    report = collect(args.ids, args.repeats)
    path = args.output or REPORTS / (report["run_id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(path)
    if any(c["status"] == "blocked" or c.get("execution", {}).get("status") == "error" for c in report["cases"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
