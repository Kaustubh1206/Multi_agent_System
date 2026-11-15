"""Real graph/checkpointer/tools, controlled model outputs; no Ollama required."""
import json
import unittest
from unittest.mock import patch

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.graphs import build_graph
from src.models import specialist_model, supervisor_model
from src.objects import AgentResult, IdentityOutput, OrderOutput, PolicyOutput, SupervisorOutput
from src.tools import SIMPLE_DATA as DATA, check_order_eligibility, identify_customer, load_customers


class ToolRecorder(BaseCallbackHandler):
    def __init__(self):
        self.names = []
        self.evidence_ids = []

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.names.append(serialized["name"])
        self.evidence_ids.append(kwargs.get("metadata", {}).get("evidence_id"))


class RetailGraphTests(unittest.TestCase):
    def setUp(self):
        dataset = patch('src.tools.DATA', DATA)
        dataset.start()
        self.addCleanup(dataset.stop)
        self.graph = build_graph(InMemorySaver())
        self.config = {"configurable": {"thread_id": self.id()}}
        self.patches = {}
        for name in ("supervisor", "identity", "order", "policy"):
            patcher = patch(f"src.nodes.{name}_llm")
            self.patches[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.supervisor = self.patches['supervisor']
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent="order", intent="order", order_id="ORD-001")
        self.patches['identity'].invoke.return_value = IdentityOutput(name="Avery Brooks")
        self.patches['order'].invoke.return_value = OrderOutput(topic="status")
        self.patches['policy'].invoke.return_value = PolicyOutput(topic="returns")

    def run_request(self, message, **state):
        return self.graph.invoke({"messages": [HumanMessage(content=message)], **state}, self.config)

    def test_models_and_removed_node(self):
        self.assertEqual(supervisor_model.model, "qwen3:4b")
        self.assertEqual(specialist_model.model, "qwen3:0.6b")
        self.assertNotIn("customer_input_node", self.graph.nodes)

    def test_policy_without_identity_and_native_tool_trace(self):
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent="policy", intent="refund/return")
        recorder = ToolRecorder()
        self.config['callbacks'] = [recorder]
        result = self.run_request("How long do I have to return an item?")
        self.assertEqual(result['agent_result']['reason_code'], 'policy_answered')
        self.assertIn('30', result['messages'][-1].content)
        self.assertEqual(recorder.names, ['get_policy'])
        self.assertEqual(recorder.evidence_ids, result['agent_result']['evidence'])
        self.patches['identity'].invoke.assert_not_called()
        self.patches['order'].invoke.assert_not_called()
        self.assertEqual(result['policy_state']['version'], 'simple-retail-v1')

    def test_name_identification_then_original_order_request(self):
        result = self.run_request("I am Avery Brooks. Where is ORD-001?")
        self.assertEqual(result['identity_state']['customer_id'], 'CUS-001')
        self.assertEqual([r['agent'] for r in result['agent_history']], ['identity', 'order'])
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertIn('shipped', result['messages'][-1].content)
        ids = {event['id'] for event in result['tool_history']}
        for agent in result['agent_history']:
            AgentResult.model_validate(agent)
            self.assertTrue(set(agent['evidence']) <= ids)

    def test_identity_pause_resume_preserves_original_request(self):
        self.patches['identity'].invoke.side_effect = [IdentityOutput(), IdentityOutput(email='customer01@example.com')]
        result = self.run_request("Where is ORD-001?")
        self.assertIn('__interrupt__', result)
        self.assertEqual(self.supervisor.invoke.call_count, 1)
        self.patches['order'].invoke.assert_not_called()
        result = self.graph.invoke(Command(resume='customer01@example.com'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(result['identity_state']['customer_id'], 'CUS-001')
        self.assertEqual(len(result['supervisor_history']), 2)
        self.assertEqual([r['reason_code'] for r in result['agent_history']],
                         ['missing_information', 'customer_identified', 'order_status_found'])
        self.assertIn('customer01@example.com', [m.content for m in result['messages']])
        self.assertGreater(len(list(self.graph.get_state_history(self.config))), 3)

    def test_order_id_pause_resume(self):
        self.supervisor.invoke.side_effect = [
            SupervisorOutput(next_agent='order', intent='order', clarification_question='What is your order ID?'),
            SupervisorOutput(next_agent='order', intent='order', order_id='ORD-001')]
        result = self.run_request('Where is my order?', verified_user_id='CUS-001')
        self.assertEqual(result['__interrupt__'][0].value, {'question': 'What is your order ID?'})
        self.assertEqual(self.supervisor.invoke.call_count, 1)
        result = self.graph.invoke(Command(resume='ORD-001'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(len(result['supervisor_history']), 2)
        self.assertEqual([m.content for m in result['messages']][1:3], ['What is your order ID?', 'ORD-001'])

    def test_failed_name_lookup_can_recover_with_email(self):
        self.patches['identity'].invoke.side_effect = [IdentityOutput(name='Nobody Here'), IdentityOutput(email='customer01@example.com')]
        result = self.run_request('Nobody Here, order ORD-001')
        self.assertEqual(result['agent_result']['reason_code'], 'not_found')
        result = self.graph.invoke(Command(resume='customer01@example.com'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')

    def test_email_resume_ignores_model_omissions_and_redundant_questions(self):
        # The model misses an explicit ID and would ask for it again.
        self.supervisor.invoke.return_value = SupervisorOutput(
            next_agent='order', intent='order',
            clarification_question='What is your order ID?')
        # A small extractor may miss email or retain an unrelated name.
        self.patches['identity'].invoke.return_value = IdentityOutput()
        result = self.run_request('Where is order ORD-001?')
        self.assertIn('name or email', result['__interrupt__'][0].value['question'])
        self.assertEqual(result['order_id'], 'ORD-001')
        result = self.graph.invoke(Command(resume='customer01@example.com'), self.config)
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['identity_state']['customer_id'], 'CUS-001')
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(result['order_id'], 'ORD-001')
        self.assertEqual(self.supervisor.invoke.call_count, 1)

    def test_name_correction_does_not_restart_the_order_request(self):
        self.patches['identity'].invoke.side_effect = [
            IdentityOutput(), IdentityOutput(name='Avery Brooks')]
        result = self.run_request('Where is order ORD-001?')
        result = self.graph.invoke(Command(resume='wrong@example.com'), self.config)
        self.assertIn('__interrupt__', result)
        result = self.graph.invoke(Command(resume='Avery Brooks'), self.config)
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['order_id'], 'ORD-001')
        self.assertEqual(result['identity_state']['email'], None)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(self.supervisor.invoke.call_count, 1)

    def test_order_id_then_email_continues_without_reclassification(self):
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent='order', intent='order')
        self.patches['identity'].invoke.return_value = IdentityOutput()
        result = self.run_request('Where is my order?')
        self.assertIn('order ID', result['__interrupt__'][0].value['question'])
        result = self.graph.invoke(Command(resume='ord-001'), self.config)
        self.assertIn('name or email', result['__interrupt__'][0].value['question'])
        result = self.graph.invoke(Command(resume='customer01@example.com'), self.config)
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(result['order_id'], 'ORD-001')
        self.assertEqual(self.supervisor.invoke.call_count, 1)
        self.assertEqual(result['supervisor_history'][-1]['source'], 'human_resume')
        self.assertIsNone(result['pending_agent'])

    def test_email_in_a_sentence_overrides_stale_model_fields(self):
        self.patches['identity'].invoke.side_effect = [
            IdentityOutput(), IdentityOutput(name='Wrong Person', email='wrong@example.com')]
        self.run_request('Where is ORD-001?')
        result = self.graph.invoke(Command(resume='My email is customer01@example.com.'), self.config)
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['identity_state']['email'], 'customer01@example.com')
        self.assertIsNone(result['identity_state']['name'])

    def test_customer_can_correct_the_order_id_during_resume(self):
        self.patches['identity'].invoke.return_value = IdentityOutput()
        self.run_request('Where is ORD-002?')
        result = self.graph.invoke(Command(resume='Actually ORD-001, my email is customer01@example.com'), self.config)
        self.assertEqual(result['order_id'], 'ORD-001')
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')

    def test_order_intent_survives_an_identity_route(self):
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent='identity', intent='order')
        self.patches['identity'].invoke.return_value = IdentityOutput()
        self.run_request('Where is ORD-001?')
        result = self.graph.invoke(Command(resume='customer01@example.com'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')

    def test_status_request_cannot_turn_into_return_after_email_resume(self):
        self.patches['identity'].invoke.return_value = IdentityOutput()
        # Reproduce the small order model choosing the wrong topic.
        self.patches['order'].invoke.return_value = OrderOutput(topic='returns')
        result = self.run_request('Where is order ORD-001?')
        self.assertIn('__interrupt__', result)
        result = self.graph.invoke(Command(resume='customer01@example.com'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(result['messages'][-1].content, 'Order ORD-001 (Blue mug) is shipped.')
        self.assertNotIn('check_order_eligibility', [e['tool'] for e in result['tool_history']])
        self.assertEqual(result['order_state']['topic'], 'status')
        self.assertEqual(result['order_state']['model_topic'], 'returns')
        self.assertEqual(result['order_state']['topic_source'], 'supervisor')
        self.assertEqual(result['customer_request'], 'Where is order ORD-001?')
        self.assertEqual(self.patches['order'].invoke.call_args.args[0][-1].content,
                         'Where is order ORD-001?')

    def test_explicit_order_topics_override_disagreeing_small_model(self):
        for topic, order_id, owner, expected in [
            ('returns', 'ORD-003', 'CUS-003', 'return_eligible'),
            ('cancellations', 'ORD-002', 'CUS-002', 'cancellation_eligible'),
            ('unsupported', 'ORD-003', 'CUS-003', 'unsupported_order_request'),
        ]:
            with self.subTest(topic=topic):
                self.config['configurable']['thread_id'] = topic
                self.supervisor.invoke.return_value = SupervisorOutput(
                    next_agent='order', intent='order', order_topic=topic)
                self.patches['order'].invoke.return_value = OrderOutput(topic='status')
                result = self.run_request(f'Help with {topic} for {order_id}', verified_user_id=owner)
                self.assertEqual(result['agent_result']['reason_code'], expected)
                self.assertEqual(result['order_state']['topic'], topic)
                self.assertEqual(result['order_state']['model_topic'], 'status')

    def test_new_request_replaces_prior_order_task(self):
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent='order', intent='order')
        result = self.run_request('Where is ORD-003?', verified_user_id='CUS-003')
        self.assertEqual(result['order_topic'], 'status')
        self.supervisor.invoke.return_value = SupervisorOutput(
            next_agent='order', intent='refund/return', order_topic='returns')
        # The specialist wrongly keeps its old topic; the new assigned task wins.
        result = self.run_request('Can I return ORD-003?')
        self.assertEqual(result['customer_request'], 'Can I return ORD-003?')
        self.assertEqual(result['agent_result']['reason_code'], 'return_eligible')
        self.assertEqual(result['order_topic'], 'returns')

    def test_other_customers_order_and_unknown_order_are_unavailable(self):
        for order_id in ('ORD-002', 'ORD-999'):
            with self.subTest(order_id=order_id):
                self.config['configurable']['thread_id'] = order_id
                self.supervisor.invoke.return_value = SupervisorOutput(next_agent='order', intent='order', order_id=order_id)
                result = self.run_request(f'Where is {order_id}?', verified_user_id='CUS-001')
                self.assertEqual(result['agent_result']['reason_code'], 'order_unavailable')
                self.assertEqual(result['order_state']['record'], {})
                self.assertNotIn('Desk lamp', result['messages'][-1].content)

    def test_claimed_customer_id_does_not_skip_identification(self):
        self.patches['identity'].invoke.return_value = IdentityOutput()
        result = self.run_request('CUS-001, where is ORD-001?', user_id='CUS-001')
        self.assertIn('__interrupt__', result)
        self.patches['order'].invoke.assert_not_called()

    def test_all_five_simple_scenarios(self):
        scenarios = json.loads((DATA/'scenarios.json').read_text())['scenarios']
        for scenario in scenarios:
            with self.subTest(scenario=scenario['id']):
                self.config['configurable']['thread_id'] = scenario['id']
                route = scenario['expected_route']
                question = 'What is your order ID?' if route == 'supervisor' else None
                self.supervisor.invoke.return_value = SupervisorOutput(
                    next_agent='policy' if route=='policy' else 'order', intent='refund/return' if scenario['id'] in {'S03','S04','S05'} else 'order',
                    order_id=scenario['order_id'], clarification_question=question)
                self.patches['order'].invoke.return_value = OrderOutput(topic='returns' if scenario['id'] in {'S04','S05'} else 'status')
                result = self.run_request(scenario['message'], verified_user_id=scenario['customer_id'])
                if scenario['id']=='S02':
                    self.assertIn('__interrupt__', result)
                    continue
                expected = {'S01':'order_status_found','S03':'policy_answered','S04':'return_eligible','S05':'return_window_expired'}
                self.assertEqual(result['agent_result']['reason_code'], expected[scenario['id']])
                if scenario['id']=='S04': self.assertIn('9 days', result['messages'][-1].content)
                if scenario['id']=='S05': self.assertIn('40 days', result['messages'][-1].content)

    def test_other_public_policy_topics(self):
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent='policy', intent='refund/return')
        for topic, phrase in [('refunds', '5–10 business days'), ('cancellations', 'Pending orders')]:
            with self.subTest(topic=topic):
                self.config['configurable']['thread_id'] = topic
                self.patches['policy'].invoke.return_value = PolicyOutput(topic=topic)
                result = self.run_request('Explain the ' + topic + ' policy')
                self.assertEqual(result['agent_result']['status'], 'completed')
                self.assertIn(phrase, result['messages'][-1].content)

    def test_tool_failure_is_recorded_and_stops(self):
        with patch('src.tools.load_customers', side_effect=OSError('fixture unavailable')):
            result = self.run_request('Where is ORD-001?', verified_user_id='CUS-001')
        self.assertEqual(result['agent_result']['status'], 'failed')
        self.assertEqual(result['agent_result']['reason_code'], 'tool_error')
        self.assertEqual(result['tool_history'][-1]['error'], 'OSError')
        self.assertEqual(self.graph.get_state(self.config).next, ())

    def test_invalid_specialist_output_is_classified(self):
        self.patches['order'].invoke.side_effect = OutputParserException('invalid topic')
        result = self.run_request('Where is ORD-001?', verified_user_id='CUS-001')
        self.assertEqual(result['agent_result']['reason_code'], 'invalid_model_output')
        self.assertEqual(result['tool_history'], [])

    def test_specialist_failure_after_resume_retains_human_answer(self):
        self.supervisor.invoke.side_effect = [
            SupervisorOutput(next_agent='order',intent='order',clarification_question='Order ID?'),
            ConnectionError('Ollama unavailable')]
        self.patches['order'].invoke.side_effect = ConnectionError('Ollama unavailable')
        self.run_request('Where is my order?', verified_user_id='CUS-001')
        result = self.graph.invoke(Command(resume='ORD-001'), self.config)
        self.assertEqual(self.supervisor.invoke.call_count, 1)
        self.assertEqual(result['agent_result']['reason_code'], 'model_error')
        self.assertIn('ORD-001', [m.content for m in result['messages']])

    def test_unsupported_request_stops_before_specialists(self):
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent='policy', intent='unsupported')
        result = self.run_request('Book a flight')
        self.assertEqual(result['agent_result']['reason_code'], 'unsupported_request')
        self.assertEqual(result['tool_history'], [])

    def test_no_fixture_mutation(self):
        before = (DATA/'customers.json').read_bytes()
        self.patches['order'].invoke.return_value = OrderOutput(topic='cancellations')
        self.supervisor.invoke.return_value = SupervisorOutput(next_agent='order',intent='order',order_id='ORD-002',order_topic='cancellations')
        result = self.run_request('Cancel ORD-002', verified_user_id='CUS-002')
        self.assertEqual(result['agent_result']['reason_code'], 'cancellation_eligible')
        self.assertIn('No cancellation has been performed', result['messages'][-1].content)
        self.assertEqual((DATA/'customers.json').read_bytes(), before)


class DataToolTests(unittest.TestCase):
    def setUp(self):
        dataset = patch('src.tools.DATA', DATA)
        dataset.start()
        self.addCleanup(dataset.stop)

    def test_name_email_case_and_conflicting_inputs(self):
        self.assertEqual(identify_customer.invoke({'name':'  AVERY BROOKS  '})['customer_id'], 'CUS-001')
        self.assertEqual(identify_customer.invoke({'email':'CUSTOMER01@EXAMPLE.COM'})['customer_id'], 'CUS-001')
        self.assertEqual(identify_customer.invoke({'name':'Avery Brooks','email':'customer02@example.com'})['status'], 'not_found')

    def test_ambiguous_name_requires_more_information(self):
        data = load_customers()
        data['customers'].append({**data['customers'][0], 'customer_id':'OTHER','email':'other@example.com'})
        with patch('src.tools.load_customers', return_value=data):
            self.assertEqual(identify_customer.invoke({'name':'Avery Brooks'}), {'status':'ambiguous'})
            self.assertEqual(identify_customer.invoke({'email':'customer01@example.com'})['customer_id'], 'CUS-001')

    def test_return_window_boundaries(self):
        for age, eligible in [(0,True),(30,True),(31,False),(-1,False)]:
            from datetime import date, timedelta
            data = load_customers()
            delivered = date.fromisoformat(data['customers'][2]['order']['delivered_on'])
            data['evaluation_date'] = (delivered+timedelta(days=age)).isoformat()
            with self.subTest(age=age), patch('src.tools.load_customers', return_value=data):
                result = check_order_eligibility.invoke({'customer_id':'CUS-003','order_id':'ORD-003','topic':'returns'})
                self.assertEqual(result['eligible'], eligible)

    def test_pending_vs_shipped_cancellation(self):
        for owner, order, expected in [('CUS-001','ORD-001',False),('CUS-002','ORD-002',True)]:
            result = check_order_eligibility.invoke({'customer_id':owner,'order_id':order,'topic':'cancellations'})
            self.assertEqual(result['eligible'], expected)


if __name__ == '__main__':
    unittest.main()
