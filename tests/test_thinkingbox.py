"""Adapter contracts with controlled models; these are not benchmark scores."""

import asyncio
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

AVAILABLE = importlib.util.find_spec("thinkingbox") is not None
if AVAILABLE:
    import httpx
    from thinkingbox.common.agent_session import AgentSession
    from thinkingbox.common.chat_types import (
        DecodeResult,
        ParallelToolCall,
        TestContext,
        TestResult,
        Text,
        ToolCall,
        ToolCallResponse,
        ToolDef,
        ToolResponse,
    )
    from thinkingbox.common.config_types import AgentConfig, CustomSessionConfig
    from thinkingbox.common.testrunner import TestScriptSubprocess

    from Evaluations.thinkingbox.dataset import audit, load_tasks, task_names
    from Evaluations.thinkingbox.mas import MASSession
    from Evaluations.thinkingbox.ollama_session import OllamaSession, encode
    from Evaluations.thinkingbox.run import METRICS, main, summarize
    from Evaluations.thinkingbox.scoring import build_case, score


@unittest.skipUnless(AVAILABLE, "Install the root thinkingbox extra for adapter tests")
class ThinkingBoxTests(unittest.TestCase):
    def test_canonical_release_is_507_unique_tasks(self):
        names = task_names()
        self.assertEqual(len(names), 507)
        self.assertEqual(len(set(names)), 507)
        self.assertTrue(all(":" in name for name in names))

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[1] / ".cache/thinkingbox-data").exists(),
        "Source hydration audit requires the pinned source cache",
    )
    def test_all_source_cases_hydrate_with_expected_domain_counts(self):
        result = audit()
        self.assertEqual(result["tasks"], 507)
        self.assertEqual(sum(result["domains"].values()), 507)

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[1] / ".cache/thinkingbox-data").exists(),
        "Requires the pinned source cache",
    )
    def test_native_check_subprocess_rejects_wrong_state_in_every_domain(self):
        seen = set()
        for task in load_tasks():
            domain = next(iter(task.scenario.world_state))
            if domain in seen:
                continue
            seen.add(domain)
            with self.subTest(domain=domain):
                context = TestContext(
                    response="I completed it.",
                    effects={
                        domain: {
                            "result_db_hash": "wrong",
                            "golden_db_hash": "expected",
                            "diff": [],
                        }
                    },
                )
                test = TestScriptSubprocess(
                    code=task.test_code,
                    judge_config=CustomSessionConfig(
                        factory="Evaluations.thinkingbox.ollama_session.create_session",
                        model="not-called",
                    ),
                    fixtures_config=task.fixtures,
                    test_uid=task.uid,
                )
                result = asyncio.run(test.evaluate(context))
                self.assertFalse(result.result)
                self.assertFalse(result.is_system_error, result.tb)
                self.assertIn("hash", result.tb.lower())
        self.assertEqual(len(seen), 5)

    def sample(self):
        messages = [
            Text(role="system", content="SOURCE_POLICY"),
            Text(role="user", content="Where is my order?"),
            Text(role="assistant", content="Please share your email."),
            Text(role="user", content="customer@example.com"),
            Text(role="assistant", content="Your order has shipped."),
        ]
        context = TestContext(
            response="Your order has shipped.",
            messages=messages,
            effects={"domain": {"result_db_hash": "actual", "golden_db_hash": "gold"}},
            tool_calls=[
                ToolCallResponse(
                    tool_call=ToolCall(
                        name="lookup", arguments={"email": "customer@example.com"}, id="call1"
                    ),
                    tool_response=ToolResponse(
                        name="lookup", content='{"status":"shipped"}', id="call1"
                    ),
                )
            ],
        )
        result = DecodeResult(
            uid="sample",
            messages=messages,
            test_context=context,
            test_result=TestResult(result=False, reward=0),
        )
        task = SimpleNamespace(
            query="Where is my order?",
            user_context="PRIVATE_SIMULATOR_IDENTITY",
            init={
                "domain": {
                    "golden_test_case": {"tool_interactions": ["GOLD_WRITE"]},
                    "data_patch": {"secret": "DB_PATCH_SECRET"},
                }
            },
        )
        return task, result

    def test_final_answer_is_visible_text_and_references_are_grader_only(self):
        task, result = self.sample()
        case = build_case("sample", task, result)
        self.assertEqual(case.actual_output, "Your order has shipped.")
        self.assertIn("PRIVATE_SIMULATOR_IDENTITY", case.expected_output)
        self.assertIn("GOLD_WRITE", case.expected_output)
        self.assertNotIn("DB_PATCH_SECRET", case.expected_output)
        self.assertIn("Please share your email", str(case.context))
        self.assertIn("SOURCE_POLICY", str(case.context))
        self.assertIn("shipped", case._trace_dict["operations"][0]["result"])

    def test_native_failure_cannot_become_success_from_deepeval(self):
        record = {
            "status": "failed",
            "native": {"result": False, "reward": 0},
            "metrics": {name: {"status": "scored", "score": 1.0} for name in METRICS},
        }
        summary = summarize([record], 507)
        self.assertEqual(summary["native_successes"], 0)
        self.assertEqual(summary["metrics"][METRICS[0]]["scored"], 1)
        self.assertEqual(summary["requested"], 507)

    def test_missing_judge_scores_remain_errors_and_null(self):
        task, result = self.sample()
        with (
            patch(
                "Evaluations.thinkingbox.task_completion.build_metric",
                side_effect=RuntimeError("offline"),
            ),
            patch(
                "Evaluations.thinkingbox.answer_correctness.build_metric",
                side_effect=RuntimeError("offline"),
            ),
        ):
            scored = score(build_case("sample", task, result), None, METRICS)
        self.assertTrue(
            all(
                metric["status"] == "error" and metric["score"] is None
                for metric in scored.values()
            )
        )
        summary = summarize([{"status": "error", "metrics": scored}], 1)
        self.assertIsNone(summary["metrics"][METRICS[0]]["mean"])

    def test_real_deepeval_metrics_accept_native_evidence(self):
        from test_evaluations import ControlledJudge

        task, result = self.sample()
        scores = score(build_case("sample", task, result), ControlledJudge(), METRICS)
        self.assertTrue(all(metric["status"] == "scored" for metric in scores.values()), scores)

    def test_default_run_selects_all_tasks_and_missing_models_block_all(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "run"
            with (
                patch("sys.argv", ["run.py", "--output", str(target)]),
                patch(
                    "Evaluations.thinkingbox.run.preflight",
                    side_effect=RuntimeError("Ollama offline"),
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as error:
                    main()
            self.assertEqual(error.exception.code, 2)
            records = json.loads((target / "results.json").read_text())
            self.assertEqual(len(records), 507)
            self.assertTrue(all(record["status"] == "blocked" for record in records))
            self.assertEqual(
                json.loads((target / "summary.json").read_text())["metrics"][METRICS[0]]["scored"],
                0,
            )

    def test_tool_wire_format_preserves_result_ids_and_excludes_thinking(self):
        messages = [
            Text(role="assistant", content="PRIVATE_THINKING", metadata={"tag": "think"}),
            ParallelToolCall(tool_calls=[ToolCall(name="lookup", arguments={"id": "abc"}, id="a")]),
            ToolResponse(name="lookup", content='{"status":"found"}', id="a"),
        ]
        wire = encode(messages)
        self.assertNotIn("PRIVATE_THINKING", str(wire))
        self.assertEqual(wire[0]["tool_calls"][0]["id"], "a")
        self.assertEqual(wire[1]["tool_call_id"], "a")

    def test_ollama_schema_context_and_response_are_preserved(self):
        request = httpx.Request("POST", "http://local/api/chat")
        response = httpx.Response(
            200,
            request=request,
            json={
                "done_reason": "stop",
                "message": {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "lookup", "arguments": {"id": "abc"}}}],
                },
                "prompt_eval_count": 20,
                "eval_count": 5,
            },
        )
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as post:
            session = OllamaSession("qwen3:4b", "http://local", 32768)
            output = asyncio.run(
                session.get_completion(conversation=[Text(role="user", content="Lookup abc")])
            )
        self.assertEqual(output[0].tool_calls[0].arguments, {"id": "abc"})
        self.assertEqual(post.call_args.kwargs["json"]["options"]["num_ctx"], 32768)
        self.assertFalse(post.call_args.kwargs["json"]["think"])
        self.assertEqual(session.messages, [])

    def test_schema_repair_before_execution_does_not_create_missing_ids(self):
        actor = MASSession("qwen3:4b", "qwen3:0.6b")
        actor.add_tools(
            [
                ToolDef(
                    name="write",
                    description="Write an authorized record",
                    input_schema={
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                )
            ]
        )
        invalid = [ParallelToolCall(tool_calls=[ToolCall(name="write", arguments={})])]
        correction = [Text(role="assistant", content="Which record should I update?")]
        with (
            patch.object(
                actor.router,
                "get_completion",
                new=AsyncMock(
                    return_value=[
                        Text(
                            role="assistant",
                            content=json.dumps({"agent": "order", "next_step": "Update record"}),
                        )
                    ]
                ),
            ),
            patch.object(
                OllamaSession, "get_completion", new=AsyncMock(side_effect=[invalid, correction])
            ) as worker,
        ):
            actor.add_messages([Text(role="user", content="Update my record")])
            with contextlib.redirect_stdout(io.StringIO()):
                output = asyncio.run(actor.get_completion())
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(output, correction)
        self.assertNotIn("arguments", str(output))
        self.assertEqual(len(actor.steps), 1)

    def test_native_agent_executes_tools_and_stops_on_done_with_same_history(self):
        actor = MASSession("qwen3:4b", "qwen3:0.6b")
        config = AgentConfig(
            system_instructions="Use tools and answer with <DONE>.", builtin_tools=[]
        )
        tools = [
            ToolDef(
                name="lookup",
                description="Lookup order",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            )
        ]
        proxy = SimpleNamespace(call_tool=AsyncMock(return_value='{"status":"shipped"}'))
        agent = AgentSession.from_config(
            config=config,
            mcp_proxy=proxy,
            mcp_tools=tools,
            bot_instructions="SOURCE_POLICY",
            scenario_metadata={},
            agent_model=actor,
        )
        route = [Text(role="assistant", content='{"agent":"order","next_step":"Read and answer"}')]
        outputs = [
            [ParallelToolCall(tool_calls=[ToolCall(name="lookup", arguments={"id": "ORD-1"})])],
            [Text(role="assistant", content="Your order is shipped. <DONE>")],
        ]

        async def drive():
            return [
                message
                async for message in agent.decode_turn_iter(
                    Text(role="user", content="Where is ORD-1?")
                )
            ]

        with (
            patch.object(actor.router, "get_completion", new=AsyncMock(return_value=route)),
            patch.object(OllamaSession, "get_completion", new=AsyncMock(side_effect=outputs)),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                asyncio.run(drive())
        proxy.call_tool.assert_awaited_once_with("lookup", id="ORD-1")
        self.assertEqual(agent.should_end_conversation(), "done")
        self.assertEqual(len(agent.conversation.metadata["tool_calls"]), 1)
        self.assertTrue(any(isinstance(message, ToolResponse) for message in actor.messages))


if __name__ == "__main__":
    unittest.main()
