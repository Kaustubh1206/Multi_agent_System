# Review of the first 25-task evaluation

Source: [1_Evaluation.md](1_Evaluation.md), committed in `c9c6320`.
The original report is preserved. This review focuses on Answer Correctness
and Task Completion, before any Matrace work.

## What the report establishes

Across the 25 displayed case rows, Task Completion averages **0.672**, with
**12/25** scores meeting its 0.8 threshold. Answer Correctness averages
**0.436**, with **3/25** meeting 0.8. These are means of the displayed rounded
scores, not recomputed judge results. DeepEval printed 24 cases together and
V2-05 separately; the 24-case summary alone excludes the failed refund FAQ.

For 24 cases, the reported `agent`, `status`, and `reason_code` match the
scenario specification. V2-05 has an empty result. Matching these three fields
does **not** establish 96% answer correctness or prove the entire workflow was
correct. Several answers omit useful explanations, and the report does not
contain a complete execution record for independently verifying every step.

There are also clear evaluation problems. For example:

- V2-01 reports the order shipped, yet the correctness reason says that shipped
  conflicts with an expected answer that also requests shipped.
- V2-07 and V2-16 explicitly deny performing an action, yet their reasons allege
  completed actions. Negation is being misread.
- V2-06 explicitly reports the tote and carafe separately, yet the judge says
  item-level information is missing.
- Status and privacy-denial cases are penalized for unrelated return checks or
  for not successfully retrieving an inaccessible order.
- Internal `references` and evidence UUIDs were supplied as the actual answer.
  Some reasons treat those fields as invented customer-facing content.
- Expected text combined earlier workflow requirements, final answer facts,
  and internal JSON fields. A final status answer was penalized for not asking
  again for a name/email that had already been supplied earlier.

The response defects and evaluation defects need separate fixes. A low model
judge score alone is not a reliable failure label for future Matrace training.

## All 25 cases

TC = reported Task Completion; AC = reported Answer Correctness. Observations
refer to the recorded customer message and specification, not a new live run.

| Case | TC | AC | Review and action |
|---|---:|---:|---|
| V2-01 | 0.8 | 0.2 | Shipped status and estimate are supported. The judge confuses prior identification with final-answer content. Keep the response behavior; expose the actual conversation separately to the evaluator. |
| V2-02 | 0.8 | 0.2 | Delivered status is supported. The final JSON alone cannot independently prove the earlier duplicate-name clarification, and the judge's allegation that it chose by name is unsupported by that final answer. Retain the email-resolution regression test and evaluate observed dialogue. |
| V2-03 | 0.6 | 0.2 | ORD-2022 is delivered on the fixture date. Grade the earlier order-ID clarification as workflow, not text that must be repeated in the answer. Give the judge the fixed date instead of the host date. |
| V2-04 | 0.8 | 0.4 | The full policy contains the correct electronics and Plus rules, but is unnecessarily long. Present the relevant category windows first, retaining membership/exclusion limits. |
| V2-05 | 0.2 | 0.2 | Genuine incomplete result: `{}`, no policy tool call. Clarify that general refund timing/shipping fees are public policy; suppress identifier questions when policy is selected. Surface incomplete runs explicitly. The pasted report does not prove whether the original cause was misrouting or a redundant clarification. |
| V2-06 | 0.6 | 0.2 | Tote delivered November 10 and carafe in transit are explicitly reported. Keep item/shipment lookup; correct the evaluation framing rather than remove factual detail to match wording. |
| V2-07 | 0.7 | 0.2 | Inclusive day-30 eligibility is correct; the answer says no action occurred. Add the standard-category rationale; teach the judge to distinguish eligibility from submission. |
| V2-08 | 0.8 | 0.8 | Correct day-31 denial. Retain the boundary and make the category/purchase-tier rationale explicit. |
| V2-09 | 0.7 | 0.2 | Day-40 eligibility within 45 days is correct, but the reply does not name Plus membership at purchase. Add that explanation; preserve the no-action statement. |
| V2-10 | 0.8 | 0.4 | Correct denial, incomplete explanation: the 30-day window is stated without explaining why current Plus does not apply. Explain membership at purchase and later membership changes. |
| V2-11 | 0.7 | 0.6 | Correct day-14 eligibility. State that electronics use 14 days for every membership tier; retain the no-action distinction. |
| V2-12 | 0.8 | 0.8 | Correct day-15 electronics denial. Keep the decision, add the category rationale. |
| V2-13 | 0.8 | 0.6 | The opened-seal denial matches policy. No new policy exception is warranted; grade the answer's logical conclusion. |
| V2-14 | 0.7 | 0.4 | Correct eligibility at day 20, but the reply does not explicitly name the hygiene unused/sealed requirement. Include the category rule. |
| V2-15 | 0.8 | 0.6 | Correct exclusion, vague wording. Say the item is marked final sale instead of only saying it is excluded. |
| V2-16 | 0.8 | 0.2 | Correct seven-day damage-review requirement, with explicit no-submission/no-approval wording. Preserve it. A requirement for human review is the expected read-only endpoint, not evidence that a case was opened. |
| V2-17 | 0.6 | 0.7 | Correctly asks for missing condition/packaging, but unnecessarily mentions hygiene seals for a standard item. Ask only the relevant question; remaining paused is expected here. |
| V2-18 | 0.6 | 0.2 | Correct one-unit eligibility and USD 23.00 discounted merchandise value. Explicitly state remaining available quantity. The current answer already says the amount is not an approved refund. |
| V2-19 | 0.9 | 0.2 | Correct denial after the item follow-up: 20 days exceeds electronics' 14 days. Name the category in the explanation; give the evaluator the earlier clarification as workflow evidence. |
| V2-20 | 0.6 | 0.7 | Correct duplicate-return denial. Replace the vague 'returned or pending' wording with the actual returned/pending counts. Internal evidence IDs should not be graded as customer statements. |
| V2-23 | 0.6 | 0.6 | Processing-order cancellation denial is correct and explicitly says no action occurred. The read-only task does not require executing a cancellation to be complete. |
| V2-24 | 0.2 | 0.7 | Generic unavailable response is the required ownership boundary. Do not expose the actual owner or require successful retrieval. Internal denial metadata is not itself a customer disclosure. |
| V2-25 | 0.2 | 0.8 | Unknown order correctly gets the same generic unavailable response. Treat this expected denial as task completion, not a lookup failure that must be overcome. |
| V2-26 | 0.9 | 0.2 | Correctly resists the instruction to bypass policy. Explicitly name final sale. Keep the refusal; do not approve a return to satisfy the customer's requested action. |
| V2-29 | 0.8 | 0.6 | Final shipped status is supported. Correction handling and retained order ID belong to the workflow; preserve the existing regression test and expose the actual follow-up sequence. |

V2-21, V2-22, V2-27, V2-28, and V2-30 are the five unsupported action/recovery
targets, not omitted failures among these 25 read-only cases.

## Changes made

### Application prompts and responses

Prompt version is now `retail-v8`. The supervisor distinguishes general refund
timing/fees from transaction-specific bank posting; the policy prompt agrees with
that distinction. Public-policy routing clears an unnecessary identifier question.
This does not override a model that incorrectly chooses an order route; that
routing behavior still needs a live check.

Order/policy prompts emphasize the requested operation, applicable rules, and
the difference between an eligibility result and a performed action. Because
these agents return topic objects rather than generate the final explanation,
prompt edits alone cannot change their existing deterministic customer messages.
Small changes therefore live alongside the specialized nodes: purchase-tier and
category rationale, explicit final-sale wording, remaining/pending quantities,
relevant condition questions, and relevant excerpts of retrieved public policy.
Other policy questions retain the full authoritative text. No separate retail
rules module or additional model call was added.

Customers, policy text, scenario requirements, dates, thresholds and eligibility
decisions are unchanged. Fixture-specific expected answers are never injected
into the MAS prompts or conversation.

### Evaluation inputs and definitions

The protocol is now **`answer-task-v2`**:

- Answer Correctness receives the customer-visible final text, a final-answer
  reference, applicable policy, authorized fixture facts, and actual dialogue.
  Full structured results remain in metadata for independent checks.
- Task Completion receives the case's original response requirements and
  observed conversation/node/tool outcomes. Generic classifier instructions
  are not treated as actions or universal checklists for every customer task.
- Judge instructions explicitly distinguish a correct denial, clarification,
  review requirement, eligibility, submission, and bank posting. They still
  penalize unsupported claims, missing requested facts and privacy violations.
- Status/reason, ownership, fixture immutability and reply-consumption checks
  remain. New checks flag missing final replies and unexpected pauses.
- The native Task Completion algorithm and GEval remain in use. Both thresholds
  stay **0.8**. There are no case-specific score overrides or automatic passes.

These evaluator changes correct the task definition and representation. They
also mean new scores cannot be attributed entirely to better MAS behavior.
Keep the first report, record the new protocol, manually review disagreements,
and use the same revised protocol for subsequent MAS comparisons.

## Why the metric scripts were short

The reference repository repeats the agent loop, test-case creation and metric
setup inside individual files. This repository shared that repeated code in
`runner.py` and the metric constructors in `metrics.py`; the individual files
were command entrypoints. They were running real DeepEval metrics, not placeholders.

The reference's Answer Correctness script extracts `structured_response.answer`.
Our previous runner instead serialized the entire result object. That was a
meaningful difference, now corrected to use `customer_message`.

For easier editing, the two metrics under active development now define their
rubrics/builders directly in [answer_correctness.py](answer_correctness.py) and
[task_completion.py](task_completion.py). The other five constructors remain in
[metrics.py](metrics.py). [runner.py](runner.py) still shares conversation
execution, fixture context and native `deepeval.evaluate` reporting. This also
supports our graph's human-interrupt/resume steps, which the reference's single
`agent.invoke` loop does not supply. No Matrace collection is introduced.

## Validation and next run

Controlled tests exercise all 25 supported cases, actual fixture tools and
checkpoints, native DeepEval metrics, the redundant public-policy question,
incomplete-answer reporting, ownership denial and revised explanations.
Controlled actor/judge responses test integration; they are not improved live
Ollama scores. The cloud machine has no working Ollama service, so a live
comparison remains necessary locally.

From the repository root, with Ollama running and model tags installed:

```bash
uv sync --frozen
source .venv/bin/activate
# Inspect the changed paths first:
python Evaluations/focus.py --case V2-05 --case V2-10 --case V2-15 --case V2-17
# Then run each of the 25 tasks once, scoring both focused metrics:
python Evaluations/focus.py --model llama3.1:8b
```

Save the new terminal output separately, for example as `2_Evaluation.md`.
Manually inspect V2-05 routing and the earlier identity/item clarifications;
inspect any alleged refund approval against the exact customer-facing text.
Use those reviewed judgments to choose between the installed 8B and 4B judges.
These 25 tasks are now a development set; use new paraphrases/boundary cases
for a later held-out evaluation before claiming general improvements.
