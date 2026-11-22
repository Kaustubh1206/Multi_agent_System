"""Direct MAS + native DeepEval tests with controlled actors/judge, not model scores."""
import io
import json
import unittest
import subprocess
import sys
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from deepeval.models import DeepEvalBaseLLM
from src.graphs import build_graph
from src.objects import IdentityOutput, OrderOutput, PolicyOutput, SupervisorOutput
from Evaluations.dataset import TASKS, SKIPPED_TASKS, dataset, expected_tools
from Evaluations.metrics import NAMES, build_metrics
from Evaluations.runner import run_mas, evaluate_cases, main


class ControlledJudge(DeepEvalBaseLLM):
    def load_model(self):
        return self

    def get_model_name(self):
        return 'controlled-judge-not-a-benchmark'

    def generate(self, prompt, schema=None, **kwargs):
        values = {'task':'Report order status','outcome':'Reported shipped',
                  'plan':['Identify customer','Read order','Report status'],
                  'reason':'Controlled judge response','score':10 if schema.__name__=='ReasonScore' else 1,
                  'verdict':1,'verdicts':[{'verdict':'yes','reason':'Controlled response'}]}
        return schema.model_validate({key:values[key] for key in schema.model_fields})

    async def a_generate(self, prompt, schema=None, **kwargs):
        return self.generate(prompt, schema, **kwargs)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.models = {}
        for name in ('supervisor','identity','order','policy'):
            patcher = patch(f'src.nodes.{name}_llm')
            self.models[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.models['supervisor'].invoke.return_value = SupervisorOutput(
            next_agent='order',intent='delivery',order_topic='status',
            plan=['Identify customer','Look up the order','Report recorded status'])
        self.models['identity'].invoke.return_value = IdentityOutput()
        self.models['order'].invoke.return_value = OrderOutput(topic='status')

    def sample(self):
        with redirect_stdout(io.StringIO()):
            return run_mas(TASKS[0], build_graph)

    def test_dataset_uses_latest_cases_and_expected_outputs(self):
        self.assertEqual(len(TASKS),25)
        self.assertEqual(len(SKIPPED_TASKS),5)
        self.assertEqual(len(dataset.goldens),25)
        self.assertEqual(dataset.goldens[0].input,'Where is ORD-2001?')
        self.assertIn('shipped',dataset.goldens[0].expected_output)
        self.assertEqual([t.name for t in expected_tools(TASKS[0])],
                         ['identify_customer','identify_customer','get_order'])

    def test_same_thread_resume_and_gold_separation(self):
        case,checks=self.sample()
        self.assertTrue(all(checks.values()),checks)
        self.assertIn('avery.brooks@example.com',case.input)
        self.assertEqual(json.loads(case.actual_output)['reason_code'],'order_status_found')
        self.assertEqual(self.models['supervisor'].invoke.call_count,1)
        first_input=self.models['supervisor'].invoke.call_args.args[0][-1].content
        self.assertEqual(first_input,'Where is ORD-2001?')
        self.assertNotIn('expected',first_input)
        self.assertEqual([t.name for t in case.tools_called],['identify_customer','identify_customer','get_order'])
        self.assertTrue(case._trace_dict['children'])

    def test_all_read_only_tasks_end_at_expected_result(self):
        for task in TASKS:
            with self.subTest(case=task['id']):
                topic=('returns' if task['category'] in {'return','policy_bypass'} else
                       'cancellations' if task['id']=='V2-23' else 'status')
                agent=task['expected']['agent']
                self.models['supervisor'].invoke.return_value=SupervisorOutput(
                    next_agent=agent,intent='refund/return' if topic=='returns' or agent=='policy' else 'order',
                    order_topic=topic if agent=='order' else None,plan=['Check required context','Look up facts','Apply policy and answer'])
                self.models['identity'].invoke.return_value=IdentityOutput(name='Alex Morgan') if task['id']=='V2-02' else IdentityOutput()
                self.models['order'].invoke.return_value=OrderOutput(topic=topic)
                self.models['policy'].invoke.return_value=PolicyOutput(topic='refunds' if task['id']=='V2-05' else 'returns')
                with redirect_stdout(io.StringIO()):
                    case,checks=run_mas(task,build_graph)
                self.assertTrue(all(checks.values()),checks)
                self.assertEqual([t.name for t in case.tools_called],[t.name for t in case.expected_tools])

    def test_all_seven_native_metrics_measure_real_mas_response(self):
        case,checks=self.sample()
        for metric in build_metrics(ControlledJudge()):
            with self.subTest(metric=metric.__name__):
                metric.measure(case,_show_indicator=False)
                self.assertTrue(metric.is_successful(),metric.reason)
                self.assertIsNotNone(metric.score)

    def test_native_evaluate_prints_and_returns_all_seven_results(self):
        case,_=self.sample()
        with redirect_stdout(io.StringIO()) as output:
            results,incomplete=evaluate_cases([case],NAMES,ControlledJudge())
        self.assertFalse(incomplete)
        self.assertEqual(len(results),1)
        self.assertTrue(results[0].success,[(m.name,m.error) for m in results[0].metrics_data])
        self.assertEqual(len(results[0].metrics_data),7)
        self.assertTrue(output.getvalue())

    def test_missing_plan_is_not_scored_as_perfect(self):
        case,_=self.sample()
        case.metadata['public_plan']=[]
        with redirect_stdout(io.StringIO()) as output:
            results,incomplete=evaluate_cases([case],['Plan Quality','Plan Adherence'],ControlledJudge())
        self.assertEqual(results,[])
        self.assertTrue(incomplete)
        self.assertIn('NOT APPLICABLE',output.getvalue())

    def test_wrong_tool_is_failed_by_deepeval(self):
        case,_=self.sample()
        case.tools_called.pop()
        metric=build_metrics(ControlledJudge(),['Tool Correctness'])[0]
        metric.measure(case,_show_indicator=False)
        self.assertFalse(metric.is_successful())

    def test_judge_error_is_not_reported_as_pass(self):
        case,_=self.sample()
        with patch.object(ControlledJudge,'generate',side_effect=ConnectionError('judge unavailable')):
            with redirect_stdout(io.StringIO()):
                results,_=evaluate_cases([case],['Task Completion'],ControlledJudge())
        self.assertFalse(results[0].success)
        self.assertTrue(results[0].metrics_data[0].error)

    def test_every_entrypoint_runs_as_a_direct_file_from_its_folder(self):
        root=Path(__file__).resolve().parents[1]
        scripts=['task_completion','step_efficiency','plan_quality','plan_adherence',
                 'argument_correctness','tool_correctness','answer_correctness','eval_all']
        for script in scripts:
            with self.subTest(script=script):
                result=subprocess.run([sys.executable,script+'.py','--list','--limit','1'],
                    cwd=root/'Evaluations',capture_output=True,text=True,check=True)
                self.assertIn('V2-01',result.stdout)
                self.assertIn('SKIPPED',result.stdout)

    def test_main_preflight_failure_never_runs_the_mas(self):
        with patch('sys.argv',['task_completion.py','--case','V2-01']), \
             patch('Evaluations.runner.preflight',side_effect=RuntimeError('Missing Ollama models: llama3.1:8b')), \
             patch('Evaluations.runner.run_mas') as run, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main('Task Completion')
        self.assertEqual(error.exception.code,2)
        run.assert_not_called()

    def test_unsupported_selection_is_not_a_zero_case_pass(self):
        with patch('sys.argv',['task_completion.py','--case','V2-21']):
            with self.assertRaises(SystemExit) as error:
                main('Task Completion')
        self.assertEqual(error.exception.code,2)
