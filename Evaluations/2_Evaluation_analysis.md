# Review of the second evaluation

Source: [2_Evaluation.md](2_Evaluation.md), committed in `d009ec6`.
The original report is unchanged.

All 25 Task Completion scores pass: mean **0.914** from the displayed case
scores. Answer Correctness passes **20/25**, mean **0.784**. These are recorded
judge results, not independent proof of all behavior. The first report used a
different evaluation protocol, so its score difference cannot be attributed
entirely to MAS changes.

The five failures are not all caused by unnecessary detail:

| Case | AC score | Recorded issue | Change or interpretation |
|---|---:|---|---|
| V2-02 | 0.2 | The status reply repeats item/shipment data and an old delivery estimate. The judge also incorrectly treats the delivered item as contradicting the delivered order. | Brief single-order status replies; detailed shipment information remains available on request and for split shipments. The alleged contradiction is unsupported by the fixture. |
| V2-04 | 0.7 | The policy answer contains extra conditions/exclusions. The judge also incorrectly says the Plus-at-purchase extension lacks context. | A question specifically about windows now receives the relevant category windows. Questions about exclusions, damage or conditions still receive the authoritative full rules. The Plus rule is present in policy P4. |
| V2-23 | 0.7 | The judge says “no cancellation or refund has been performed” contradicts saying the order cannot be cancelled. | That is not a contradiction. Simplify this denial to the recorded processing status and cancellation restriction; omit the unrelated refund statement. Eligible cancellation checks still explicitly say no action was performed. |
| V2-26 | 0.7 | The reply states final sale but does not explicitly address the request to bypass it. | Add a short “I cannot override that policy” and explicitly state no return/refund was performed. The Task Completion reason suggesting compliance with the bypass would be better is wrong. |
| V2-29 | 0.2 | The judge rejects extra factual shipment details and claims exact wording is required. | Brief single-order status replies. The rubric already permits equivalent wording and supported relevant facts; the exact-match requirement was invented by the judge. |

Other judge reasons also deserve scrutiny. V2-17 unnecessarily criticizes a
missing conjunction even though the question has the same meaning. V2-29's
Task Completion reason criticizes failure to identify the deliberately unknown
first email, although rejecting it and accepting the correction is required.
Keep such reasons as judge disagreements, not unquestioned MAS failure labels
for future Matrace training.

Prompt version is `retail-v9` for the response changes. Final replies are
deterministic in the specialized nodes, so output brevity was changed there;
adding instructions to a topic-classification prompt would not shorten them.
Detailed facts remain in state/tool results for evaluation and future debugging.
The task goal now labels `completed`/`denied` as internal statuses, so the judge
is not asked to require those exact words in customer replies. Thresholds,
gold answers, policies and customers remain unchanged.

Rerun locally on this branch, keeping the result separate from earlier reports:

```bash
uv run --frozen python Evaluations/focus.py --case V2-02 --case V2-04 --case V2-23 --case V2-26 --case V2-29
uv run --frozen python Evaluations/focus.py
```

Controlled graph tests verify the response changes, retained detail requests,
split shipments and refusal of policy bypass. Live Ollama scores are not
available in the cloud environment; no claim of a new 25/25 score is made.
