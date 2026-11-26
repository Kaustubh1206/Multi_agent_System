"""Grade only the final visible answer; source references remain grader-only."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Evaluations import model  # noqa: F401 -- configure DeepEval before importing it

from deepeval.metrics import GEval  # isort: skip
from deepeval.test_case import SingleTurnParams  # isort: skip


def build_metric(judge):
    return GEval(
        name="Answer Correctness",
        model=judge,
        threshold=0.8,
        async_mode=False,
        _include_g_eval_suffix=False,
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
            SingleTurnParams.CONTEXT,
        ],
        evaluation_steps=[
            "Grade ACTUAL_OUTPUT as the final customer-visible answer. CONTEXT contains source policy, "
            "the observed conversation/tool results and native checks. Earlier clarification need not "
            "be repeated. Do not grade internal JSON or routing as customer statements.",
            "EXPECTED_OUTPUT describes source goals and reference backend outcomes, not a verbatim "
            "answer or exact tool sequence. Check requested facts, correct conclusions and substantive "
            "completion claims against actual tool results and applicable policy.",
            "Accept concise equivalent wording and additional relevant supported facts. Do not require "
            "internal IDs, database hashes, exact ticket prose, or implementation details in the answer.",
            "Penalize invented facts, incorrect eligibility/status/amounts, unauthorized disclosure, "
            "missing requested information and claims of operations that did not actually succeed. "
            "Read negations literally. Cite the exact unsupported or contradictory claim; do not infer "
            "a missing earlier step solely from a short final answer.",
        ],
    )


if __name__ == "__main__":
    from Evaluations.thinkingbox.run import main

    main("Answer Correctness")
