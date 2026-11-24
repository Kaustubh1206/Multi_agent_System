"""Actual source snapshots/tools/graph, controlled actors: not live model scores."""
import copy
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.objects import IdentityOutput, OrderOutput, SupervisorOutput
from src.nodes import latest_order_id
from src.tools import customer_dataset, get_policy, load_customers
from langchain_core.messages import HumanMessage
from Evaluations.tau_retail.dataset import SOURCE, build_probes, load_source
from Evaluations.tau_retail.evaluate import run_probe
from Evaluations.runner import evaluate_cases
from test_evaluations import ControlledJudge


class TauRetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probes, cls.fixture, cls.audit = build_probes()

    def setUp(self):
        self.models = {}
        for agent in ('supervisor', 'identity', 'order', 'policy'):
            patcher = patch(f'src.nodes.{agent}_llm')
            self.models[agent] = patcher.start()
            self.addCleanup(patcher.stop)
        self.models['supervisor'].invoke.return_value = SupervisorOutput(
            next_agent='order', intent='order', order_topic='status',
            plan=['Identify by email', 'Read authorized status', 'Answer'])
        self.models['identity'].invoke.return_value = IdentityOutput()
        self.models['order'].invoke.return_value = OrderOutput(topic='status')

    def run_one(self, probe, fixture=None, audit=None):
        with redirect_stdout(io.StringIO()):
            return run_probe(probe, fixture or self.fixture, audit or self.audit)

    def test_source_checksums_and_coverage_are_explicit(self):
        db, sets, manifest = load_source()
        self.assertEqual(manifest['release'], 'v2.0.0')
        self.assertEqual(len(sets['legacy114']), 114)
        self.assertEqual(len(sets['reviewed50']), 50)
        self.assertEqual(self.audit['native_end_to_end_tasks_evaluated'], 0)
        self.assertEqual(self.audit['source_tasks_linked_to_status_probes'], 49)
        self.assertEqual(len(self.probes), 76)
        self.assertEqual(self.audit['unique_order_status_probes'], 73)
        for probe in self.probes:
            if probe['kind'] == 'status':
                raw = db['orders'][probe['case']['order_id']]
                self.assertEqual(probe['expected_source_status'], raw['status'])
                self.assertEqual(probe['lookup_customer_id'], raw['user_id'])
                self.assertEqual(probe['messages'][1], db['users'][raw['user_id']]['email'])
        self.assertEqual(len({p['case']['order_id'] for p in self.probes if p['kind']=='status'}), 73)

    def test_all_legacy_probes_use_real_graph_and_read_only_source_facts(self):
        probes, fixture, audit = build_probes('legacy114')
        self.assertEqual(len(probes), 118)
        before = json.dumps(fixture, sort_keys=True)
        default = load_customers()['policy_version']
        for probe in probes:
            with self.subTest(probe=probe['id']):
                case, checks = self.run_one(probe, fixture, audit)
                self.assertTrue(all(checks.values()), checks)
                self.assertEqual([t.name for t in case.tools_called], probe['expected_tool_names'])
                self.assertEqual(case.actual_output, probe['expected_output'])
                self.assertEqual(case.metadata['evaluation_protocol'], 'tau-status-probes-v1')
                self.assertEqual(case.metadata['source_task_ids'], probe['source_task_ids'])
        self.assertEqual(before, json.dumps(fixture, sort_keys=True))
        self.assertEqual(default, load_customers()['policy_version'])

    def test_wrong_fixture_status_cannot_pass_independent_source_check(self):
        probe = self.probes[0]
        fixture = copy.deepcopy(self.fixture)
        for customer in fixture['customers']:
            for order in customer['orders']:
                if order['order_id'] == probe['case']['order_id']:
                    order['status'] = 'delivered' if probe['expected_source_status'] != 'delivered' else 'pending'
        _, checks = self.run_one(probe, fixture)
        self.assertFalse(checks['source_status'])
        self.assertFalse(checks['status_in_customer_reply'])

    def test_external_fixture_cannot_apply_project_return_policy_and_restores_after_error(self):
        baseline = load_customers()['policy_version']
        with self.assertRaisesRegex(RuntimeError, 'test cleanup'):
            with customer_dataset(self.fixture):
                self.assertEqual(get_policy.invoke({'topic': 'returns'}), {'status': 'unsupported'})
                raise RuntimeError('test cleanup')
        self.assertEqual(load_customers()['policy_version'], baseline)

    def test_native_ids_are_retained_without_changing_existing_ids(self):
        for text, expected in (('Order #W1267569', '#W1267569'), ('Order w1267569', '#W1267569'),
                               ('Order ORD-2001', 'ORD-2001'), ('Payment 1267569', None)):
            self.assertEqual(latest_order_id([HumanMessage(content=text)]), expected)

    def test_source_scenarios_and_expected_outcomes_are_not_model_inputs(self):
        probe = self.probes[0]
        case, checks = self.run_one(probe)
        self.assertTrue(all(checks.values()))
        request = self.models['supervisor'].invoke.call_args.args[0][-1].content
        self.assertEqual(request, probe['messages'][0])
        self.assertNotIn('evaluation_criteria', request)
        self.assertNotIn('reference', request)
        self.assertNotIn('expected', request)
        self.assertNotIn('processed', request)
        self.assertNotIn('delivery', case.actual_output)

    def test_native_deepeval_accepts_the_source_probe(self):
        case, checks = self.run_one(self.probes[0])
        self.assertTrue(all(checks.values()))
        with redirect_stdout(io.StringIO()):
            results, incomplete = evaluate_cases([case], ['Task Completion', 'Answer Correctness'], ControlledJudge())
        self.assertFalse(incomplete)
        self.assertTrue(results[0].success)
        self.assertEqual(len(results[0].metrics_data), 2)

    def test_offline_audit_and_direct_file_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, 'evaluate.py', '--audit'],
            cwd=root/'Evaluations'/'tau_retail', capture_output=True, text=True, check=True)
        audit = json.loads(result.stdout)
        self.assertEqual(audit['total_probes'], 76)
        self.assertEqual(audit['native_end_to_end_tasks_evaluated'], 0)
