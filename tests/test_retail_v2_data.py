"""Validate v2 evaluation specifications; this does not execute or score agents."""
import json
import re
import unittest
from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1] / 'evaluation' / 'retail-v2'


class RetailV2DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / 'customers.json').read_text())
        cls.suite = json.loads((ROOT / 'scenarios.json').read_text())
        cls.policy = (ROOT / 'policy.md').read_text()
        cls.today = date.fromisoformat(cls.data['evaluation_date'])
        cls.customers = {c['customer_id']: c for c in cls.data['customers']}
        cls.orders = {o['order_id']: (c, o) for c in cls.customers.values() for o in c['orders']}

    def test_counts_versions_and_fictional_identity(self):
        self.assertTrue(self.data['fictional'])
        self.assertEqual(len(self.customers), 20)
        self.assertEqual(len(self.orders), 30)
        self.assertEqual(len(self.suite['scenarios']), 30)
        self.assertEqual(sum(len(c['orders']) for c in self.customers.values()), 30)
        self.assertEqual(len({c['email'] for c in self.customers.values()}), 20)
        for c in self.customers.values():
            self.assertTrue(c['email'].endswith('@example.com'))
            ZoneInfo(c['timezone'])
            self.assertIn(c['member_tier'], {'standard', 'plus'})
            self.assertLessEqual(date.fromisoformat(c['member_since']), self.today)
        self.assertEqual(Counter(c['name'] for c in self.customers.values())['Alex Morgan'], 2)
        for field in ('policy_version', 'dataset_version', 'evaluation_date'):
            self.assertEqual(self.data[field], self.suite[field])
        self.assertIn('`' + self.data['policy_version'] + '`', self.policy)
        self.assertEqual(set(re.findall(r'^## (P\d+) ', self.policy, re.M)), {f'P{i}' for i in range(1, 11)})

    def test_order_dates_items_and_financial_totals(self):
        all_item_ids = []
        for _, order in self.orders.values():
            with self.subTest(order=order['order_id']):
                ordered = date.fromisoformat(order['ordered_on'])
                self.assertLessEqual(ordered, self.today)
                self.assertIn(order['status'], {'pending', 'processing', 'shipped', 'partially_shipped', 'delivered', 'cancelled'})
                self.assertIn(order['payment_status'], {'paid', 'pending', 'refunded'})
                self.assertIn(order['membership_at_purchase'], {'standard', 'plus'})
                self.assertTrue(order['items'])
                for item in order['items']:
                    all_item_ids.append(item['item_id'])
                    self.assertGreater(item['quantity'], 0)
                    self.assertGreaterEqual(item['returned_quantity'], 0)
                    self.assertGreaterEqual(item['pending_return_quantity'], 0)
                    self.assertLessEqual(item['returned_quantity'] + item['pending_return_quantity'], item['quantity'])
                    self.assertEqual(item['line_total_cents'], item['quantity'] * item['unit_price_cents'] - item['line_discount_cents'])
                    self.assertGreaterEqual(item['line_total_cents'], 0)
                    self.assertEqual(item['fulfillment_status'] == 'delivered', item['delivered_on'] is not None)
                    if item['delivered_on']:
                        delivered = date.fromisoformat(item['delivered_on'])
                        self.assertLessEqual(ordered, delivered)
                        self.assertLessEqual(delivered, self.today)
                    if item['damage_reported_on']:
                        self.assertLessEqual(delivered, date.fromisoformat(item['damage_reported_on']))
                        self.assertLessEqual(date.fromisoformat(item['damage_reported_on']), self.today)
                self.assertEqual(order['subtotal_cents'], sum(i['line_total_cents'] for i in order['items']))
                self.assertEqual(order['total_cents'], order['subtotal_cents'] + order['shipping_cents'] + order['tax_cents'])
                for shipment in order['shipments']:
                    self.assertTrue(set(shipment['item_ids']) <= {i['item_id'] for i in order['items']})
                self.assertLessEqual(sum(r['amount_cents'] for r in order['refunds']), order['total_cents'])
                for refund in order['refunds']:
                    self.assertLessEqual(date.fromisoformat(refund['approved_on']), self.today)
                    if refund['item_id']:
                        item = next(i for i in order['items'] if i['item_id'] == refund['item_id'])
                        self.assertLessEqual(refund['quantity'], item['returned_quantity'])
        self.assertEqual(len(set(all_item_ids)), len(all_item_ids))

    def test_scenario_references_and_declared_runtime_gaps(self):
        scenarios = self.suite['scenarios']
        dimensions = set(self.suite['evaluation_dimensions'])
        self.assertEqual(len(dimensions), 7)
        self.assertEqual(set().union(*(set(s['evaluation_dimensions']) for s in scenarios)), dimensions)
        self.assertEqual(len({s['id'] for s in scenarios}), len(scenarios))
        self.assertTrue({'identification', 'clarification', 'policy', 'status', 'return', 'cancellation', 'ownership', 'policy_bypass', 'recovery'} <= {s['category'] for s in scenarios})
        for scenario in scenarios:
            with self.subTest(scenario=scenario['id']):
                self.assertTrue(set(scenario['evaluation_dimensions']) <= dimensions)
                self.assertEqual(scenario['execution_status'], 'not_run')
                self.assertEqual(scenario['implementation_status'], 'requires_v2_runtime')
                self.assertTrue(scenario['messages'])
                self.assertTrue(all(isinstance(m, str) and m.strip() for m in scenario['messages']))
                self.assertTrue(set(scenario['policy_rules']) <= {f'P{i}' for i in range(1, 11)})
                owner = scenario['context']['customer_id']
                self.assertTrue(owner is None or owner in self.customers)
                self.assertTrue(scenario['expected']['response_requirements'])
                oid = scenario['case']['order_id']
                if oid:
                    if scenario.get('unknown_order'):
                        self.assertNotIn(oid, self.orders)
                        continue
                    actual_owner, order = self.orders[oid]
                    if owner and scenario['category'] != 'ownership':
                        self.assertEqual(actual_owner['customer_id'], owner)
                    if scenario['case']['item_id']:
                        self.assertIn(scenario['case']['item_id'], {i['item_id'] for i in order['items']})
                if 'lookup_customer_id' in scenario:
                    customer = self.customers[scenario['lookup_customer_id']]
                    self.assertIn(customer['email'], scenario['messages'][-1])

    def return_outcome(self, order, item, quantity):
        """Independent policy consistency check for the declared return cases."""
        available = item['quantity'] - item['returned_quantity'] - item['pending_return_quantity']
        if available < quantity or item['delivered_on'] is None or order['payment_status'] != 'paid':
            return 'ineligible'
        age = (self.today - date.fromisoformat(item['delivered_on'])).days
        if item['condition'] == 'damaged':
            if not item['damage_reported_on']:
                return 'needs_information'
            report_age = (date.fromisoformat(item['damage_reported_on']) - date.fromisoformat(item['delivered_on'])).days
            return 'review' if 0 <= report_age <= 7 else 'ineligible'
        if item['final_sale'] or not item['returnable'] or item['category'] == 'perishable':
            return 'ineligible'
        window = {'standard': 45 if order['membership_at_purchase'] == 'plus' else 30,
                  'electronics': 14, 'hygiene': 30}[item['category']]
        if not 0 <= age <= window:
            return 'ineligible'
        if item['condition'] == 'unknown' or item['original_packaging'] is None:
            return 'needs_information'
        if item['condition'] not in {'unused', 'tried_on'} or not item['original_packaging']:
            return 'ineligible'
        if item['category'] in {'electronics', 'hygiene'} and item['condition'] != 'unused':
            return 'ineligible'
        if item['category'] == 'hygiene' and item['seal'] != 'sealed':
            return 'needs_information' if item['seal'] == 'unknown' else 'ineligible'
        return 'eligible'

    def test_return_expectations_match_dates_categories_and_quantities(self):
        for scenario in self.suite['scenarios']:
            if 'return_check' not in scenario:
                continue
            _, order = self.orders[scenario['case']['order_id']]
            item = next(i for i in order['items'] if i['item_id'] == scenario['case']['item_id'])
            check = scenario['return_check']
            with self.subTest(scenario=scenario['id']):
                self.assertGreater(check['quantity'], 0)
                self.assertEqual(self.return_outcome(order, item, check['quantity']), check['expected_eligibility'])
                if 'expected_merchandise_refund_cents' in scenario:
                    self.assertEqual(scenario['expected_merchandise_refund_cents'], item['line_total_cents'] // item['quantity'] * check['quantity'])

    def test_cancellation_and_recovery_expectations(self):
        for scenario in self.suite['scenarios']:
            changes = scenario['expected']['database_changes']
            if changes:
                _, order = self.orders[scenario['case']['order_id']]
                self.assertEqual(order['status'], 'pending')
                self.assertEqual(changes, [{'order_id': order['order_id'], 'field': 'status', 'from': 'pending', 'to': 'cancelled'}])
                self.assertEqual(scenario['messages'][-1], 'Yes, cancel ORD-2002.')
                self.assertIn('P10', scenario['policy_rules'])
            if scenario['expected']['reason_code'] == 'order_not_cancellable':
                _, order = self.orders[scenario['case']['order_id']]
                observed = scenario.get('fault_injection', {}).get('before_execution', {}).get('status', order['status'])
                self.assertNotEqual(observed, 'pending')
            if scenario.get('fault_injection', {}).get('outcome') == 'committed_response_lost':
                self.assertEqual(scenario['fault_injection']['reconciliation'], 'cancelled')
                self.assertTrue(changes)
