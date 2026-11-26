"""Run the 98 ThinkingBox retail tasks, native checks, and two DeepEval metrics."""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Evaluations import model
from Evaluations.thinkingbox.dataset import (
    DATA,
    MANIFEST,
    RETAIL_DOMAIN,
    ROOT,
    audit,
    load_tasks,
    task_names,
    validate_retail_task,
)

METRICS = ["Task Completion", "Answer Correctness"]
PROTOCOL = "thinkingbox-retail-mas-v2"


def preflight(args):
    import httpx

    response = httpx.get(args.base_url.rstrip("/") + "/api/tags", timeout=10)
    response.raise_for_status()
    available = {item["name"] for item in response.json()["models"]}
    required = {args.supervisor_model, args.specialist_model, args.user_model, args.model}
    missing = {
        name for name in required if name not in available and name + ":latest" not in available
    }
    if missing:
        raise RuntimeError(
            "Missing Ollama models: "
            + ", ".join(sorted(missing))
            + ". Install each with: ollama pull <model>"
        )


async def execute(name, task, args):
    validate_retail_task(task)
    from thinkingbox.common.agent_session import AgentSession
    from thinkingbox.common.agent_user_loop import run_agent_user_loop
    from thinkingbox.common.config_types import (
        CustomSessionConfig,
        SessionProxyConfig,
        merge_init_config,
    )
    from thinkingbox.common.mcp_proxy_client import MCPProxyClient
    from thinkingbox.common.testrunner import TestScriptSubprocess

    from Evaluations.thinkingbox.mas import MASSession
    from Evaluations.thinkingbox.ollama_session import OllamaSession

    actor = MASSession(
        args.supervisor_model,
        args.specialist_model,
        base_url=args.base_url,
        num_ctx=args.num_ctx,
        timeout=args.timeout,
    )

    def factory(**kwargs):
        # Only public source instructions, conversation and tool schemas reach the actor.
        return AgentSession.from_config(agent_model=actor, **kwargs)

    config = SessionProxyConfig(
        endpoint_url="http://127.0.0.1:7111", timeout=args.timeout, max_retries_server_error=0
    )
    async with MCPProxyClient.session_context_from_config(
        config=config,
        server_config=merge_init_config(task.scenario.world_state, task.init),
        available_tools=[tool.name for tool in task.scenario.tools],
    ) as proxy:
        result = await run_agent_user_loop(
            task,
            factory,
            proxy,
            user_model=OllamaSession(args.user_model, args.base_url, args.num_ctx, args.timeout),
            store_test_context=True,
        )
        if result.is_system_error:
            return result, actor.steps
        judge_config = CustomSessionConfig(
            factory="Evaluations.thinkingbox.ollama_session.create_session",
            model=args.model,
            base_url=args.base_url,
            num_ctx=args.num_ctx,
            timeout=args.timeout,
        )
        test = TestScriptSubprocess(
            code=task.test_code,
            judge_config=judge_config,
            judge_type="legacy",
            fixtures_config=task.fixtures,
            test_uid=task.uid,
        )
        result.test_result = await test.evaluate(result.test_context)
        return result, actor.steps


def summarize(records, requested):
    native = [
        record["native"]
        for record in records
        if record.get("native") and not record["native"].get("is_system_error")
    ]
    successes = sum(bool(result["result"]) for result in native)
    metrics = {}
    for name in METRICS:
        scores = [
            record["metrics"][name]["score"]
            for record in records
            if record.get("metrics", {}).get(name, {}).get("status") == "scored"
        ]
        metrics[name] = {
            "scored": len(scores),
            "mean": sum(scores) / len(scores) if scores else None,
            "passed": sum(score >= 0.8 for score in scores),
        }
    return {
        "scope": "retail-only",
        "domain": RETAIL_DOMAIN,
        "requested": requested,
        "recorded": len(records),
        "complete": len(records) == requested
        and all(record["status"] in {"passed", "failed"} for record in records),
        "statuses": dict(Counter(record["status"] for record in records)),
        "native_scored": len(native),
        "native_successes": successes,
        "native_mean_reward": sum(result["reward"] for result in native) / len(native)
        if native
        else None,
        "native_success_rate_over_requested": successes / requested if requested else None,
        "metrics": metrics,
        "leaderboard_comparable": False,
    }


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def run(args, selected, metric_names):
    from Evaluations.thinkingbox.scoring import LocalJudge, build_case, score
    from Evaluations.thinkingbox.services import services

    output = args.output or ROOT / "evaluation/thinkingbox/runs" / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%S%fZ")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = {key: value for key, value in vars(args).items() if key != "output"}
    write_json(
        output / "manifest.json",
        {
            "protocol": PROTOCOL,
            "scope": "retail-only",
            "domain": RETAIL_DOMAIN,
            "source": MANIFEST,
            "config": config,
            "metrics": metric_names,
            "selected_tasks": selected,
            "leaderboard_comparable": False,
        },
    )
    print(f"Results: {output}", flush=True)
    records = []
    try:
        preflight(args)
        tasks = load_tasks(selected)
        os.environ["THINKINGBOX_DATA"] = str(DATA)
        # Native test subprocesses import this project's local judge factory.
        os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
        judge = LocalJudge(args.model, args.base_url, args.num_ctx)
        with services():
            for index, (name, task) in enumerate(zip(selected, tasks, strict=True), 1):
                print(f"[{index}/{len(tasks)}] {name}", flush=True)
                record = {"task": name, "status": "error", "native": None, "metrics": {}}
                try:
                    result, steps = asyncio.run(execute(name, task, args))
                    # Save native conversation/effects for reproducibility, no separate collection step.
                    write_json(output / f"{index:03d}.native.json", result.model_dump(mode="json"))
                    record["routing"] = steps
                    record["finish_reason"] = result.finish_reason
                    record["native"] = (
                        result.test_result.model_dump(mode="json") if result.test_result else None
                    )
                    if (
                        result.is_system_error
                        or not result.test_result
                        or result.test_result.is_system_error
                    ):
                        raise RuntimeError(
                            str(
                                result.metadata.get("error")
                                or record["native"]
                                or "Missing native checks"
                            )
                        )
                    if not result.test_context.response.strip():
                        raise RuntimeError(
                            "No final customer-visible answer; DeepEval was not scored"
                        )
                    record["metrics"] = score(build_case(name, task, result), judge, metric_names)
                    scored = all(
                        value["status"] == "scored" for value in record["metrics"].values()
                    )
                    passed = result.test_result.result and all(
                        value["passed"] for value in record["metrics"].values()
                    )
                    if result.finish_reason not in ("done", "end_turn_tool"):
                        record["status"] = "incomplete"
                    else:
                        record["status"] = ("passed" if passed else "failed") if scored else "error"
                except Exception as error:
                    record["error"] = f"{type(error).__name__}: {error}"
                records.append(record)
                write_json(output / "results.json", records)
                write_json(output / "summary.json", summarize(records, len(selected)))
                native = record["native"]
                print(
                    json.dumps(
                        {
                            "status": record["status"],
                            "native_passed": native["result"]
                            if native and not native.get("is_system_error")
                            else None,
                            "metrics": record["metrics"],
                            "error": record.get("error"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        recorded = {record["task"] for record in records}
        records.extend(
            {"task": name, "status": "blocked", "error": message, "native": None, "metrics": {}}
            for name in selected
            if name not in recorded
        )
        write_json(output / "results.json", records)
        write_json(output / "summary.json", summarize(records, len(selected)))
        print(f"Run blocked: {message}", file=sys.stderr)
        print(f"Details: {output / 'summary.json'}", file=sys.stderr)
        return 2
    summary = summarize(records, len(selected))
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 2 if any(record["status"] in ("error", "blocked") for record in records) else 0


def main(metric_name=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Hydrate and validate all 98 retail tasks; no models/services",
    )
    parser.add_argument("--list", action="store_true", help="Print canonical retail task IDs")
    parser.add_argument(
        "--case", action="append", help="Exact canonical filename:test_name; repeatable"
    )
    parser.add_argument(
        "--limit", type=int, help="Explicit smoke-test subset; default is all 98 retail tasks"
    )
    parser.add_argument(
        "--model", default=model.eval_model, help="DeepEval and native rubric judge"
    )
    parser.add_argument("--supervisor-model", default=os.getenv("SUPERVISOR_MODEL", "qwen3:4b"))
    parser.add_argument("--specialist-model", default=os.getenv("SPECIALIST_MODEL", "qwen3:0.6b"))
    parser.add_argument("--user-model", default="llama3.1:8b", help="Source user simulator model")
    parser.add_argument("--base-url", default=model.ollama_url())
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", type=Path, help="New output directory (must not already exist)")
    args = parser.parse_args()
    names = task_names()
    if args.audit:
        print(json.dumps(audit(), indent=2))
        return
    if args.list:
        print("\n".join(names))
        return
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.num_ctx < 8192:
        parser.error("--num-ctx must be at least 8192; 32768 or more recommended")
    selected = args.case or names
    if len(set(selected)) != len(selected) or set(selected) - set(names):
        parser.error("--case must contain unique canonical retail task IDs (see --list)")
    if args.limit:
        selected = selected[: args.limit]
    raise SystemExit(run(args, selected, [metric_name] if metric_name else METRICS))


if __name__ == "__main__":
    main()
