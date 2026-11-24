"""The same seven metric choices as the reference evaluation repository."""
from deepeval.metrics import (
    ArgumentCorrectnessMetric, PlanAdherenceMetric, PlanQualityMetric,
    StepEfficiencyMetric, ToolCorrectnessMetric,
)
from deepeval.metrics.plan_quality.schema import AgentPlan
from .answer_correctness import build_metric as answer_metric
from .task_completion import build_metric as completion_metric

NAMES = ["Task Completion", "Step Efficiency", "Plan Quality", "Plan Adherence",
         "Argument Correctness", "Tool Correctness", "Answer Correctness"]
PLAN_METRICS = {"Plan Quality", "Plan Adherence"}



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
        "Task Completion": lambda: completion_metric(model),
        "Step Efficiency": lambda: StepEfficiencyMetric(**common, eval_mode="llm"),
        "Plan Quality": lambda: PublicPlanQualityMetric(**common, eval_mode="llm"),
        "Plan Adherence": lambda: PublicPlanAdherenceMetric(**common, eval_mode="llm"),
        "Argument Correctness": lambda: ArgumentCorrectnessMetric(**common, eval_mode="llm"),
        "Tool Correctness": lambda: ToolCorrectnessMetric(model=model, threshold=0.9, async_mode=False,
            eval_mode="llm", should_exact_match=True, should_consider_ordering=True),
        "Answer Correctness": lambda: answer_metric(model),
    }
    return [factories[name]() for name in (names or NAMES)]
