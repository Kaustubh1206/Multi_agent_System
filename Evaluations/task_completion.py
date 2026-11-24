"""Evaluate completion of each specific retail task, including its actual workflow."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Evaluations import model  # Load local environment before DeepEval imports.

from deepeval.metrics import TaskCompletionMetric

COMPLETION_SCOPE = """
Evaluate only this customer's requested task and the case-specific requirements.
A status request does not require return calculations, cancellation or a refund.
A public policy answer requires no customer identity. An already identified
session satisfies the identity prerequisite. A unique name/email lookup is the
prototype's identification rule, not production login.
A correct policy denial or generic unavailable response can fully complete the
task. If the expected outcome is missing-information clarification or review
required, asking the relevant question or explaining review is success for this
read-only task. Do not require unsupported submission, payment or review handoff.
Use observed conversation, node outcomes and tool inputs/results to assess steps
that occurred earlier. Do not infer that a step was omitted merely because the
final answer does not narrate it. Internal classifier prompts are instructions,
not completed actions. Negated claims are not positive action claims.
Internal tool arguments and evidence IDs are not customer-facing disclosures.
State a specific evidence-backed omission or contradiction if scoring below full
completion. Do not require every retail operation on every request.
"""


class ScopedTaskCompletionMetric(TaskCompletionMetric):
    """Scope the native metric per case, without changing its scoring algorithm."""
    def _get_prompt(self, method, *, template_class=None, **context):
        return super()._get_prompt(method, template_class=template_class or "TaskCompletionMetric", **context)

    def scoped_case(self, case):
        metadata = case.metadata or {}
        self.task = COMPLETION_SCOPE + "\nCase requirements:\n" + metadata.get("completion_goal", case.input)
        self._is_task_provided = True
        scoped = case.model_copy()
        # Use observed workflow facts, not the agents' generic system prompts.
        scoped._trace_dict = metadata.get("task_evidence", case._trace_dict)
        return scoped

    def measure(self, test_case, *args, **kwargs):
        return super().measure(self.scoped_case(test_case), *args, **kwargs)

    async def a_measure(self, test_case, *args, **kwargs):
        return await super().a_measure(self.scoped_case(test_case), *args, **kwargs)


def build_metric(model):
    return ScopedTaskCompletionMetric(model=model, threshold=0.8, async_mode=False,
                                      eval_mode="llm", task=COMPLETION_SCOPE)


if __name__ == "__main__":
    from Evaluations.runner import main
    main("Task Completion")
