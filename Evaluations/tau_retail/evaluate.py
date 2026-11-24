"""Run source-derived τ retail probes through the existing MAS and DeepEval."""
import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Evaluations.tau_retail.dataset import TASK_SETS, build_probes, source_policy


def run_probe(task, fixture, audit):
    from Evaluations.runner import run_mas
    case, checks = run_mas(task, fixture=fixture, policy_text=source_policy())
    if "expected_source_status" in task:
        result = case.metadata["structured_result"]
        observed = next((call.output.get("order", {}).get("status") for call in case.tools_called
                         if call.name == "get_order" and isinstance(call.output, dict)), None)
        checks["source_status"] = observed == task["expected_source_status"]
        checks["status_in_customer_reply"] = task["expected_source_status"] in result.get("customer_message", "")
    checks["status_tools_only"] = all(t.name in {"identify_customer", "get_order"} for t in case.tools_called)
    case.metadata.update(evaluation_protocol="tau-status-probes-v1",
                         source_task_ids=task["source_task_ids"], source_release=audit["release"],
                         source_commit=audit["commit"], source_task_set=audit["task_set"], probe_kind=task["kind"])
    return case, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-set", choices=TASK_SETS, default="reviewed50")
    parser.add_argument("--audit", action="store_true", help="Validate pinned source and show coverage without model calls")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case", action="append", dest="ids")
    parser.add_argument("--model", help="Ollama judge; defaults to EVAL_MODEL or llama3.1:8b")
    args = parser.parse_args()
    tasks, fixture, audit = build_probes(args.task_set)
    if args.audit:
        print(json.dumps(audit, indent=2))
        return
    if args.ids:
        unknown = set(args.ids) - {t["id"] for t in tasks}
        if unknown:
            parser.error("Unknown probe ID: " + ", ".join(sorted(unknown)))
        tasks = [t for t in tasks if t["id"] in args.ids]
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be positive")
        tasks = tasks[:args.limit]
    if args.list:
        for task in tasks:
            print(task["id"], task["title"], "| source task IDs:", ",".join(task["source_task_ids"]) or "synthetic control")
        return

    # Audit/list do not require DeepEval, Ollama, or a hosted API key.
    from Evaluations.model import build_judge, eval_model
    from Evaluations.runner import evaluate_cases, preflight
    model = args.model or eval_model
    print(f"TAU SOURCE-DERIVED PROBES | {args.task_set} | native tasks evaluated: 0 | selected probes: {len(tasks)}/{audit['total_probes']}", flush=True)
    print("Scores apply only to adapted status/identity/ownership questions, not the official benchmark.", flush=True)
    try:
        preflight(model)
    except Exception as exc:
        print(f"Cannot start: {exc}. Start Ollama and check ollama list.", file=sys.stderr)
        raise SystemExit(2)
    cases, checks_ok = [], True
    for index, task in enumerate(tasks, 1):
        print(f"\n[{index}/{len(tasks)}] {task['id']} — {task['title']}", flush=True)
        try:
            case, checks = run_probe(task, fixture, audit)
            cases.append(case)
            checks_ok = checks_ok and all(checks.values())
            print("Actual output:", case.actual_output, flush=True)
            print("Checks:", checks, flush=True)
        except Exception as exc:
            checks_ok = False
            print(f"MAS ERROR: {type(exc).__name__}: {exc}", flush=True)
    if not cases:
        raise SystemExit(2)
    results, incomplete = evaluate_cases(cases, ["Task Completion", "Answer Correctness"], build_judge(model))
    print(f"Evaluated {len(cases)}/{len(tasks)} selected probes. Native end-to-end tasks evaluated: 0.", flush=True)
    if not checks_ok or incomplete or not results or not all(r.success for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
