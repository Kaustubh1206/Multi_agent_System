"""The same seven metric choices as the reference evaluation repository."""
from deepeval.metrics import (
    ArgumentCorrectnessMetric, GEval, PlanAdherenceMetric, PlanQualityMetric,
    StepEfficiencyMetric, TaskCompletionMetric, ToolCorrectnessMetric,
)
from deepeval.metrics.plan_quality.schema import AgentPlan
from deepeval.test_case import SingleTurnParams

NAMES = ["Task Completion", "Step Efficiency", "Plan Quality", "Plan Adherence",
         "Argument Correctness", "Tool Correctness", "Answer Correctness"]
PLAN_METRICS = {"Plan Quality", "Plan Adherence"}
TASK_DESCRIPTION = """
Complete the customer's retail request under the supplied active policy.
Resolve identity for private orders; public policies need no identity. Preserve
order IDs and operations across human clarification. Enforce ownership, item and
quantity selection, category windows, membership at purchase and exclusions.
Report recorded facts and eligibility only: this application is read-only.
Correct policy denial, clarification or required damage review can be success.
Never claim a return, cancellation, review case or refund has been performed.
"""


class RecordedPlan:
    """Use the actual public plan, not an invented plan or a no-plan perfect score."""
    def _get_prompt(self, method, *, template_class=None, **context):
        return super()._get_prompt(method, template_class=template_class or self.template_class, **context)

    def _extract_plan_from_trace(self, test_case):
        plan = (test_case.metadata or {}).get("public_plan", [])
        if not plan:
            raise ValueError("No public plan emitted; this metric is not applicable")
        return AgentPlan(plan=plan)

    async def _a_extract_plan_from_trace(self, test_case):
        return self._extract_plan_from_trace(test_case)


class PublicPlanQualityMetric(RecordedPlan, PlanQualityMetric):
    template_class = "PlanQualityMetric"


class PublicPlanAdherenceMetric(RecordedPlan, PlanAdherenceMetric):
    template_class = "PlanAdherenceMetric"


def build_metrics(model, names=None):
    common = {"model": model, "threshold": 0.8, "async_mode": False}
    factories = {
        "Task Completion": lambda: TaskCompletionMetric(**common, task=TASK_DESCRIPTION, eval_mode="llm"),
        "Step Efficiency": lambda: StepEfficiencyMetric(**common, eval_mode="llm"),
        "Plan Quality": lambda: PublicPlanQualityMetric(**common, eval_mode="llm"),
        "Plan Adherence": lambda: PublicPlanAdherenceMetric(**common, eval_mode="llm"),
        "Argument Correctness": lambda: ArgumentCorrectnessMetric(**common, eval_mode="llm"),
        "Tool Correctness": lambda: ToolCorrectnessMetric(model=model, threshold=0.9, async_mode=False,
            eval_mode="llm", should_exact_match=True, should_consider_ordering=True),
        "Answer Correctness": lambda: GEval(**common, name="Answer Correctness", _include_g_eval_suffix=False,
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT,
                               SingleTurnParams.EXPECTED_OUTPUT, SingleTurnParams.CONTEXT],
            evaluation_steps=[
                "Compare the final structured result and response with the expected conclusion and requirements.",
                "Use the supplied policy and authorized reference record to check dates, quantities, amounts and ownership.",
                "Penalize invented facts, disclosure of other customers' data and false claims of completed actions.",
                "Correct denial, clarification or required human review can be success; judge meaning rather than exact wording.",
            ]),
    }
    return [factories[name]() for name in (names or NAMES)]
