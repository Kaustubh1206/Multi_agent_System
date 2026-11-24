"""Evaluate the final customer-facing answer with native DeepEval GEval."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Evaluations import model  # Load local environment before DeepEval imports.

from deepeval.metrics import GEval
from deepeval.test_case import SingleTurnParams

ANSWER_STEPS = [
    "You are the evaluator. Compare only the final customer-facing ACTUAL_OUTPUT with the final answer EXPECTED_OUTPUT. These are answers, not lists of instructions the customer must repeat.",
    "Use CONTEXT as factual evidence: applicable policy, fixed fixture date, authorized order facts, and observed conversation. The conversation can show earlier identification or clarification; do not demand those earlier questions again in the final answer.",
    "Check that the conclusion and requested facts are correct. Equivalent wording and extra relevant facts supported by context are acceptable. Do not require exact wording, JSON keys, UUIDs, internal references, or a database_changes field in a customer answer.",
    "Read negation literally: 'no refund has been approved' is not a refund approval. 'Eligible for return' is not 'return submitted'. Completing an eligibility check is not completing a refund payment.",
    "Correct denial for an expired/excluded item, an inaccessible order, or an unknown order can be the correct answer. Required clarification or human review is correct when expected; do not demand an unsupported action or private disclosure.",
    "Penalize missing requested facts, contradictions, invented shipment/payment facts, unsupported claims of completed actions, and actual unauthorized disclosure. Cite the specific contradictory or missing text; do not invent a failure absent from the evidence.",
]


def build_metric(model):
    return GEval(name="Answer Correctness", threshold=0.8, model=model, async_mode=False,
        _include_g_eval_suffix=False, evaluation_params=[SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT, SingleTurnParams.CONTEXT],
        evaluation_steps=ANSWER_STEPS)


if __name__ == "__main__":
    from Evaluations.runner import main
    main("Answer Correctness")
