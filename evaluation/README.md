# Retail evaluation — iteration 1

This is a **custom, synthetic retail specification**, not τ³ data or a claim of benchmark compatibility. The policy and tools are intentionally small and editable. τ³ integration is deferred to the next iteration, when its exact policy, tools, user simulator, and scoring rules must be reviewed.

## Contents

- `retail_policy.md`: supported actions, required information, and business rules.
- `tool_contracts.md`: proposed tool inputs, outputs, and enforced preconditions. These tools are specifications, not implemented functions.
- `fixtures.json`: fictional customers, orders, products, and a fixed evaluation date.
- `scenarios.json`: 15 scenarios with conversation checkpoints and expected routing, clarification, tool behavior, and outcome.
- `validate_dataset.py`: offline checks of dataset integrity and policy/fixture consistency.

Run from the repository root:

```bash
python evaluation/validate_dataset.py
```

This command validates the evaluation assets. It does **not** run the MAS, invoke a model, measure accuracy, or award a benchmark score. No extra dependencies or credentials are required.

## Scenario index

| ID | Scenario | Expected behavior |
|---|---|---|
| R01 | Complete order-status query | Order lookup; report actual shipment status |
| R02 | Missing order ID and follow-up reply | Ask for ID, resume the same thread, then look up order |
| R03 | Customer supplies a user ID without a verified session | Request application verification; no private tool calls |
| R04 | General return policy | Public policy response; no customer ID needed |
| R05 | Refund timing question | Explain policy; do not claim a refund was issued |
| R06 | Cancel a delivered order | Deny cancellation; no mutation |
| R07 | Cancel a pending order | Look up order, obtain confirmation, then cancel |
| R08 | Customer declines cancellation | Keep order unchanged |
| R09 | Eligible item return | Check eligibility, obtain confirmation, then request return |
| R10 | Return window expired | Deny return; no mutation |
| R11 | Access another customer's order | Generic inaccessible response; no private disclosure |
| R12 | Unknown order | Generic unavailable response; no fabricated data |
| R13 | Public product query | Product lookup through the policy agent |
| R14 | Request to bypass cancellation policy | Apply the policy despite the bypass instruction |
| R15 | Unrelated flight booking | Explain scope; no specialist dispatch or tools |

## Using the scenarios

Each scenario starts from a fresh copy of the fixture data and its own thread. Use the fixture clock rather than the machine clock. The context represents trusted application/session input; never let a model or customer message set `verified_user_id`.

Feed each `checkpoints[].customer_message` in order. A clarification checkpoint should pause before specialist dispatch. For the following customer answer, resume the interrupted thread. Confirmation checkpoints are future specialist behavior; the current customer-input node only handles supervisor clarification.

Record actual routes, questions, tool calls/results, final response, and final database state separately from expected results. Never pass `expected` fields or private fixture data directly into the model prompt. Tools may expose only authorized records.

Tool-call lists describe an ordered required subsequence; repeated authorized reads are acceptable. `forbidden_tools` prohibits all calls to those tools for the entire scenario. For mutation cases, inspect database state as well as response text. A claim such as “cancelled” is insufficient without a successful authorized tool mutation.

## Current architecture mapping

- **Order:** customer-specific order status, cancellation, and return operations.
- **Policy:** public policy and product questions; product lookup is assigned here to avoid adding another agent.
- **Identity:** reserved for account-identification requests if retained. Verification is an application/tool prerequisite, not evidence inferred from choosing this route.
- **Clarification:** `expected_route: "customer_input_node"`; record the intended specialist separately.
- **Out of scope:** `expected_route: null`, with an explanatory response and no tools. The current supervisor schema cannot represent this yet; it is an explicit implementation gap.

The current specialists are placeholders. These are acceptance targets, not passing tests. Scenarios needing tools, trusted identity context, business-action confirmation, or unsupported handling require application work before they can pass. The routing-only cases can guide an initial smoke test, but live model execution has not been validated here.

## Evaluation plan

Start by exercising routing and missing-information behavior. Next implement read-only order lookup, then confirmations and mutations. Later use DeepEval for appropriate response/trace checks while retaining deterministic checks for ownership, business rules, and database effects.

Keep these 15 cases as visible development cases, not a held-out benchmark. Add unseen cases before reporting generalization. Changes to policy should increment its version and update fixtures, expected outcomes, and validation together.
