"""Run original retail tasks with the upstream simulator, tools and native reward."""

import argparse
import json
import os
import sys
import traceback
import urllib.request
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Evaluations.tau_native.setup import (
    DEFAULT_DATA,
    PIN,
    ROOT,
    prepare_imports,
    setup_data,
)


@contextmanager
def judge_settings(model, base_url):
    """Configure the pinned native NL judge; never replace its prompt or algorithm.

    v2.0 has module-level NL model settings rather than a per-run argument.
    Runs are serial; restore both settings after evaluation, including errors.
    """
    from tau2.evaluator import evaluator_nl_assertions as nl

    previous = nl.DEFAULT_LLM_NL_ASSERTIONS, nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS
    if model != "upstream":
        nl.DEFAULT_LLM_NL_ASSERTIONS = model
        nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {
            "temperature": 0,
            "api_base": base_url,
            "response_format": {"type": "json_object"},
        }
    try:
        yield
    finally:
        nl.DEFAULT_LLM_NL_ASSERTIONS, nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS = previous


def validate_reward(task, simulation):
    """An incomplete/malformed mandatory judge result is an error, never a pass."""
    from tau2.data_model.tasks import RewardType

    reward = simulation.reward_info
    if reward is None:
        raise ValueError("Native evaluator produced no reward")
    criteria = task.evaluation_criteria
    if criteria and RewardType.NL_ASSERTION in criteria.reward_basis:
        expected = Counter(criteria.nl_assertions or [])
        actual = Counter(c.nl_assertion for c in reward.nl_assertions or [])
        # Early termination is already a native zero and needs no judge results.
        if reward.reward_basis is not None and actual != expected:
            raise ValueError("Native NL judge omitted, duplicated, or changed required assertions")


def preflight(base_url, models):
    with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=5) as response:
        installed = {m["name"] for m in json.load(response)["models"]}
    tags = {m.removeprefix("ollama_chat/") for m in models if m != "upstream"}
    missing = tags - installed
    if missing:
        raise RuntimeError("Missing Ollama models: " + ", ".join(sorted(missing)))


def run_task(config, task, *, seed, output, judge_model, base_url):
    """Build a fresh official language-bound environment/user, then score natively."""
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.runner import build_text_orchestrator, run_simulation

    orchestrator = build_text_orchestrator(config, task, seed=seed)
    try:
        with judge_settings(judge_model, base_url):
            simulation = run_simulation(orchestrator, evaluation_type=EvaluationType.ALL)
        output.write_text(simulation.model_dump_json(indent=2))
        validate_reward(task, simulation)
        return simulation
    finally:
        session = getattr(orchestrator, "agent_state", None) or getattr(
            orchestrator.agent, "last_session", None
        )
        if session is not None:
            output.with_suffix(".steps.json").write_text(json.dumps(session.events, indent=2))
        if not output.exists():
            messages = [m.model_dump(mode="json") for m in orchestrator.get_messages()]
            output.with_suffix(".incomplete.json").write_text(
                json.dumps(
                    {"task_id": task.id, "reward": None, "unscored": True, "messages": messages},
                    indent=2,
                )
            )


def main():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setup", action="store_true", help="Download a pinned sparse upstream data checkout"
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.getenv("TAU2_NATIVE_DATA_DIR", str(DEFAULT_DATA)))
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify package/data and show selected original tasks without model calls",
    )
    parser.add_argument("--task-id", action="append", dest="ids")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--supervisor-model", default="qwen3:4b")
    parser.add_argument("--specialist-model", default="qwen3:0.6b")
    parser.add_argument("--user-model", default="ollama_chat/llama3.1:8b")
    parser.add_argument(
        "--judge-model",
        default="ollama_chat/llama3.1:8b",
        help="Local native judge or 'upstream' for the pinned hosted judge",
    )
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument(
        "--output", type=Path, help="New output directory; existing directories are not overwritten"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show upstream debug logs as well as MAS step progress",
    )
    args = parser.parse_args()
    if min(args.trials, args.max_steps, args.num_ctx) < 1 or (
        args.limit is not None and args.limit < 1
    ):
        parser.error("trials, max-steps, context and limit must be positive")
    if args.all and (args.limit is not None or args.ids):
        parser.error("Use --all, --task-id, or --limit for selection")
    if not args.user_model.startswith("ollama_chat/"):
        parser.error("User model must use ollama_chat/<tag>")
    if args.judge_model != "upstream" and not args.judge_model.startswith("ollama_chat/"):
        parser.error("Judge must use ollama_chat/<tag> or 'upstream'")
    try:
        if args.setup:
            print(setup_data(args.data_dir))
            return
        prepare_imports(args.data_dir)
    except Exception as exc:
        parser.exit(
            2,
            f"Cannot load native τ-bench: {exc}\nRun uv sync --frozen --extra tau-native and python Evaluations/tau_native/run.py --setup.\n",
        )

    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.verbose else "ERROR")
    from tau2.data_model.semantic_communication import (
        CommunicateScoring,
        SemanticCommunicateSettings,
    )
    from tau2.data_model.simulation import TextRunConfig
    from tau2.multilingual.runtime import pin_language_artifacts
    from tau2.registry import registry
    from tau2.runner.helpers import select_run_tasks

    from src.tau_native.agent import create_native_agent
    from src.tau_native.prompts import VERSION

    if registry.get_agent_factory("retail_mas_native") is None:
        registry.register_agent_factory(create_native_agent, "retail_mas_native")
    base_url = args.ollama_url.rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    user_args = {"temperature": 0, "api_base": base_url, "num_ctx": args.num_ctx}
    config = TextRunConfig(
        domain="retail",
        language="en",
        agent="retail_mas_native",
        llm_agent="ollama_chat/" + args.supervisor_model,
        llm_args_agent={
            "specialist_model": args.specialist_model,
            "base_url": base_url,
            "num_ctx": args.num_ctx,
            "progress": True,
        },
        llm_user=args.user_model,
        llm_args_user=user_args,
        task_ids=args.ids,
        num_tasks=args.limit if args.limit is not None else (None if args.all or args.ids else 1),
        max_steps=args.max_steps,
        max_errors=10,
        seed=args.seed,
        num_trials=args.trials,
        enforce_communication_protocol=True,
        max_concurrency=1,
    )
    if args.judge_model != "upstream":
        config.communicate_scoring = CommunicateScoring(
            judge=SemanticCommunicateSettings(
                model=args.judge_model, model_args={"temperature": 0, "api_base": base_url}
            )
        )
    loaded = pin_language_artifacts(config)
    tasks = select_run_tasks(config, loaded)
    if not tasks:
        parser.error("No native tasks selected")
    metadata = {
        "upstream_commit": PIN,
        "agent_version": VERSION,
        "scope": "native retail tasks, tools, simulator and rewards",
        "leaderboard_comparable": False,
        "reason": "Custom MAS and local simulator/judge configuration; no released leaderboard claim",
        "judge_model": args.judge_model,
        "config": config.model_dump(mode="json"),
        "selected_task_ids": [t.id for t in tasks],
        "available_native_tasks": len(loaded.tasks),
    }
    from tau2.config import DEFAULT_LLM_NL_ASSERTIONS

    metadata["native_nl_judge_model"] = (
        DEFAULT_LLM_NL_ASSERTIONS if args.judge_model == "upstream" else args.judge_model
    )
    if args.dry_run:
        print(json.dumps(metadata, indent=2))
        return
    try:
        preflight(
            base_url,
            [args.supervisor_model, args.specialist_model, args.user_model, args.judge_model],
        )
    except Exception as exc:
        parser.exit(2, f"Cannot start models: {exc}. Start Ollama and check ollama list.\n")
    output = args.output or ROOT / "evaluation/tau-native/runs" / uuid4().hex
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_text(json.dumps(metadata, indent=2))
    records = []
    for trial in range(args.trials):
        for task in tasks:
            print(f"Native task {task.id}, trial {trial + 1}/{args.trials}", flush=True)
            try:
                simulation = run_task(
                    config,
                    task,
                    seed=args.seed + trial,
                    output=output / f"task-{task.id}-trial-{trial + 1}.json",
                    judge_model=args.judge_model,
                    base_url=base_url,
                )
                records.append(
                    {
                        "task_id": task.id,
                        "trial": trial + 1,
                        "status": "scored",
                        "reward": simulation.reward_info.reward,
                        "reward_breakdown": simulation.reward_info.model_dump(mode="json"),
                        "termination": simulation.termination_reason.value,
                    }
                )
                print(
                    f"  Termination: {simulation.termination_reason.value}; native reward: {simulation.reward_info.reward}",
                    flush=True,
                )
            except Exception as exc:
                failure = f"{type(exc).__name__}: {exc}"
                error_path = output / f"task-{task.id}-trial-{trial + 1}.error.txt"
                error_path.write_text(traceback.format_exc())
                print(f"  ERROR: {failure}\n  Traceback: {error_path}", flush=True)
                records.append(
                    {
                        "task_id": task.id,
                        "trial": trial + 1,
                        "status": "error",
                        "reward": None,
                        "error": failure,
                        "error_file": error_path.name,
                    }
                )
            (output / "results.json").write_text(json.dumps(records, indent=2))
    scored = [r for r in records if r["status"] == "scored"]
    summary = {
        "requested_trials": len(tasks) * args.trials,
        "scored": len(scored),
        "errors": len(records) - len(scored),
        "successes": sum(r["reward"] == 1 for r in scored),
        "native_mean_reward": sum(r["reward"] for r in scored) / len(scored) if scored else None,
        "complete": len(scored) == len(tasks) * args.trials,
        "termination_counts": dict(Counter(r.get("termination", "run_error") for r in records)),
        "success_rate_over_requested": sum(r["reward"] == 1 for r in scored)
        / (len(tasks) * args.trials),
        "leaderboard_comparable": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), "\nResults:", output)
    if summary["errors"] or summary["successes"] != summary["requested_trials"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
