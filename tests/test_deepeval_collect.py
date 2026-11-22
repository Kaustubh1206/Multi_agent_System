"""Real MAS execution/export tests with controlled actor models, no judge required."""
import unittest
from unittest.mock import patch

from src.graphs import build_graph
from src.objects import IdentityOutput, OrderOutput, PolicyOutput, SupervisorOutput
from evaluation.deepeval.collect import collect, load_scenarios, run_case


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.models = {}
        for name in ('supervisor', 'identity', 'order', 'policy'):
            patcher = patch(f'src.nodes.{name}_llm')
            self.models[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.models['supervisor'].invoke.return_value = SupervisorOutput(
            next_agent='order', intent='delivery', order_topic='status',
            plan=['Identify the customer', 'Look up their order', 'Report recorded shipment status'])
        self.models['identity'].invoke.return_value = IdentityOutput()
        self.models['order'].invoke.return_value = OrderOutput(topic='status')
        self.cases = {s['id']:s for s in load_scenarios()['scenarios']}

    def test_resume_trace_captures_direct_tools_and_original_plan(self):
        result = run_case(self.cases['V2-01'], build_graph)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['fixture_unchanged'])
        self.assertFalse(result['unused_scripted_messages'])
        state = result['state']
        self.assertEqual(state['agent_result']['reason_code'], 'order_status_found')
        self.assertEqual(state['plan'], state['supervisor_history'][0]['plan'])
        self.assertEqual([s['name'] for s in result['spans'] if s['type']=='tool'],
                         ['identify_customer','identify_customer','get_order'])
        self.assertTrue(any(s['metadata'].get('evidence_id') for s in result['spans'] if s['type']=='tool'))
        self.assertTrue(any(s['parent_id'] for s in result['spans']))
        # Expectations and fixture answers must never become actor input.
        initial_input = self.models['supervisor'].invoke.call_args.args[0][-1].content
        self.assertEqual(initial_input, 'Where is ORD-2001?')
        self.assertNotIn('expected', initial_input)
        self.assertEqual(self.models['supervisor'].invoke.call_count, 1)

    def test_expected_pause_is_retained_as_a_clarification_outcome(self):
        self.models['supervisor'].invoke.return_value = SupervisorOutput(
            next_agent='order', intent='refund/return', order_topic='returns', plan=['Check item condition'])
        self.models['order'].invoke.return_value = OrderOutput(topic='returns')
        result = run_case(self.cases['V2-17'], build_graph)
        self.assertEqual(result['status'], 'awaiting_input')
        self.assertEqual(result['state']['agent_result']['reason_code'], 'item_condition_required')
        self.assertIsNone(result['error'])

    def test_unsupported_cases_skip_without_invoking_actors(self):
        report = collect(['V2-21'], repeats=2, graph_factory=build_graph, preflight=False)
        self.assertEqual([c['status'] for c in report['cases']], ['skipped','skipped'])
        self.assertEqual(report['mode'], 'controlled_test')
        self.models['supervisor'].invoke.assert_not_called()

    def test_operational_preflight_failure_is_blocked_not_passed(self):
        with patch('evaluation.deepeval.collect.ollama_preflight', side_effect=ConnectionError('unavailable')):
            report = collect(['V2-01','V2-21'])
        self.assertEqual([c['status'] for c in report['cases']], ['blocked','skipped'])
        self.models['supervisor'].invoke.assert_not_called()

    def test_every_supported_case_has_an_independent_tool_oracle(self):
        import json
        from pathlib import Path
        oracle = json.loads(Path('evaluation/deepeval/expected_tools.json').read_text())
        self.assertEqual(set(oracle), {s['id'] for s in self.cases.values() if s['implementation_status']=='read_only_supported'})

    def test_all_read_only_scripts_finish_at_their_declared_outcome(self):
        for case in self.cases.values():
            if case['implementation_status'] != 'read_only_supported':
                continue
            with self.subTest(case=case['id']):
                topic = ('returns' if case['category'] in {'return','policy_bypass'} else
                         'cancellations' if case['id']=='V2-23' else 'status')
                agent = case['expected']['agent']
                self.models['supervisor'].invoke.return_value = SupervisorOutput(
                    next_agent=agent, intent='refund/return' if topic=='returns' or agent=='policy' else 'order',
                    order_topic=topic if agent=='order' else None,
                    plan=['Resolve required information','Look up authorized facts','Apply policy and answer'])
                self.models['identity'].invoke.return_value = IdentityOutput(name='Alex Morgan') if case['id']=='V2-02' else IdentityOutput()
                self.models['order'].invoke.return_value = OrderOutput(topic=topic)
                self.models['policy'].invoke.return_value = PolicyOutput(topic='refunds' if case['id']=='V2-05' else 'returns')
                execution = run_case(case, build_graph)
                self.assertIsNone(execution['error'])
                self.assertFalse(execution['unused_scripted_messages'])
                self.assertTrue(execution['fixture_unchanged'])
                for field in ('agent','status','reason_code'):
                    self.assertEqual(execution['state']['agent_result'][field], case['expected'][field])

    def test_unknown_selection_fails_instead_of_running_zero_cases(self):
        with self.assertRaises(ValueError):
            collect(['not-a-case'], preflight=False)
