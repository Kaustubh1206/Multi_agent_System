"""Offline graph checks; model responses are controlled, not live Ollama calls."""
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.graphs import build_graph
from src.models import specialist_model, supervisor_model
from src.objects import SupervisorOutput


class RetailGraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = build_graph(InMemorySaver())
        self.config = {"configurable": {"thread_id": self.id()}}

    def test_models_and_removed_node(self):
        self.assertEqual(supervisor_model.model, "qwen3:4b")
        self.assertEqual(specialist_model.model, "qwen3:0.6b")
        self.assertNotIn("customer_input_node", self.graph.nodes)

    @patch("src.nodes.supervisor_llm")
    def test_policy_routes_without_interrupt(self, model):
        model.invoke.return_value = SupervisorOutput(
            next_agent="policy", intent="refund/return"
        )
        result = self.graph.invoke(
            {"messages": [HumanMessage(content="What is your return policy?")]},
            self.config,
        )
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["next_agent"], "policy")
        self.assertEqual(len(result["supervisor_history"]), 1)
        self.assertEqual(result["messages"][-1].content,
                         "Your request reached the policy agent.")

    @patch("src.nodes.supervisor_llm")
    def test_pause_resume_and_retained_history(self, model):
        model.invoke.side_effect = [
            SupervisorOutput(next_agent="order", intent="order",
                             clarification_question="What is your order ID?"),
            SupervisorOutput(next_agent="order", intent="order", order_id="ORD-001"),
        ]
        result = self.graph.invoke(
            {"messages": [HumanMessage(content="Where is my order?")],
             "verified_user_id": "CUS-001"}, self.config
        )
        self.assertEqual(result["__interrupt__"][0].value,
                         {"question": "What is your order ID?"})
        self.assertEqual(model.invoke.call_count, 1)
        self.assertEqual(len(result["supervisor_history"]), 1)
        self.assertEqual(result["messages"][-1].content, "What is your order ID?")
        self.assertEqual(self.graph.get_state(self.config).next, ("supervisor",))

        result = self.graph.invoke(Command(resume="ORD-001"), self.config)
        self.assertEqual(model.invoke.call_count, 2)
        self.assertEqual(result["order_id"], "ORD-001")
        self.assertEqual(result["verified_user_id"], "CUS-001")
        self.assertIsNone(result["clarification_question"])
        self.assertEqual(len(result["supervisor_history"]), 2)
        self.assertEqual([m.content for m in result["messages"]], [
            "Where is my order?", "What is your order ID?", "ORD-001",
            "Your request reached the order agent.",
        ])
        self.assertEqual(model.invoke.call_args.args[0][-1].content, "ORD-001")
        self.assertEqual(self.graph.get_state(self.config).next, ())

    @patch("src.nodes.supervisor_llm")
    def test_repeated_clarification_preserves_identifiers(self, model):
        model.invoke.side_effect = [
            SupervisorOutput(next_agent="order", intent="order", user_id="CUS-002",
                             clarification_question="What is your order ID?"),
            SupervisorOutput(next_agent="order", intent="order",
                             clarification_question="Please provide the order number."),
            SupervisorOutput(next_agent="order", intent="order", order_id="ORD-002"),
        ]
        self.graph.invoke({"messages": [HumanMessage(content="Help with my order")]},
                          self.config)
        result = self.graph.invoke(Command(resume="I do not remember"), self.config)
        self.assertIn("__interrupt__", result)
        self.assertEqual(model.invoke.call_count, 2)
        result = self.graph.invoke(Command(resume="ORD-002"), self.config)
        self.assertEqual(result["user_id"], "CUS-002")
        self.assertEqual(result["order_id"], "ORD-002")
        self.assertEqual(len(result["supervisor_history"]), 3)
        self.assertEqual(len(result["messages"]), 6)


if __name__ == "__main__":
    unittest.main()
