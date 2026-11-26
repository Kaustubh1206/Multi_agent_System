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

    from Evaluations.thinkingbox.dataset import (
        RETAIL_DOMAIN,
        audit,
        load_tasks,
        task_names,
        validate_retail_task,
    )
    from Evaluations.thinkingbox.mas import MASSession
    from Evaluations.thinkingbox.ollama_session import OllamaSession, encode
    from Evaluations.thinkingbox.run import METRICS, main, summarize
    from Evaluations.thinkingbox.scoring import build_case, score


@unittest.skipUnless(AVAILABLE, "Install the root thinkingbox extra for adapter tests")
class ThinkingBoxTests(unittest.TestCase):
    def test_selection_is_exactly_98_unique_canonical_retail_tasks(self):
        names = task_names()
        self.assertEqual(len(names), 98)
        self.assertEqual(len(set(names)), 98)
        self.assertTrue(
            all(name.startswith("sandbox_external_retail_group1.py:") for name in names)
        )

    def test_non_retail_case_is_rejected_before_preflight(self):
        with (
            patch("sys.argv", ["run.py", "--case", "external_booking_v1_group1.py:test_bmd_001"]),
            patch("Evaluations.thinkingbox.run.preflight") as preflight,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        preflight.assert_not_called()

    def test_task_loader_rejects_other_domains_before_fetching_source(self):
        with patch("Evaluations.thinkingbox.dataset.prepare_source") as prepare:
            with self.assertRaisesRegex(ValueError, "retail task IDs"):
                load_tasks(["external_booking_v1_group1.py:test_bmd_001"])
        prepare.assert_not_called()

    def test_task_validation_rejects_an_unexpected_backend(self):
        task = SimpleNamespace(
            scenario=SimpleNamespace(world_state={"external_booking_v1": {}}),
            init={"external_booking_v1": {}},
        )
        with self.assertRaisesRegex(ValueError, "retail MAS"):
            validate_retail_task(task)

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[1] / ".cache/thinkingbox-data").exists(),
        "Source hydration audit requires the pinned source cache",
    )
    def test_only_retail_source_cases_hydrate_with_expected_count(self):
        result = audit()
        self.assertEqual(result["tasks"], 98)
        self.assertEqual(result["domains"], {RETAIL_DOMAIN: 98})
        self.assertEqual(result["scope"], "retail-only")

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[1] / ".cache/thinkingbox-data").exists(),
        "Requires the pinned source cache",
    )
    def test_native_retail_check_subprocess_rejects_wrong_state(self):
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
        self.assertEqual(seen, {RETAIL_DOMAIN})

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
        self.assertNotIn("GOLD_WRITE", str(case._trace_dict))
        self.assertNotIn("golden_db_hash", str(case._trace_dict))
        self.assertNotIn("SOURCE_POLICY", str(case._trace_dict))
        self.assertNotIn("effects", case._trace_dict)

    def test_done_only_answer_has_explicit_zero_correctness_without_fabricated_judge_score(self):
        task, result = self.sample()
        result.test_context.response = "<DONE>"
        case = build_case("sample", task, result)
        self.assertEqual(case.actual_output, "")
        with patch("Evaluations.thinkingbox.answer_correctness.build_metric") as builder:
            scores = score(case, None, ["Answer Correctness"])
        builder.assert_not_called()
        self.assertEqual(scores["Answer Correctness"]["score"], 0)
        self.assertEqual(scores["Answer Correctness"]["method"], "empty_answer_check")
        summary = summarize([{"status": "failed", "metrics": scores}], 1)
        self.assertEqual(
            summary["metrics"]["Answer Correctness"]["methods"], {"empty_answer_check": 1}
        )

    def test_done_is_removed_from_visible_answer_but_native_result_is_preserved(self):
        task, result = self.sample()
        result.test_context.response = "Your order has shipped. <DONE>"
        case = build_case("sample", task, result)
        self.assertEqual(case.actual_output, "Your order has shipped.")
        self.assertEqual(result.test_context.response, "Your order has shipped. <DONE>")

    def test_native_failure_cannot_become_success_from_deepeval(self):
        record = {
            "status": "failed",
            "native": {"result": False, "reward": 0},
            "metrics": {name: {"status": "scored", "score": 1.0} for name in METRICS},
        }
        summary = summarize([record], 98)
        self.assertEqual(summary["native_successes"], 0)
        self.assertEqual(summary["metrics"][METRICS[0]]["scored"], 1)
        self.assertEqual(summary["requested"], 98)

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

    def test_default_run_selects_98_retail_tasks_and_missing_models_block_all(self):
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
            self.assertEqual(len(records), 98)
            self.assertTrue(all(record["status"] == "blocked" for record in records))
            self.assertEqual({record["task"] for record in records}, set(task_names()))
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(manifest["domain"], RETAIL_DOMAIN)
            self.assertEqual(manifest["scope"], "retail-only")
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

    def test_late_node_instructions_are_sent_with_source_policy_in_one_leading_system(self):
        wire = encode(
            [
                Text(role="system", content="SOURCE_POLICY"),
                Text(role="user", content="Where is ORD-1?"),
                Text(role="system", content="IDENTITY_NODE_INSTRUCTIONS"),
            ]
        )
        self.assertEqual([message["role"] for message in wire], ["system", "user"])
        self.assertIn("SOURCE_POLICY", wire[0]["content"])
        self.assertIn("IDENTITY_NODE_INSTRUCTIONS", wire[0]["content"])
        self.assertEqual(wire[1]["content"], "Where is ORD-1?")

    def test_router_gets_a_classification_request_with_observed_history(self):
        actor = MASSession("qwen3:4b", "qwen3:0.6b")
        history = [
            Text(role="system", content="SOURCE_POLICY"),
            Text(role="user", content="Where is ORD-1?"),
            ToolResponse(name="get_customer_profile", content='{"id":"CUS-1"}'),
        ]
        response = [Text(role="assistant", content='{"agent":"order","next_step":"Read order"}')]
        with (
            patch.object(
                actor.router, "get_completion", new=AsyncMock(return_value=response)
            ) as router,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            route = asyncio.run(actor.supervise({"messages": history}))
        self.assertEqual(route["route"]["agent"], "order")
        messages = router.call_args.kwargs["conversation"]
        self.assertEqual([message.role for message in messages], ["system", "user"])
        self.assertIn("classifier/router", messages[0].content)
        data = json.loads(messages[1].content)
        self.assertEqual(data["source_policy"], "SOURCE_POLICY")
        self.assertIn("CUS-1", str(data["observed_history"]))

    def test_premature_resolution_without_tools_is_repaired_before_reaching_customer(self):
        actor = MASSession("qwen3:4b", "qwen3:0.6b")
        actor.add_tools(
            [
                ToolDef(
                    name="get_customer_profile",
                    description="Read customer",
                    input_schema={"type": "object"},
                )
            ]
        )
        actor.add_messages([Text(role="user", content="Where is ORD-1?")])
        route = [
            Text(role="assistant", content='{"agent":"identity","next_step":"Resolve customer"}')
        ]
        outputs = [
            [
                Text(
                    role="assistant",
                    content="Delivery is delayed because it was placed 18 days ago. <DONE>",
                )
            ],
            [Text(role="assistant", content="What is the email address on your order?")],
        ]
        with (
            patch.object(actor.router, "get_completion", new=AsyncMock(return_value=route)),
            patch.object(
                OllamaSession, "get_completion", new=AsyncMock(side_effect=outputs)
            ) as worker,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = asyncio.run(actor.get_completion())
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(result[0].content, "What is the email address on your order?")
        self.assertFalse(
            any(
                "18 days" in message.content
                for message in actor.messages
                if isinstance(message, Text)
            )
        )

    def test_repeated_done_only_output_fails_after_one_repair(self):
        actor = MASSession("qwen3:4b", "qwen3:0.6b")
        actor.add_messages([Text(role="user", content="Where is ORD-1?")])
        route = [Text(role="assistant", content='{"agent":"order","next_step":"Read order"}')]
        with (
            patch.object(actor.router, "get_completion", new=AsyncMock(return_value=route)),
            patch.object(
                OllamaSession,
                "get_completion",
                new=AsyncMock(return_value=[Text(role="assistant", content="<DONE>")]),
            ) as worker,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(ValueError, "not a customer answer"):
                asyncio.run(actor.get_completion())
        self.assertEqual(worker.call_count, 2)

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
                name="get_customer_profile",
                description="Read customer profile",
                input_schema={
                    "type": "object",
                    "properties": {"email": {"type": "string"}},
                    "required": ["email"],
                },
            ),
            ToolDef(
                name="lookup",
                description="Lookup order",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
        ]
        proxy = SimpleNamespace(
            call_tool=AsyncMock(side_effect=['{"id":"CUS-1"}', '{"status":"shipped"}'])
        )
        agent = AgentSession.from_config(
            config=config,
            mcp_proxy=proxy,
            mcp_tools=tools,
            bot_instructions="SOURCE_POLICY",
            scenario_metadata={},
            agent_model=actor,
        )
        routes = [
            [Text(role="assistant", content=json.dumps({"agent": role, "next_step": step}))]
            for role, step in [
                ("identity", "Ask for email"),
                ("identity", "Read profile"),
                ("order", "Read order"),
                ("order", "Report status"),
            ]
        ]
        outputs = [
            [Text(role="assistant", content="What is the email address on your order?")],
            [
                ParallelToolCall(
                    tool_calls=[
                        ToolCall(
                            name="get_customer_profile", arguments={"email": "customer@example.com"}
                        )
                    ]
                )
            ],
            [ParallelToolCall(tool_calls=[ToolCall(name="lookup", arguments={"id": "ORD-1"})])],
            [Text(role="assistant", content="Your order is shipped. <DONE>")],
        ]

        async def drive():
            first = [
                message
                async for message in agent.decode_turn_iter(
                    Text(role="user", content="Where is ORD-1?")
                )
            ]
            self.assertIsNone(agent.should_end_conversation())
            second = [
                message
                async for message in agent.decode_turn_iter(
                    Text(role="user", content="customer@example.com")
                )
            ]
            return first + second

        with (
            patch.object(actor.router, "get_completion", new=AsyncMock(side_effect=routes)),
            patch.object(OllamaSession, "get_completion", new=AsyncMock(side_effect=outputs)),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                asyncio.run(drive())
        self.assertEqual(proxy.call_tool.await_count, 2)
        self.assertEqual(proxy.call_tool.await_args_list[0].args, ("get_customer_profile",))
        self.assertEqual(proxy.call_tool.await_args_list[1].kwargs, {"id": "ORD-1"})
        self.assertEqual(agent.should_end_conversation(), "done")
        self.assertEqual(len(agent.conversation.metadata["tool_calls"]), 2)
        self.assertEqual(
            [step["agent"] for step in actor.steps], ["identity", "identity", "order", "order"]
        )
        self.assertTrue(any(isinstance(message, ToolResponse) for message in actor.messages))


if __name__ == "__main__":
    unittest.main()
