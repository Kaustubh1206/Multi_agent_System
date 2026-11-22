"""Score an exported MAS run with seven native DeepEval metrics; never rerun the MAS."""
import argparse
from collections import Counter
import json
import hashlib
import os
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval.metrics import (
    ArgumentCorrectnessMetric, GEval, PlanAdherenceMetric, PlanQualityMetric,
    StepEfficiencyMetric, TaskCompletionMetric, ToolCorrectnessMetric,
)
from deepeval.models import OpenAIModel, OllamaModel
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.test_case import ToolCall

NAMES = ["Task Completion", "Step Efficiency", "Plan Quality", "Plan Adherence",
         "Argument Correctness", "Tool Correctness", "Answer Correctness"]
EXPECTED_TOOLS = json.loads(Path(__file__).with_name("expected_tools.json").read_text())


def build_metrics(model, task):
    common = {"model": model, "threshold": 0.8, "async_mode": False}
    return [
        TaskCompletionMetric(**common, task=task, eval_mode="llm"),
        StepEfficiencyMetric(**common, eval_mode="llm"),
        PlanQualityMetric(**common, eval_mode="llm"),
        PlanAdherenceMetric(**common, eval_mode="llm"),
        ArgumentCorrectnessMetric(**common, eval_mode="llm"),
        ToolCorrectnessMetric(model=model, threshold=0.9, async_mode=False,
                              eval_mode="llm", should_exact_match=True, should_consider_ordering=True),
        GEval(**common, name="Answer Correctness", evaluation_params=[
            SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT,
            SingleTurnParams.CONTEXT], evaluation_steps=[
                "Compare the actual result with the expected status, reason and response requirements.",
                "Use the supplied policy and authorized fixture reference to check facts, dates, amounts, identity and exclusions.",
                "Deny credit for invented facts, private disclosure or claims of completed actions without evidence.",
                "Correct policy denial or a required human clarification/review may be a successful outcome. Judge meaning, not wording.",
            ], _include_g_eval_suffix=False),
    ]


def trace_dict(execution):
    """Adapt observed events; no inferred planning or invented parent relationships."""
    children = []
    for span in execution["spans"]:
        if span["type"] not in {"tool", "llm"}:
            continue
        children.append({**span, "children": []})
    # Node updates record the actual public model plan and executed decisions.
    for step in execution["steps"]:
        for node, update in step["update"].items():
            if not isinstance(update, dict):
                continue
            observed = {key: update[key] for key in ["plan", "intent", "order_topic", "order_id", "identity_state", "agent_result", "supervisor_history"] if key in update}
            children.append({"type": "node_update", "name": node, "output": observed, "start_time": step["observed_at"]})
    return {"name": "retail_conversation", "type": "agent", "input": execution["input"],
            "output": execution["state"].get("agent_result"), "children": sorted(children, key=lambda s: s["start_time"])}


def as_test_case(entry, policy):
    scenario, execution = entry["scenario"], entry["execution"]
    history = execution["state"].get("tool_history", [])
    case = LLMTestCase(
        name=scenario["id"], input=json.dumps(execution["input"]),
        actual_output=json.dumps(execution["state"].get("agent_result")),
        expected_output=json.dumps(scenario["expected"]),
        context=[policy, json.dumps(entry.get("reference_context", {}))],
        tools_called=[ToolCall(name=e["tool"], input_parameters=e["arguments"],
                               output=e.get("result", {"error": e.get("error")})) for e in history],
        expected_tools=[ToolCall(name=name) for name in entry.get("expected_tools", EXPECTED_TOOLS[scenario["id"]])],
    )
    # DeepEval 4.3.4 trace-metric bridge for saved external traces. Kept here and
    # covered by native metric tests; changes to DeepEval require compatibility tests.
    case._trace_dict = trace_dict(execution)
    return case


def hard_checks(entry):
    scenario, execution = entry["scenario"], entry["execution"]
    state, expected = execution["state"], scenario["expected"]
    actual = state.get("agent_result") or {}
    checks = {field: actual.get(field) == expected[field] for field in ("agent", "status", "reason_code")}
    checks.update(trace_observed=bool(execution["spans"]), fixture_unchanged=execution["fixture_unchanged"],
                  no_execution_error=execution["error"] is None,
                  scripted_replies_consumed=not execution["unused_scripted_messages"])
    oid = scenario.get("resolved_order_id") or scenario["case"]["order_id"]
    owner = scenario["context"]["customer_id"] or scenario.get("lookup_customer_id")
    if oid and expected["agent"] == "order":
        checks["order_retained"] = state.get("order_id") == oid
    if owner:
        identified = state.get("verified_user_id") or state.get("identity_state", {}).get("customer_id")
        checks["correct_customer"] = identified == owner
    checks["no_mutation_tool"] = not any(e["tool"] in {"cancel_order", "request_return", "issue_refund"} for e in state.get("tool_history", []))
    for i, event in enumerate(state.get("tool_history", [])):
        if event["tool"] in {"get_order", "check_order_eligibility"}:
            args = event["arguments"]
            checks[f"tool_{i}_customer"] = owner is not None and args.get("customer_id") == owner
            checks[f"tool_{i}_order"] = args.get("order_id") == oid
    return checks


def score_entry(entry, policy, model):
    if entry["status"] != "executed":
        return {"scenario_id": entry["scenario"]["id"], "repetition": entry["repetition"],
                "status": entry["status"], "reason": entry.get("reason"), "metrics": []}
    case = as_test_case(entry, policy)
    execution, scenario = entry["execution"], entry["scenario"]
    plans = [d.get("plan") for d in execution["state"].get("supervisor_history", [])
             if d.get("source") != "human_resume" and any(isinstance(step, str) and step.strip() for step in d.get("plan", []))]
    task = "Complete the user's retail request under the supplied policy. This MAS is read-only. " + json.dumps(scenario["expected"])
    results = []
    for name, metric in zip(NAMES, build_metrics(model, task)):
        if name in {"Plan Quality", "Plan Adherence"} and not plans:
            results.append({"name": name, "status": "not_applicable", "score": None,
                            "reason": "No explicit public plan was emitted; no synthetic plan or automatic perfect score."})
            continue
        if name == "Argument Correctness" and not case.tools_called:
            results.append({"name": name, "status": "not_applicable", "score": None, "reason": "No tool calls observed."})
            continue
        try:
            metric.measure(case, _show_indicator=False)
            reason = metric.reason or ""
            if name in {"Plan Quality", "Plan Adherence"} and ("no plans to evaluate" in reason.lower() or "no plan to evaluate" in reason.lower()):
                results.append({"name": name, "status": "not_applicable", "score": None, "reason": reason})
                continue
            score = metric.score
            if score is None or not 0 <= score <= 1:
                raise ValueError("Metric returned no valid normalized score")
            results.append({"name": name, "status": "passed" if metric.is_successful() else "failed",
                            "score": score, "threshold": metric.threshold, "reason": reason})
        except Exception as exc:
            results.append({"name": name, "status": "error", "score": None,
                            "reason": f"{type(exc).__name__}: {exc}"})
    checks = hard_checks(entry)
    status = "passed"
    if not all(checks.values()) or any(m["status"] == "failed" for m in results):
        status = "failed"
    elif any(m["status"] == "error" for m in results):
        status = "error"
    elif any(m["status"] == "not_applicable" for m in results):
        status = "incomplete_metrics"
    return {"scenario_id": scenario["id"], "repetition": entry["repetition"], "status": status,
            "checks": checks, "metrics": results}


def summarize(cases):
    metrics = {}
    for name in NAMES:
        values = [m for c in cases for m in c["metrics"] if m["name"] == name]
        scores = [m["score"] for m in values if m["score"] is not None]
        metrics[name] = {"mean": sum(scores) / len(scores) if scores else None,
                         "scored": len(scores), "outcomes": dict(Counter(m["status"] for m in values))}
    return {"cases": dict(Counter(c["status"] for c in cases)), "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--judge-provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--judge-model", default="qwen3:8b")
    parser.add_argument("--judge-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    report = json.loads(args.report.read_text())
    if report.get("schema_version") != 1 or report.get("mode") != "live":
        parser.error("Expected a live collector report with schema_version 1")
    needs_judge = any(c["status"] == "executed" for c in report["cases"])
    model, preflight_error = None, None
    if needs_judge:
        try:
            if args.judge_provider == "ollama":
                from .common import ollama_preflight
                ollama_preflight([args.judge_model], args.judge_url)
                model = OllamaModel(model=args.judge_model, base_url=args.judge_url,
                                    temperature=0, generation_kwargs={"num_ctx":32768, "num_predict":2048})
            else:
                if not os.environ.get("OPENAI_API_KEY"):
                    raise RuntimeError("Set OPENAI_API_KEY securely for the explicitly selected OpenAI judge")
                model = OpenAIModel(model=args.judge_model, temperature=0)
        except Exception as exc:
            preflight_error = f"{type(exc).__name__}: {exc}"
    output = {"schema_version": 1, "source_run_id": report["run_id"],
              "judge": {"provider": args.judge_provider, "model": args.judge_model},
              "deepeval_version": "4.3.4", "git_commit": report.get("git_commit"),
              "dataset_version": report.get("dataset_version"), "policy_version": report.get("policy_version"),
              "code_sha256": report.get("code_sha256"), "source_dirty": report.get("source_dirty"),
              "source_report_sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(), "cases": []}
    for entry in report["cases"]:
        if preflight_error and entry["status"] == "executed":
            output["cases"].append({"scenario_id": entry["scenario"]["id"], "repetition":entry["repetition"],
                                    "status":"blocked", "reason":preflight_error, "metrics":[]})
        else:
            output["cases"].append(score_entry(entry, report["policy"], model))
    output["summary"] = summarize(output["cases"])
    path = args.output or args.report.with_name(args.report.stem + ".scores.json")
    if path.resolve() == args.report.resolve():
        parser.error("Score output must not overwrite the execution trace")
    path.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output["summary"], indent=2))
    print(path)
    if not any(c["metrics"] for c in output["cases"]) or any(c["status"] not in {"passed", "skipped"} for c in output["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
