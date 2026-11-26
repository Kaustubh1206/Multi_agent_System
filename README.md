# Retail support — version 2

The default runtime now reads **`evaluation/retail-v2/`**: 20 fictional customers,
30 orders, and policy `retail-v2.0`, evaluated at the fixture date 2026-11-15.
It does not read `evaluation/simple-retail/`. That smaller dataset and the
`simple-retail-version-1` branch remain as the original baseline.

## Run locally

With Python 3.12 and uv:

```bash
uv sync --frozen
ollama pull qwen3:4b
ollama pull qwen3:0.6b
```

Start `ollama serve` if the local service is not already running. Direct Python
callers must use `build_graph(InMemorySaver())` and a unique thread ID per
conversation. The LangGraph development server supplies its own checkpointing.
If you created `run_local.py` using the earlier terminal example, that launcher
still works: update this branch, restart the process, and start a fresh thread.
LangSmith is optional for direct execution; the earlier launcher disables remote
tracing and still retains state histories during the process.

Example conversation after updating:

```text
You: The tea gift box in ORD-2013 arrived damaged. Can I get a refund?
Agent: Please provide your full name or email address.
You: priya.nair@example.com
Agent: [Damage was reported within 7 days; human review is required.
        No review case, return, or refund has been submitted or approved.]
```

Priya is CUS-213. Her order is nested under her v2 customer record. The final
structured status is `needs_review`, reason `damage_review_required`. This is
an explanation of the review requirement, not a handoff to a real review service.

## Files

| File | Responsibility |
|---|---|
| `src/prompts.py` | Prompts for supervisor, identity, order, and policy agents |
| `src/models.py` | Supervisor qwen3:4b; specialists qwen3:0.6b |
| `src/objects.py` | Structured model outputs and common agent result |
| `src/state.py` | Shared state and specialist substates |
| `src/nodes.py` | Agent logic, order policy checks, human interrupts, item/condition input |
| `src/graphs.py` | Routing and checkpoint-aware continuation |
| `src/tools.py` | Active dataset, authorized lookups, policy retrieval |

## Supported behavior

- **Identity:** unique full-name or email matching, ignoring case and surrounding
  whitespace. Duplicate names require email. Explicit email replies do not depend
  on model extraction. Corrections replace stale inputs. A typed customer ID is
  not proof of identity. This is prototype identification, not production login.
- **Ownership:** order tools search only the identified customer's orders,
  including their second or later order. Unknown and inaccessible orders get the
  same generic unavailable response. Optional `verified_user_id` is trusted
  application input; otherwise lookup sets `identity_state.customer_id`.
- **Status:** order/item and split-shipment facts, actual delivery dates, and
  recorded estimates labeled as estimates. No invented shipping updates.
- **Returns:** clarify item/quantity when ambiguous; check captured payment,
  delivered units, previous/pending returns, category windows, membership at
  purchase, final sale, condition, packaging, and hygiene seals. Calculate paid
  merchandise value after discounts without claiming an approved refund.
- **Damage:** reports within seven days require review, including perishable and
  final-sale items. Late damage reports do not automatically get an exception.
- **Cancellation:** check pending status and explain eligibility only.
- **Policy:** public returns, refunds, cancellation, shipping, and identification
  rules, retrieved from the same versioned policy as the active dataset.

The supervisor saves the original request, a short public action plan, and its
operation before collecting identity. The plan is observable intended actions,
not private reasoning. Prompts contain a few invented teaching examples; gold
evaluation scenarios and expected answers are never loaded into MAS prompts.
Order calculations and eligibility checks live beside the order node in
`src/nodes.py`; `src/tools.py` retrieves records and policy text.
Model-proposed and executed topics are recorded separately. Names,
emails, and clarification answers cannot silently change a status request into a
return request. The original order/item selectors and later corrections remain
in conversation state across human interruptions.

Any missing required input returns `needs_information` and pauses in the
supervisor. Resume with `Command(resume="the human reply")` using the same thread.
The saved specialist then continues without another supervisor classification.
`InMemorySaver` is process-local; persistent storage is needed across restarts.

## Remaining limits

The application is **read-only**. It does not implement action confirmations,
mutations, review-case submission, refund payments, automatic retries, or
idempotency/reconciliation. Do not expect cancellation/return submission or
injected-recovery acceptance cases to complete. Public policy can describe these
requirements without claiming the actions are implemented.

Explicit English item IDs, unique full product names, numeric quantities, and
simple condition answers are supported. Ambiguous or missing data requires
clarification. Return decisions use the fixed fixture clock, not today's host
clock. Dataset records are never changed by these tools.

## Traces and evaluation

- `messages`: customer inputs, questions, human replies, and agent responses.
- `supervisor_history`: classified tasks, a public action plan, and `human_resume` continuation events.
- `agent_history`: structured outcomes and failure categories.
- `tool_history`: evidence IDs, arguments, results/errors, timing.
- `order_state`: executed/model topics, authorized order, and eligibility result.
- `graph.get_state_history(config)`: snapshots across checkpoints and interruptions.

Model runs carry prompt version `retail-v7`. Tool run metadata links to state
through evidence IDs. Attach LangChain callbacks or enable LangSmith tracing
before execution for full model inputs/outputs, exceptions, run relationships,
and usage metadata when supplied by Ollama. Credentials belong in secure
settings. State summaries are not complete model traces. No Matrace adapter is implemented yet. The DeepEval workflow is documented in
[Evaluations/README.md](Evaluations/README.md).

## Dataset and tests

The older `evaluation/retail-scenarios-v1` branch was used only as a reference;
its files were not imported. V2 uses the three files `customers.json`, `policy.md`,
and `scenarios.json`. The scenarios describe expected outcomes, not model scores.

`implementation_status` distinguishes `read_only_supported`, `requires_mutations`,
and `requires_recovery`. All cases remain `execution_status: not_run` for live
model evaluation. Mutation and fault-injection expectations remain future targets.
Never feed expected outcomes, private fixture records, or injected failures into
model prompts. Give every scenario fresh fixture state and a new thread.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover baseline compatibility, v2 integrity, and v2 runtime behavior. Runtime
tests use real data, tools, graphs, and checkpoints with controlled model outputs;
they do not establish live Ollama accuracy. The old baseline tests explicitly
select `simple-retail` so passing them cannot hide a default-dataset mismatch.

## Run DeepEval directly

All application and evaluation dependencies now use the root environment:

```bash
uv sync --frozen
source .venv/bin/activate
python Evaluations/task_completion.py
# Focus on Task Completion and Answer Correctness, using the same MAS responses:
python Evaluations/focus.py
# Or run all seven metrics on each MAS response:
python Evaluations/eval_all.py
```

The `Evaluations/` folder follows the reference repository: `dataset.py` contains
25 explicit task inputs and expected answers from the latest retail-v2 scenarios,
`model.py` configures the Ollama judge, and there is a separate Python entrypoint
for each of seven parameters. Every script runs the MAS and shows native DeepEval
progress and results. No saved baseline, trace collection command, separate scoring
command, or nested evaluation environment is needed.

The default judge is `llama3.1:8b`. Select the supervisor-sized judge with
`python Evaluations/eval_all.py --model qwen3:4b`. Judge quality has not been
benchmarked here; inspect reasons against human-reviewed cases. With Ollama
running, `--case V2-16` or `--limit 3` provides a small first run.

The parameters are task completion, step efficiency, plan quality, plan adherence,
argument correctness, tool correctness, and answer correctness. Five unimplemented
action/recovery cases remain explicitly skipped. Missing plans and judge failures
cannot become automatic passes. See [Evaluations/README.md](Evaluations/README.md)
for individual scripts, setup and evidence limits.

The [first 25-case review](Evaluations/1_Evaluation_analysis.md) separates missing
or unclear answers from judge/input problems. The revised `answer-task-v2`
protocol scores customer-visible text and case-specific workflow requirements;
keep its results separate from the original report. The two focused metric
definitions live in their respective scripts; shared execution remains in
`Evaluations/runner.py`.

Branch `ThinkingBox` evaluates all 507 ThinkingBox-Bench v1.0 tasks with a
supervisor/specialist MAS, source tools and policies, native checks, and DeepEval
Task Completion and Answer Correctness. See
[ThinkingBox setup and commands](Evaluations/thinkingbox/README.md).
