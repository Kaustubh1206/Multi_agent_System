# Seven-metric retail evaluation

This workflow follows the seven metric choices in
[Agentic_Customer_Evaluation](https://github.com/Kaustubh1206/Agentic_Customer_Evaluation),
inspected at commit `8e41729036ec6620cb171f0b919cd8c141d499f2`.
It uses this repository's current `evaluation/retail-v2/` fixtures and scenarios,
not the other repository's earlier refund dataset.

| Metric | Evidence | Threshold |
|---|---|---|
| Task Completion | Original task, conversation, observed result, trace | 0.8 |
| Step Efficiency | Executed model/tool steps and human pauses | 0.8 |
| Plan Quality | Supervisor's explicit public plan | 0.8 |
| Plan Adherence | That plan compared with actual execution | 0.8 |
| Argument Correctness | Observed tool arguments and results | 0.8 |
| Tool Correctness | Independent expected tool sequence vs actual calls | 0.9 |
| Answer Correctness | GEval: expected requirements, policy, authorized fixture context | 0.8 |

These are the same seven *metrics* as the reference, not the seven broader
research dimensions in the earlier discussion. Additional hard checks verify
structured outcomes, retained order/customer IDs, private tool scope, consumed
scripted replies, trace presence, and unchanged fixture data. A favorable judge
cannot override a failed hard check. Correct denials, clarification questions,
and required human review can all be successful task outcomes.

## Two environments, one execution

DeepEval 4.3.4 and the MAS have conflicting Click dependency ranges. The evaluator
has its own `pyproject.toml`, `uv.lock`, and `.venv`; the MAS dependencies and root
lockfile are unchanged. This also keeps DeepEval optional for normal app use.

From the repository root, install both environments:

```bash
uv sync --frozen
uv sync --project evaluation/deepeval --frozen
```

Start Ollama if it is not already running and install the actor and judge models:

```bash
ollama serve
# In another terminal:
ollama pull qwen3:4b
ollama pull qwen3:0.6b
ollama pull qwen3:8b
```

The independent default judge is `qwen3:8b`. It uses a 32768-token context and can
need substantial memory. Select another suitable judge with `--judge-model` if
necessary. Judge scores must be calibrated against human-reviewed examples;
a local judge is not an unquestionable ground truth.

## Collect a run

```bash
uv run --frozen python -m evaluation.deepeval.collect \
  --case V2-01 --case V2-16 \
  --output evaluation/deepeval/reports/smoke.json
```

Run all current scenarios, or repeat them to measure variability:

```bash
uv run --frozen python -m evaluation.deepeval.collect \
  --repeats 3 --output evaluation/deepeval/reports/baseline.json
```

Use `--list` to see capabilities without contacting Ollama. A full run selects
30 scenarios: 25 read-only cases execute, and 5 mutation/recovery cases are
explicitly skipped. Each repetition gets a fresh graph, checkpointer, and thread.
Scripted human replies are consumed only when the graph pauses, on the same
thread. Unused replies and unresolved unexpected pauses remain visible as errors
in the result checks. An expected final clarification is retained and evaluated.

`collect.py` passes only human messages and trusted session context to the MAS.
Gold answers, reference fixture context, and expected tools are evaluator-only.
The scenario's `context.customer_id` is mapped to trusted `verified_user_id`;
when absent, identification must happen through the conversation.

Every execution is recorded once. Native LangChain callbacks capture model,
chain, and directly invoked tool spans with parent IDs. LangGraph updates capture
executed decisions and public plans. The state's `tool_history` is the source
for actual tool calls; these calls do not necessarily appear in AI messages.
Expected tools are independently annotated in `expected_tools.json`, not derived
from what the agent happened to do. Tool Correctness checks exact names/order;
Argument Correctness and hard scope checks cover arguments separately.

Exports retain policy/scenario snapshots, authorized reference context, expected
tools, fixture/code hashes, commit/dirty status, model names, and actual traces.
They contain customer data and are ignored by Git. Local capture requires no
LangSmith or Confident AI account. LangSmith remains opt-in via your environment.

## Score that same execution

```bash
uv run --project evaluation/deepeval --frozen python -m evaluation.deepeval.score \
  evaluation/deepeval/reports/smoke.json
```

The scorer never reruns the MAS. It writes `smoke.scores.json`, with each metric's
score, threshold, reason and status; hard-check results; per-metric means and
explicit denominators; blocked, skipped, failed, and incomplete counts.
Scores and source traces are separate files. Do not edit a trace to improve a
score. Preserve the original baseline before changing prompts or code.

Use a different local judge:

```bash
uv run --project evaluation/deepeval --frozen python -m evaluation.deepeval.score \
  evaluation/deepeval/reports/smoke.json --judge-model YOUR_OLLAMA_MODEL
```

An optional hosted judge requires a securely exported `OPENAI_API_KEY`:

```bash
uv run --project evaluation/deepeval --frozen python -m evaluation.deepeval.score \
  evaluation/deepeval/reports/smoke.json \
  --judge-provider openai --judge-model YOUR_SUPPORTED_OPENAI_MODEL
```

That explicitly selected option sends evaluation material to the judge provider.
The default local workflow needs no hosted API key. No secret belongs in source,
reports, command arguments, or chat.

## Honest coverage

- No emitted public plan means Plan Quality and Plan Adherence are **not
  applicable**, not perfect scores. The case cannot claim a complete seven-metric
  pass. This guards DeepEval's native no-plan perfect-score fallback.
- Missing models, actor errors, unavailable judges, malformed judge output,
  unsupported actions, and missing evidence never become passing scores.
- Only scored results enter metric means; their counts are reported. A run with
  zero scored cases exits unsuccessfully.
- A policy denial is distinct from a technical failure. A state field saying
  completed does not mean a cancellation/refund was performed.
- Plan metrics measure the explicit observable plan, not hidden chain-of-thought.
  System-level correctness includes deterministic checks inside the order node;
  it is not proof the small model independently reasoned through every policy.
- Trace metrics use a small saved-trace bridge to DeepEval 4.3.4's `_trace_dict`.
  The tested version is pinned; run compatibility tests before upgrading. Tool/LLM
  events and node observations retain chronology and recorded parent IDs; no
  invented causal dependency map is introduced here.
- Metrics are enabled together, but missing evidence or judge errors can prevent
  seven valid scores for a case. Reports distinguish those outcomes.

## Validation and current evidence

```bash
# MAS, v2 policy, collector, privacy/continuation checks:
uv run --frozen python -m unittest discover -s tests -v

# Native DeepEval compatibility and error-path tests in its own environment:
uv run --project evaluation/deepeval --frozen python -m unittest discover \
  -s evaluation/deepeval/tests -v
```

Tests use controlled actors/judges and are not benchmark scores. Cloud live
collection was attempted: actor preflight failed because no Ollama service is
running; the model registry request returned proxy HTTP 403. The report recorded
25 blocked cases, 5 skipped cases, and zero scores. Run the commands locally to
obtain a live baseline; no live performance result is claimed here.
