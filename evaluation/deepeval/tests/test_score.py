"""Native DeepEval compatibility tests, not live model or judge benchmarks."""
import json
from pathlib import Path
import unittest
import subprocess
from unittest.mock import patch

from deepeval.models import DeepEvalBaseLLM
from evaluation.deepeval.score import NAMES, as_test_case, hard_checks, score_entry, summarize


class ControlledJudge(DeepEvalBaseLLM):
    """Return schema-valid responses solely to exercise native metric integration."""
    def load_model(self):
        return self

    def get_model_name(self):
        return 'controlled-test-judge-not-a-benchmark'

    def generate(self, prompt, schema=None, **kwargs):
        name = schema.__name__
        values = {'task':'Report recorded status for an identified order', 'outcome':'Reported shipped',
                  'plan':['Identify customer','Read order','Report status'], 'reason':'Controlled test response',
                  'score':10 if name == 'ReasonScore' else 1,
                  'verdict':1, 'verdicts':[{'verdict':'yes','reason':'Controlled test'}]}
        return schema.model_validate({k:values[k] for k in schema.model_fields})

    async def a_generate(self, prompt, schema=None, **kwargs):
        return self.generate(prompt, schema, **kwargs)


def entry():
    root = Path(__file__).resolve().parents[3]
    scenario = json.loads((root/'evaluation/retail-v2/scenarios.json').read_text())['scenarios'][0]
    tools = [
        {'tool':'identify_customer','arguments':{'name':None,'email':None},'result':{'status':'missing_information'}},
        {'tool':'identify_customer','arguments':{'name':None,'email':'avery.brooks@example.com'},'result':{'status':'matched','customer_id':'CUS-201'}},
        {'tool':'get_order','arguments':{'customer_id':'CUS-201','order_id':'ORD-2001'},'result':{'status':'found','order':{'status':'shipped'}}},
    ]
    result = {'agent':'order','status':'completed','reason_code':'order_status_found',
              'customer_message':'Order ORD-2001 is shipped.'}
    return {'status':'executed','repetition':1,'scenario':scenario,'execution':{
        'status':'completed','input':{'customer_messages':scenario['messages'],'session_customer_id':None},
        'state':{'agent_result':result,'order_id':'ORD-2001','identity_state':{'customer_id':'CUS-201'},
                 'supervisor_history':[{'plan':['Identify customer','Read order','Report status']}], 'tool_history':tools},
        'spans':[{'id':'tool-1','parent_id':None,'type':'tool','name':'get_order','input':tools[-1]['arguments'],
                  'output':tools[-1]['result'],'start_time':2,'end_time':3}],
        'steps':[{'observed_at':1,'update':{'supervisor':{'plan':['Identify customer','Read order','Report status']}}}],
        'fixture_unchanged':True,'unused_scripted_messages':[],'error':None}}


class ScoreTests(unittest.TestCase):
    def test_all_seven_native_metrics_measure_saved_observations(self):
        result = score_entry(entry(), 'Read-only policy. Do not invent shipment facts.', ControlledJudge())
        self.assertEqual([m['name'] for m in result['metrics']], NAMES)
        self.assertEqual(result['status'], 'passed', result)
        self.assertTrue(all(m['score'] is not None for m in result['metrics']))
        self.assertTrue(all(result['checks'].values()))

    def test_actual_mas_export_crosses_the_isolated_environment_boundary(self):
        root = Path(__file__).resolve().parents[3]
        script = """
import json
from unittest.mock import patch
from src.graphs import build_graph
from src.objects import SupervisorOutput,IdentityOutput,OrderOutput
from evaluation.deepeval.collect import collect
with patch('src.nodes.supervisor_llm') as supervisor, patch('src.nodes.identity_llm') as identity, patch('src.nodes.order_llm') as order:
    supervisor.invoke.return_value=SupervisorOutput(next_agent='order',intent='delivery',order_topic='status',plan=['Identify the customer','Look up the order','Report status'])
    identity.invoke.return_value=IdentityOutput()
    order.invoke.return_value=OrderOutput(topic='status')
    result=collect(['V2-01'],graph_factory=build_graph,preflight=False)
    print(json.dumps(result['cases'][0]))
"""
        execution = subprocess.run([str(root/'.venv/bin/python'),'-c',script],cwd=root,
                                   capture_output=True,text=True,check=True)
        recorded = json.loads(execution.stdout)
        result = score_entry(recorded, 'Read-only policy; report facts.', ControlledJudge())
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(len(result['metrics']), 7)
        self.assertEqual([t['tool'] for t in recorded['execution']['state']['tool_history']],
                         ['identify_customer','identify_customer','get_order'])

    def test_missing_plan_is_not_awarded_a_perfect_score(self):
        value = entry()
        value['execution']['state']['supervisor_history'] = []
        result = score_entry(value, 'Policy', ControlledJudge())
        plans = [m for m in result['metrics'] if m['name'].startswith('Plan')]
        self.assertTrue(all(m['status']=='not_applicable' and m['score'] is None for m in plans))
        self.assertEqual(result['status'], 'incomplete_metrics')

    def test_wrong_customer_fails_even_when_judge_approves(self):
        value = entry()
        value['execution']['state']['tool_history'][-1]['arguments']['customer_id'] = 'CUS-202'
        result = score_entry(value, 'Policy', ControlledJudge())
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['checks']['tool_2_customer'])

    def test_missing_tool_fails_tool_metric(self):
        value = entry()
        value['execution']['state']['tool_history'].pop()
        result = score_entry(value, 'Policy', ControlledJudge())
        tool = next(m for m in result['metrics'] if m['name']=='Tool Correctness')
        self.assertEqual(tool['status'], 'failed')

    def test_judge_error_never_becomes_zero_cost_success(self):
        with patch.object(ControlledJudge, 'generate', side_effect=ConnectionError('judge unavailable')):
            result = score_entry(entry(), 'Policy', ControlledJudge())
        self.assertEqual(result['status'], 'error')
        self.assertTrue(any(m['status']=='error' and m['score'] is None for m in result['metrics']))

    def test_skips_and_errors_have_explicit_denominators(self):
        skipped = {'scenario':{'id':'V2-21'},'status':'skipped','reason':'requires_mutations','repetition':1}
        result = score_entry(skipped, 'Policy', None)
        summary = summarize([result])
        self.assertEqual(summary['cases'], {'skipped':1})
        self.assertEqual(summary['metrics']['Task Completion']['scored'], 0)
        self.assertIsNone(summary['metrics']['Task Completion']['mean'])

    def test_trace_bridge_preserves_chronology_and_tool_arguments(self):
        case = as_test_case(entry(), 'Policy')
        self.assertEqual(case._trace_dict['children'][0]['name'], 'supervisor')
        self.assertEqual(case.tools_called[-1].input_parameters, {'customer_id':'CUS-201','order_id':'ORD-2001'})
        self.assertEqual(case.expected_tools[-1].name, 'get_order')
