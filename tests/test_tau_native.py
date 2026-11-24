"""Native tools/orchestrator/rewards, controlled LLM replies; not benchmark scores.

Install --extra tau-native and run the native --setup first, or set
TAU2_NATIVE_DATA_DIR to a clean pinned source checkout's data directory.
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Evaluations.tau_native.setup import DEFAULT_DATA, prepare_imports

DATA = Path(os.getenv("TAU2_NATIVE_DATA_DIR", str(DEFAULT_DATA)))
READY = importlib.util.find_spec("tau2") is not None and DATA.exists()
if READY:
    prepare_imports(DATA)
    from langchain_core.messages import AIMessage
    from tau2.data_model.message import AssistantMessage, ToolMessage, UserMessage
    from tau2.data_model.simulation import RewardInfo, TextRunConfig
    from tau2.data_model.tasks import RewardType
    from tau2.domains.retail.environment import get_environment, get_tasks
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.registry import registry
    from tau2.runner import run_simulation
    from tau2.user.user_simulator import UserSimulator

    from Evaluations.tau_native.run import judge_settings, run_task, validate_reward
    from src.tau_native.agent import WRITE_TOOLS, NativeRetailAgent, create_native_agent
    from src.tau_native.objects import IdentityFields, Route, Session


@unittest.skipUnless(
    READY, "Native integration requires the tau-native extra and pinned data checkout"
)
class NativeRetailTests(unittest.TestCase):
    def setUp(self):
        self.env = get_environment()
        self.agent = self.make_agent(self.env.get_tools(), self.env.get_policy())
        self.task = next(t for t in get_tasks() if t.id == "69")
        self.oid = "#W2417020"
        self.uid = self.env.tools.db.orders[self.oid].user_id
        self.arguments = {"order_id": self.oid, "reason": "no longer needed"}

    def make_agent(self, tools, policy):
        agent = NativeRetailAgent(tools, policy)
        agent.router = Mock()
        agent.router.invoke.return_value = Route(
            agent="order", plan=["Read the order", "Confirm any change"]
        )
        agent.specialists = {role: Mock() for role in ("identity", "order", "policy")}
        agent.identity_extractor = Mock()
        agent.identity_extractor.invoke.return_value = IdentityFields()
        return agent

    def proposal(self, name, arguments):
        return AIMessage(
            content="", tool_calls=[{"id": "proposed", "name": name, "args": arguments}]
        )

    def test_identity_asks_for_identity_not_order_details_or_transactions(self):
        state = self.agent.get_init_state()
        response, state = self.agent.generate_next_message(
            UserMessage.text("I received a laptop and found a better deal. Can I return it?"), state
        )
        self.assertIn("first name", response.content)
        self.assertIn("last name", response.content)
        self.assertIn("zip code", response.content)
        self.assertNotIn("order ID", response.content)
        self.assertNotIn("refund", response.content)
        self.assertFalse(response.tool_calls)
        self.agent.specialists["identity"].invoke.assert_not_called()
        prompt = self.agent.identity_extractor.invoke.call_args.args[0][0].content
        self.assertNotIn("Source policy:", prompt)
        self.assertNotIn("Before any database change", prompt)
        self.assertIsNone(state.user_id)

    def test_identity_looks_up_name_zip_after_asking_the_right_question(self):
        state = self.agent.get_init_state()
        _, state = self.agent.generate_next_message(
            UserMessage.text("I'd like to return a laptop."), state
        )
        self.agent.identity_extractor.invoke.return_value = IdentityFields(
            first_name="Emma", last_name="Smith", zip="10192"
        )
        request, state = self.agent.generate_next_message(
            UserMessage.text("I'm Emma Smith. My zip code is 10192."), state
        )
        self.assertEqual(request.tool_calls[0].name, "find_user_id_by_name_zip")
        self.assertIsNone(state.user_id)
        profile_request, state = self.agent.generate_next_message(
            self.env.get_response(request.tool_calls[0]), state
        )
        self.assertEqual(state.user_id, self.uid)
        self.assertEqual(profile_request.tool_calls[0].name, "get_user_details")

    def test_identity_rejects_hallucinated_fields_and_user_id_name_inference(self):
        for text in ("Please return my laptop.", "My user ID is emma_smith_10192."):
            with self.subTest(text=text):
                state = self.agent.get_init_state()
                self.agent.identity_extractor.invoke.return_value = IdentityFields(
                    first_name="Emma",
                    last_name="Smith",
                    zip="10192",
                    email="emma.smith3991@example.com",
                )
                response, state = self.agent.generate_next_message(UserMessage.text(text), state)
                self.assertFalse(response.tool_calls)
                self.assertIsNone(state.user_id)
                self.assertEqual(state.identity_fields, IdentityFields())

    def test_identity_retains_names_for_a_corrected_bare_zip_and_does_not_repeat_failed_lookup(
        self,
    ):
        state = self.agent.get_init_state()
        self.agent.identity_extractor.invoke.return_value = IdentityFields(
            first_name="Emma", last_name="Smith", zip="00000"
        )
        request, state = self.agent.generate_next_message(
            UserMessage.text("My name is Emma Smith and my zip code is 00000."), state
        )
        failed = self.env.get_response(request.tool_calls[0])
        self.assertTrue(failed.error)
        question, state = self.agent.generate_next_message(failed, state)
        self.assertFalse(question.tool_calls)
        self.assertIn("couldn't find a match", question.content)
        corrected, state = self.agent.generate_next_message(UserMessage.text("10192"), state)
        self.assertEqual(
            corrected.tool_calls[0].arguments,
            {"first_name": "Emma", "last_name": "Smith", "zip": "10192"},
        )
        self.assertEqual(self.agent.identity_extractor.invoke.call_count, 1)

    def test_identity_email_is_a_native_lookup_and_does_not_require_the_extractor(self):
        state = self.agent.get_init_state()
        request, state = self.agent.generate_next_message(
            UserMessage.text("emma.smith3991@example.com"), state
        )
        self.assertEqual(request.tool_calls[0].name, "find_user_id_by_email")
        self.assertIsNone(state.user_id)
        self.agent.identity_extractor.invoke.assert_not_called()
        _, state = self.agent.generate_next_message(
            self.env.get_response(request.tool_calls[0]), state
        )
        self.assertEqual(state.user_id, self.uid)

    def test_identity_accumulates_name_then_zip(self):
        state = self.agent.get_init_state()
        self.agent.identity_extractor.invoke.return_value = IdentityFields(
            first_name="Emma", last_name="Smith"
        )
        question, state = self.agent.generate_next_message(
            UserMessage.text("My name is Emma Smith."), state
        )
        self.assertIn("zip code", question.content)
        self.assertNotIn("first name", question.content)
        request, state = self.agent.generate_next_message(UserMessage.text("10192."), state)
        self.assertEqual(request.tool_calls[0].arguments["zip"], "10192")

    def test_identity_extractor_can_interpret_a_last_name_reply_using_the_actual_question(self):
        state = self.agent.get_init_state()
        self.agent.identity_extractor.invoke.side_effect = [
            IdentityFields(first_name="Emma", zip="10192"),
            IdentityFields(last_name="Smith"),
        ]
        question, state = self.agent.generate_next_message(
            UserMessage.text("My first name is Emma, and my zip code is 10192."), state
        )
        self.assertIn("last name", question.content)
        request, state = self.agent.generate_next_message(UserMessage.text("Smith"), state)
        self.assertEqual(
            request.tool_calls[0].arguments,
            {"first_name": "Emma", "last_name": "Smith", "zip": "10192"},
        )
        prompt = self.agent.identity_extractor.invoke.call_args.args[0][0].content
        self.assertIn(question.content, prompt)
        self.assertIn('"first_name": "Emma"', prompt)
        self.assertIn('"zip": "10192"', prompt)

    def test_failed_email_is_not_retried_with_only_different_casing_and_can_be_corrected(self):
        state = self.agent.get_init_state()
        request, state = self.agent.generate_next_message(
            UserMessage.text("nobody@tau-probes.invalid"), state
        )
        failed = self.env.get_response(request.tool_calls[0])
        self.assertTrue(failed.error)
        _, state = self.agent.generate_next_message(failed, state)
        response, state = self.agent.generate_next_message(
            UserMessage.text("NOBODY@TAU-PROBES.INVALID"), state
        )
        self.assertFalse(response.tool_calls)
        self.assertIn("couldn't find a match", response.content)
        corrected, state = self.agent.generate_next_message(
            UserMessage.text("emma.smith3991@example.com"), state
        )
        self.assertEqual(corrected.tool_calls[0].arguments, {"email": "emma.smith3991@example.com"})
        self.assertIsNone(state.user_id)

    def authenticated(self):
        profile = self.env.tools.get_user_details(self.uid).model_dump(mode="json")
        order = self.env.tools.get_order_details(self.oid).model_dump(mode="json")
        return Session(
            user_id=self.uid, profile=profile, orders={self.oid: order}, active_agent="order"
        )

    def test_no_write_before_confirmation_then_exactly_one_after_recheck(self):
        state = self.authenticated()
        response = self.agent.guarded_call(state, "order", "cancel_pending_order", self.arguments)
        self.assertFalse(response.tool_calls)
        self.assertIn(self.oid, response.content)
        self.assertIn("confirm", response.content)
        self.assertEqual(self.env.tools.db.orders[self.oid].status, "pending")
        state.history.append(response)
        recheck, state = self.agent.generate_next_message(UserMessage.text("Yes, please."), state)
        self.assertEqual(recheck.tool_calls[0].name, "get_order_details")
        self.assertEqual(self.env.tools.db.orders[self.oid].status, "pending")
        write, state = self.agent.generate_next_message(
            self.env.get_response(recheck.tool_calls[0]), state
        )
        self.assertEqual(write.tool_calls[0].name, "cancel_pending_order")
        self.assertEqual(write.tool_calls[0].arguments, self.arguments)
        result = self.env.get_response(write.tool_calls[0])
        self.assertFalse(result.error)
        self.assertEqual(self.env.tools.db.orders[self.oid].status, "cancelled")
        denied = self.agent.guarded_call(state, "order", "cancel_pending_order", self.arguments)
        self.assertFalse(denied.tool_calls)
        self.assertIn("already attempted", denied.content)

    def test_refusal_and_changed_arguments_cannot_confirm_an_old_proposal(self):
        for reply in ("No.", "Yes, but use a different card.", "Tell me the amount first."):
            with self.subTest(reply=reply):
                state = self.authenticated()
                shown = self.agent.guarded_call(
                    state, "order", "cancel_pending_order", self.arguments
                )
                state.history.append(shown)
                self.agent.specialists["order"].invoke.return_value = AIMessage(
                    content="Please clarify the requested change."
                )
                response, state = self.agent.generate_next_message(UserMessage.text(reply), state)
                self.assertIsNone(state.pending)
                self.assertFalse(response.tool_calls)
                self.assertEqual(self.env.tools.db.orders[self.oid].status, "pending")

    def test_order_changed_before_confirmation_requires_a_new_proposal(self):
        state = self.authenticated()
        shown = self.agent.guarded_call(state, "order", "cancel_pending_order", self.arguments)
        state.history.append(shown)
        self.env.tools.db.orders[self.oid].status = "processed"
        recheck, state = self.agent.generate_next_message(UserMessage.text("Yes"), state)
        response, state = self.agent.generate_next_message(
            self.env.get_response(recheck.tool_calls[0]), state
        )
        self.assertFalse(response.tool_calls)
        self.assertIn("record changed", response.content)
        self.assertIsNone(state.pending)
        self.assertEqual(self.env.tools.db.orders[self.oid].status, "processed")

    def test_identity_ownership_and_tool_result_correlation_are_enforced(self):
        unauthenticated = Session(history=[UserMessage.text("I am customer emma_smith_8564")])
        response = self.agent.guarded_call(
            unauthenticated,
            "identity",
            "find_user_id_by_email",
            {"email": "emma.smith3991@example.com"},
        )
        self.assertFalse(response.tool_calls)
        self.assertIsNone(unauthenticated.user_id)
        state = self.authenticated()
        other = next(oid for oid, o in self.env.tools.db.orders.items() if o.user_id != self.uid)
        denied = self.agent.guarded_call(state, "order", "get_order_details", {"order_id": other})
        self.assertEqual(denied.content, "This order is unavailable for the identified customer.")
        self.assertFalse(denied.tool_calls)
        with self.assertRaisesRegex(ValueError, "outstanding"):
            self.agent.generate_next_message(
                ToolMessage(id="invented", role="tool", content="{}"), state
            )

    def test_all_native_write_types_are_proposed_without_mutating_the_database(self):
        examples = {}
        for task in get_tasks():
            for action in task.evaluation_criteria.actions or []:
                if action.name in WRITE_TOOLS and action.name not in examples:
                    examples[action.name] = action.arguments
        self.assertEqual(set(examples), WRITE_TOOLS)
        for name, arguments in examples.items():
            with self.subTest(tool=name):
                oid = arguments.get("order_id")
                uid = self.env.tools.db.orders[oid].user_id if oid else arguments["user_id"]
                profile = self.env.tools.get_user_details(uid).model_dump(mode="json")
                state = Session(
                    user_id=uid,
                    profile=profile,
                    active_agent="order",
                    orders={oid: self.env.tools.get_order_details(oid).model_dump(mode="json")}
                    if oid
                    else {},
                )
                before = self.env.get_db_hash()
                response = self.agent.guarded_call(state, "order", name, arguments)
                self.assertFalse(response.tool_calls)
                self.assertEqual(state.pending["arguments"], arguments)
                self.assertEqual(self.env.get_db_hash(), before)

    def test_all_write_types_use_the_same_confirmation_and_native_execution_path(self):
        examples = {}
        for task in get_tasks():
            for action in task.evaluation_criteria.actions or []:
                if action.name in WRITE_TOOLS and action.name not in examples:
                    examples[action.name] = action.arguments
        for name, arguments in examples.items():
            with self.subTest(tool=name):
                env = get_environment()
                agent = self.make_agent(env.get_tools(), env.get_policy())
                oid = arguments.get("order_id")
                uid = env.tools.db.orders[oid].user_id if oid else arguments["user_id"]
                state = Session(
                    user_id=uid,
                    profile=env.tools.get_user_details(uid).model_dump(mode="json"),
                    active_agent="order",
                    orders={oid: env.tools.get_order_details(oid).model_dump(mode="json")}
                    if oid
                    else {},
                )
                question = agent.guarded_call(state, "order", name, arguments)
                state.history.append(question)
                recheck, state = agent.generate_next_message(UserMessage.text("Yes"), state)
                write, state = agent.generate_next_message(
                    env.get_response(recheck.tool_calls[0]), state
                )
                self.assertEqual(write.tool_calls[0].name, name)
                self.assertEqual(write.tool_calls[0].arguments, arguments)
                response = env.get_response(write.tool_calls[0])
                self.assertFalse(response.error, response.content)

    def test_failed_write_is_recorded_and_not_automatically_retried(self):
        state = self.authenticated()
        question = self.agent.guarded_call(state, "order", "cancel_pending_order", self.arguments)
        state.history.append(question)
        recheck, state = self.agent.generate_next_message(UserMessage.text("Yes"), state)
        write, state = self.agent.generate_next_message(
            self.env.get_response(recheck.tool_calls[0]), state
        )
        self.agent.specialists["order"].invoke.return_value = self.proposal(
            "cancel_pending_order", self.arguments
        )
        failed = ToolMessage(
            id=write.tool_calls[0].id, role="tool", content="Timeout: outcome unknown", error=True
        )
        response, state = self.agent.generate_next_message(failed, state)
        self.assertFalse(response.tool_calls)
        self.assertIn("already attempted", response.content)
        self.assertTrue(any(e["kind"] == "tool_result" and e["error"] for e in state.events))

    def prepare_dialogue(self, confirmation="Yes"):
        self.agent.identity_extractor.invoke.return_value = IdentityFields(
            first_name="Emma", last_name="Smith", zip="10192"
        )
        self.agent.specialists["order"].invoke.side_effect = [
            self.proposal("get_order_details", {"order_id": self.oid}),
            self.proposal("cancel_pending_order", self.arguments),
            AIMessage(content="The order was cancelled."),
        ]
        return [
            "My name is Emma Smith and my zip code is 10192. Please check my laptop order.",
            confirmation,
            "Thank you. ###STOP###",
        ]

    def native_simulation(self, confirmation="Yes"):
        utterances = self.prepare_dialogue(confirmation)
        user = UserSimulator(
            llm="controlled-not-a-benchmark", instructions=str(self.task.user_scenario)
        )
        orchestrator = Orchestrator(
            domain="retail",
            agent=self.agent,
            user=user,
            environment=self.env,
            task=self.task,
            max_steps=40,
            max_errors=2,
            seed=42,
            validate_communication=True,
        )
        with patch(
            "tau2.user.user_simulator.generate",
            side_effect=[AssistantMessage(role="assistant", content=t) for t in utterances],
        ):
            return run_simulation(orchestrator, evaluation_type=EvaluationType.ALL)

    def native_config(self):
        name = "controlled-native-" + self.id()

        def factory(tools, domain_policy, **kwargs):
            self.agent.domain_policy = domain_policy
            return self.agent

        registry.register_agent_factory(factory, name)
        return TextRunConfig(
            domain="retail",
            language="en",
            agent=name,
            llm_agent="controlled",
            llm_user="controlled",
            max_steps=40,
            enforce_communication_protocol=True,
        )

    def test_official_language_builder_saves_native_results_and_agent_steps(self):
        utterances = self.prepare_dialogue()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "simulation.json"
            with patch(
                "tau2.user.user_simulator.generate",
                side_effect=[AssistantMessage(role="assistant", content=t) for t in utterances],
            ):
                simulation = run_task(
                    self.native_config(),
                    self.task,
                    seed=42,
                    output=output,
                    judge_model="upstream",
                    base_url="http://localhost:11434",
                )
            self.assertEqual(simulation.reward_info.reward, 1)
            self.assertIsNotNone(simulation.language_context)
            self.assertEqual(json.loads(output.read_text())["task_id"], "69")
            events = json.loads(output.with_suffix(".steps.json").read_text())
            self.assertTrue(any(e["kind"] == "confirmation_given" for e in events))

    def test_evaluator_failure_keeps_an_unscored_conversation_and_steps(self):
        utterances = self.prepare_dialogue()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "simulation.json"
            with (
                patch(
                    "tau2.user.user_simulator.generate",
                    side_effect=[AssistantMessage(role="assistant", content=t) for t in utterances],
                ),
                patch(
                    "tau2.runner.simulation.evaluate_simulation",
                    side_effect=RuntimeError("judge unavailable"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "judge unavailable"):
                    run_task(
                        self.native_config(),
                        self.task,
                        seed=42,
                        output=output,
                        judge_model="upstream",
                        base_url="http://localhost:11434",
                    )
            incomplete = json.loads(output.with_suffix(".incomplete.json").read_text())
            self.assertTrue(incomplete["unscored"])
            self.assertIsNone(incomplete["reward"])
            self.assertTrue(incomplete["messages"])
            self.assertTrue(output.with_suffix(".steps.json").exists())

    def test_real_orchestrator_simulator_and_native_db_reward_accept_confirmed_cancellation(self):
        simulation = self.native_simulation()
        self.assertEqual(simulation.reward_info.reward, 1)
        self.assertTrue(simulation.reward_info.db_check.db_match)
        actual = [
            c.name for m in simulation.messages for c in (getattr(m, "tool_calls", None) or [])
        ]
        reference = [a.name for a in self.task.evaluation_criteria.actions]
        self.assertNotEqual(
            actual, reference
        )  # Extra recheck is valid, exact sequence is not required.
        self.assertEqual(actual.count("cancel_pending_order"), 1)
        self.assertEqual(actual.count("get_order_details"), 2)
        self.assertEqual(
            get_environment().tools.db.orders[self.oid].status, "pending"
        )  # Fresh fixture.
        self.assertTrue(
            any(e["kind"] == "confirmation_given" for e in self.agent.last_session.events)
        )

    def test_native_dialogue_without_initial_identity_can_complete_after_name_zip_clarification(
        self,
    ):
        utterances = [
            "I received a laptop but found a better deal. Can I return it?",
            *self.prepare_dialogue(),
        ]
        user = UserSimulator(
            llm="controlled-not-a-benchmark", instructions=str(self.task.user_scenario)
        )
        orchestrator = Orchestrator(
            domain="retail",
            agent=self.agent,
            user=user,
            environment=self.env,
            task=self.task,
            max_steps=40,
            max_errors=2,
            seed=42,
            validate_communication=True,
        )
        with patch(
            "tau2.user.user_simulator.generate",
            side_effect=[AssistantMessage(role="assistant", content=t) for t in utterances],
        ):
            simulation = run_simulation(orchestrator, evaluation_type=EvaluationType.ALL)
        replies = [
            m.content for m in simulation.messages if isinstance(m, AssistantMessage) and m.content
        ]
        self.assertIn("first name", replies[1])
        self.assertNotIn("order ID", replies[1])
        self.assertEqual(simulation.reward_info.reward, 1)
        self.assertEqual(self.agent.last_session.user_id, self.uid)

    def test_refusing_a_required_change_receives_a_native_zero(self):
        simulation = self.native_simulation(confirmation="No.")
        self.assertEqual(simulation.reward_info.reward, 0)
        self.assertFalse(simulation.reward_info.db_check.db_match)
        self.assertEqual(self.env.tools.db.orders[self.oid].status, "pending")

    def test_mandatory_native_nl_assertion_cannot_be_replaced_by_db_success(self):
        self.task.evaluation_criteria.nl_assertions = ["Agent reports cancellation."]
        reply = AssistantMessage(
            role="assistant",
            content=json.dumps(
                {
                    "results": [
                        {
                            "expectedOutcome": "Agent reports cancellation.",
                            "metExpectation": False,
                            "reasoning": "Controlled negative judge",
                        }
                    ]
                }
            ),
        )
        with patch("tau2.evaluator.evaluator_nl_assertions.generate", return_value=reply) as judge:
            simulation = self.native_simulation()
        self.assertTrue(simulation.reward_info.db_check.db_match)
        self.assertEqual(simulation.reward_info.reward, 0)
        self.assertTrue(judge.called)
        validate_reward(self.task, simulation)

    def test_missing_judge_assertions_are_errors_and_judge_configuration_restores(self):
        from tau2.evaluator import evaluator_nl_assertions as nl

        self.task.evaluation_criteria.nl_assertions = ["Required fact"]
        simulation = SimpleNamespace(
            reward_info=RewardInfo(
                reward=1, reward_basis=[RewardType.DB, RewardType.NL_ASSERTION], nl_assertions=[]
            )
        )
        with self.assertRaisesRegex(ValueError, "omitted"):
            validate_reward(self.task, simulation)
        original = nl.DEFAULT_LLM_NL_ASSERTIONS
        with self.assertRaises(RuntimeError):
            with judge_settings("ollama_chat/llama3.1:8b", "http://localhost:11434"):
                self.assertEqual(nl.DEFAULT_LLM_NL_ASSERTIONS, "ollama_chat/llama3.1:8b")
                raise RuntimeError("test cleanup")
        self.assertEqual(nl.DEFAULT_LLM_NL_ASSERTIONS, original)

    def test_factory_does_not_give_the_task_or_reference_actions_to_the_agent(self):
        with patch("src.tau_native.agent.NativeRetailAgent") as constructor:
            create_native_agent(
                self.env.get_tools(),
                self.env.get_policy(),
                task="SECRET_REFERENCE_ACTIONS",
                llm="ollama_chat/qwen3:4b",
                llm_args={"specialist_model": "llama3.1:8b"},
            )
        self.assertNotIn("SECRET_REFERENCE_ACTIONS", str(constructor.call_args))
        self.assertEqual(constructor.call_args.kwargs["specialist_model"], "llama3.1:8b")
