"""Native DeepEval Task Completion, scoped to each ThinkingBox task."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Evaluations import model  # noqa: F401 -- configure DeepEval before importing it

from deepeval.metrics import TaskCompletionMetric  # isort: skip

SCOPE = """Evaluate this retail customer's requested task under the source retail policy.
Use the source policy, observed conversation, actual tool inputs/results and
native end-state checks. Reference write operations describe required outcomes,
not an exact required sequence of read/tool calls or required customer wording.
The task may require real state changes, ticket creation/update and follow-through.
A proposed tool call or a promise is not a completed action. Respect source-domain
authorization and confirmation requirements; do not invent universal retail rules.
Only OBSERVED operations/results are completed actions. Reference writes, source
policy instructions, database diffs and grader computations are not agent actions.
Earlier identification, clarification and successful actions need not be repeated
in the final answer. Denials and clarifications complete the task only when the
source goal/policy warrants them. Do not penalize concise, equivalent wording or
extra relevant supported facts. Identify specific evidence for any failure.
"""


class ThinkingBoxCompletion(TaskCompletionMetric):
    def _get_prompt(self, method, *, template_class=None, **context):
        return super()._get_prompt(
            method, template_class=template_class or "TaskCompletionMetric", **context
        )

    def measure(self, test_case, *args, **kwargs):
        self.task = (
            SCOPE
            + "\nCase goal and reference outcomes:\n"
            + test_case.expected_output
            + "\nSOURCE POLICY (requirements, not completed actions):\n"
            + (test_case.metadata or {}).get("source_policy", "")
        )
        self._is_task_provided = True
        return super().measure(test_case, *args, **kwargs)


def build_metric(judge):
    return ThinkingBoxCompletion(
        model=judge, threshold=0.8, async_mode=False, eval_mode="llm", task=SCOPE
    )


if __name__ == "__main__":
    from Evaluations.thinkingbox.run import main

    main("Task Completion")
