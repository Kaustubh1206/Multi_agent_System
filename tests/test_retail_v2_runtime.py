"""V2 runtime checks: real dataset/tools/checkpoints, controlled Ollama responses."""
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.graphs import build_graph
from src.nodes import return_inputs
from src.objects import IdentityOutput, OrderOutput, PolicyOutput, SupervisorOutput
from src.nodes import check_order_eligibility
from src.tools import DATA, get_order, get_policy, identify_customer


class RetailV2RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.graph = build_graph(InMemorySaver())
        self.config = {'configurable': {'thread_id': self.id()}}
        self.models = {}
        for agent in ('supervisor', 'identity', 'order'):
            patcher = patch(f'src.nodes.{agent}_llm')
            self.models[agent] = patcher.start()
            self.addCleanup(patcher.stop)
        self.models['supervisor'].invoke.return_value = SupervisorOutput(
            next_agent='order', intent='refund/return', order_topic='returns')
        self.models['identity'].invoke.return_value = IdentityOutput()
        self.models['order'].invoke.return_value = OrderOutput(topic='returns')

    def ask(self, text, **state):
        return self.graph.invoke({'messages': [HumanMessage(content=text)], **state}, self.config)

    def test_active_dataset_identifies_priya_and_reads_her_order(self):
        self.assertEqual(DATA.name, 'retail-v2')
        self.assertEqual(identify_customer.invoke({'email':'priya.nair@example.com'}),
                         {'status':'matched', 'customer_id':'CUS-213'})
        result = get_order.invoke({'customer_id':'CUS-213', 'order_id':'ORD-2013'})
        self.assertEqual(result['order']['items'][0]['product_name'], 'Specialty tea gift box')

    def test_reported_conversation_resumes_once_and_requires_review(self):
        before = (DATA/'customers.json').read_bytes()
        result = self.ask('The tea gift box in ORD-2013 arrived damaged. Can I get a refund?')
        self.assertIn('name or email', result['__interrupt__'][0].value['question'])
        result = self.graph.invoke(Command(resume='priya.nair@example.com'), self.config)
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['identity_state']['customer_id'], 'CUS-213')
        self.assertEqual(result['order_id'], 'ORD-2013')
        self.assertEqual(result['agent_result']['status'], 'needs_review')
        self.assertEqual(result['agent_result']['reason_code'], 'damage_review_required')
        self.assertIn('within the 7-day review window', result['messages'][-1].content)
        self.assertIn('no return or refund has been approved', result['messages'][-1].content)
        self.assertEqual(self.models['supervisor'].invoke.call_count, 1)
        self.assertEqual(self.graph.get_state(self.config).next, ())
        self.assertEqual((DATA/'customers.json').read_bytes(), before)

    def test_duplicate_name_is_resolved_by_email(self):
        self.models['supervisor'].invoke.return_value = SupervisorOutput(next_agent='order',intent='order',order_topic='status')
        self.models['order'].invoke.return_value = OrderOutput(topic='status')
        self.models['identity'].invoke.return_value = IdentityOutput(name='Alex Morgan')
        result = self.ask('Alex Morgan here. Where is ORD-2009?')
        self.assertEqual(result['agent_result']['reason_code'], 'ambiguous')
        result = self.graph.invoke(Command(resume='alex.morgan.east@example.com'), self.config)
        self.assertEqual(result['identity_state']['customer_id'], 'CUS-209')
        self.assertEqual(result['agent_result']['reason_code'], 'order_status_found')

    def test_nested_second_order_and_ownership(self):
        self.assertEqual(get_order.invoke({'customer_id':'CUS-201','order_id':'ORD-2021'})['status'], 'found')
        self.assertEqual(get_order.invoke({'customer_id':'CUS-213','order_id':'ORD-2002'}), {'status':'unavailable'})
        self.assertEqual(get_order.invoke({'customer_id':'CUS-213','order_id':'ORD-2999'}), {'status':'unavailable'})

    def test_split_shipment_status_and_delivered_line_return(self):
        self.models['supervisor'].invoke.return_value = SupervisorOutput(next_agent='order',intent='delivery',order_topic='status')
        self.models['order'].invoke.return_value = OrderOutput(topic='status')
        result = self.ask('Where are the items in ORD-2017?', verified_user_id='CUS-217')
        message = result['messages'][-1].content
        self.assertIn('Canvas tote', message)
        self.assertIn('Glass carafe', message)
        self.assertIn('2026-11-10', message)
        self.assertIn('in_transit', message)
        self.assertIn('not a guarantee', message)
        returned = check_order_eligibility.invoke({'customer_id':'CUS-217','order_id':'ORD-2017',
            'topic':'returns','item_id':'ITEM-2017-A'})
        self.assertTrue(returned['eligible'])
        undelivered = check_order_eligibility.invoke({'customer_id':'CUS-217','order_id':'ORD-2017',
            'topic':'returns','item_id':'ITEM-2017-B'})
        self.assertEqual(undelivered['reason_code'], 'item_not_delivered')

    def test_multi_item_followup_retains_the_task(self):
        result = self.ask('Can I return something from ORD-2028?', verified_user_id='CUS-208')
        self.assertEqual(result['agent_result']['reason_code'], 'item_required')
        result = self.graph.invoke(Command(resume='The USB-C hub, ITEM-2028-B.'), self.config)
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['agent_result']['reason_code'], 'return_window_expired')
        self.assertIn('14-day', result['messages'][-1].content)

    def test_quantity_followup_and_condition_followup(self):
        result = self.ask('Can I return ORD-2003?', verified_user_id='CUS-203')
        self.assertEqual(result['agent_result']['reason_code'], 'quantity_required')
        result = self.graph.invoke(Command(resume='1 unit'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'return_eligible')
        self.config['configurable']['thread_id'] = 'unknown-condition'
        result = self.ask('Can I return ORD-2020?', verified_user_id='CUS-220')
        self.assertEqual(result['agent_result']['reason_code'], 'item_condition_required')
        result = self.graph.invoke(Command(resume='Unused and in original packaging.'), self.config)
        self.assertEqual(result['agent_result']['reason_code'], 'return_eligible')

    def test_partial_return_value_is_not_a_refund_claim(self):
        result = self.ask('Can I return 1 unit from ORD-2029?', verified_user_id='CUS-209')
        self.assertEqual(result['order_state']['eligibility']['merchandise_value_cents'], 2300)
        self.assertIn('23.00', result['messages'][-1].content)
        self.assertIn('No return has been requested', result['messages'][-1].content)

    def test_all_declared_return_cases_against_runtime_rules(self):
        data = json.loads((DATA/'scenarios.json').read_text())
        for case in data['scenarios']:
            if 'return_check' not in case:
                continue
            with self.subTest(case=case['id']):
                result = check_order_eligibility.invoke({
                    'customer_id':case['context']['customer_id'], 'order_id':case['case']['order_id'],
                    'topic':'returns', 'item_id':case['case']['item_id'], 'quantity':case['return_check']['quantity']})
                expected = {'review':'needs_review','ineligible':'ineligible','eligible':'eligible','needs_information':'needs_information'}[case['return_check']['expected_eligibility']]
                self.assertEqual(result['outcome'], expected)

    def test_policy_sections_match_active_version(self):
        for topic in ('returns','refunds','cancellations','shipping','identification'):
            result = get_policy.invoke({'topic':topic})
            self.assertEqual(result['version'], 'retail-v2.0')
            self.assertEqual(result['status'], 'found')
        text = get_policy.invoke({'topic':'returns'})['answer']
        self.assertIn('45', text)
        self.assertIn('14', text)
        self.assertIn('human review', text)

    def test_unfavorable_customer_condition_is_not_ignored(self):
        before = get_order.invoke({'customer_id':'CUS-203','order_id':'ORD-2003'})['order']
        inputs = return_inputs(before, ['It is not unused and not in original packaging.'])
        self.assertEqual(inputs['condition'], 'used')
        self.assertFalse(inputs['packaging'])
        result = check_order_eligibility.invoke({'customer_id':'CUS-203','order_id':'ORD-2003',
            'topic':'returns','quantity':1, **inputs})
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason_code'], 'item_condition_ineligible')
        # A favorable claim cannot override a known opened hygiene seal.
        result = check_order_eligibility.invoke({'customer_id':'CUS-210','order_id':'ORD-2010',
            'topic':'returns','seal':'sealed'})
        self.assertEqual(result['reason_code'], 'opened_hygiene_item')

    def test_late_damage_report_does_not_get_automatic_review_approval(self):
        result = check_order_eligibility.invoke({'customer_id':'CUS-219','order_id':'ORD-2019','topic':'returns'})
        self.assertEqual(result['reason_code'], 'damage_report_window_expired')
        self.assertFalse(result['eligible'])

    def test_public_shipping_question_does_not_require_an_order_or_identity(self):
        self.models['supervisor'].invoke.return_value = SupervisorOutput(next_agent='policy', intent='delivery')
        with patch('src.nodes.policy_llm') as model:
            model.invoke.return_value = PolicyOutput(topic='shipping')
            result = self.ask('Are estimated shipping dates guaranteed?')
        self.assertNotIn('__interrupt__', result)
        self.assertEqual(result['agent_result']['reason_code'], 'policy_answered')
        self.assertIn('not a guarantee', result['messages'][-1].content)
        self.models['identity'].invoke.assert_not_called()

    def test_cancellation_still_only_checks_eligibility(self):
        self.models['supervisor'].invoke.return_value = SupervisorOutput(next_agent='order',intent='order',order_topic='cancellations')
        result = self.ask('Can I cancel ORD-2002?', verified_user_id='CUS-202')
        self.assertEqual(result['agent_result']['reason_code'], 'cancellation_eligible')
        self.assertIn('No cancellation or refund has been performed', result['messages'][-1].content)
        self.assertEqual(get_order.invoke({'customer_id':'CUS-202','order_id':'ORD-2002'})['order']['status'], 'pending')
