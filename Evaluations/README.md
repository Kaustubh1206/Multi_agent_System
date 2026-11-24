# Run the MAS and DeepEval directly

This follows the layout of
[Agentic_Customer_Evaluation](https://github.com/Kaustubh1206/Agentic_Customer_Evaluation):
`dataset.py`, `model.py`, one Python script per metric, and `eval_all.py`.
There is one shared root `.venv`, one root `pyproject.toml`, and one root lockfile.

## Setup once

From the repository root:

```bash
uv sync --frozen
source .venv/bin/activate
```

The unused Hugging Face dependencies were removed because their Click version
requirements conflicted with DeepEval. The Ollama MAS does not import those
packages. Other application dependencies remain in the root project.

Start Ollama if it is not running, then make sure these model tags are installed:

```bash
ollama serve
# In a second terminal, if needed:
ollama pull qwen3:4b
ollama pull qwen3:0.6b
ollama pull llama3.1:8b
```

## Run one parameter

From the repository root with `.venv` activated:

```bash
python Evaluations/task_completion.py
python Evaluations/step_efficiency.py
python Evaluations/plan_quality.py
python Evaluations/plan_adherence.py
python Evaluations/argument_correctness.py
python Evaluations/tool_correctness.py
python Evaluations/answer_correctness.py
```

Or enter the folder and run a script like the reference repository:

```bash
cd Evaluations
python task_completion.py
```

Each command runs the actual MAS on the selected tasks, supplies scripted human
replies on the same interrupted conversation, and calls native `deepeval.evaluate`.
The terminal shows case progress and actual answers during execution, then
DeepEval progress bars, scores, reasons, and per-case results. Judge evaluation
runs serially to avoid overwhelming a local Ollama server.

## Run all seven on one set of responses

```bash
python Evaluations/eval_all.py
```

This executes each MAS task once and evaluates those responses on all seven
metrics. It does not execute the MAS seven times per task. Running seven separate
scripts does create seven fresh sets of MAS responses, one per command.

For the current Answer Correctness / Task Completion work, run both on the same
set of responses:

```bash
python Evaluations/focus.py --model llama3.1:8b
# A smaller first run:
python Evaluations/focus.py --case V2-05 --case V2-10 --case V2-15 --case V2-17
```

| Script | DeepEval metric | Threshold |
|---|---|---|
| `task_completion.py` | Task Completion | 0.8 |
| `step_efficiency.py` | Step Efficiency | 0.8 |
| `plan_quality.py` | Plan Quality | 0.8 |
| `plan_adherence.py` | Plan Adherence | 0.8 |
| `argument_correctness.py` | Argument Correctness | 0.8 |
| `tool_correctness.py` | Tool Correctness | 0.9 |
| `answer_correctness.py` | Answer Correctness (GEval) | 0.8 |

## Where the evaluation code lives

The reference repository repeats the application loop and test-case setup in
individual metric scripts. Here, `runner.py` shares that work, including scripted
human replies and the graph's interrupt/resume handling. The short per-metric
entrypoints call that runner; they are not empty metric implementations.

`answer_correctness.py` now contains its GEval rubric and constructor, and
`task_completion.py` contains its case-scoped native Task Completion definition.
Edit those files to tune these two metrics. The other five constructors remain
in `metrics.py`, with their own direct command entrypoints. Every run still uses
native `deepeval.evaluate` and its terminal reporting.

The protocol is **`answer-task-v2`**, with MAS prompts **`retail-v8`**. Answer
Correctness grades only `customer_message`, like the reference repo's extraction
of `structured_response.answer`. It no longer grades serialized internal result
fields as customer statements. Reference answers describe final replies; earlier
identification/clarification steps are checked through the observed conversation
and case-specific Task Completion requirements. Full structured results remain
in test-case metadata. Policy and fixed-date authorized fixture facts ground the
judge; gold answers are never sent to the MAS.

Thresholds and business requirements are unchanged. The evaluator representation
and task scope changed, so new scores are not directly comparable to the old
report. Keep [1_Evaluation.md](1_Evaluation.md) as the original and read the
[25-case analysis](1_Evaluation_analysis.md) for findings and limits. Save new
output separately. A better judge score alone does not establish better behavior.

## Tasks and expected outcomes

Edit `TASK_DEFINITIONS` in `dataset.py`: each entry visibly declares the initial
request, follow-up replies and expected answer, like the reference's Golden list.
`scenario_id` supplies trusted context and structured outcome checks from the
current `evaluation/retail-v2/scenarios.json`. If changing the intended outcome
rather than just the wording, update that scenario's structured checks as well.

The default dataset contains 25 supported read-only cases: identity, duplicate
names, missing information, status, split shipments, policy questions, category
windows, memberships, damage review, ownership, and input corrections. Five
mutation/fault-recovery targets are listed as skipped; the current MAS does not
implement them. The dataset uses the latest retail-v2 fixture and its fixed clock,
not the machine date. Expected answers are evaluator-only and never given to the
MAS as human input. Only trusted session identity is passed from task context.

Start with one case or a small subset:

```bash
python Evaluations/task_completion.py --case V2-16
python Evaluations/eval_all.py --limit 3
python Evaluations/answer_correctness.py --list
```

## Judge model

`model.py` defaults to **llama3.1:8b**, matching the user's installed model. Change
`eval_model` there, export `EVAL_MODEL`, or select another installed Ollama tag:

```bash
python Evaluations/eval_all.py --model llama3.1:8b
python Evaluations/eval_all.py --model qwen3:4b
```

The MAS still uses qwen3:4b for the supervisor and qwen3:0.6b for specialists.
The judge is separate in purpose, not in Python environment. Llama 8b is a starting
judge choice, not a measured winner: compare judgments with human-reviewed cases
before deciding which works better. No hosted API key is needed. `OLLAMA_HOST`
can configure a non-default server; `.env` is loaded before the app is imported.

## No collection/scoring workflow

There is no `collect.py` command, `score.py` command, baseline JSON prerequisite,
or separate evaluator environment. Temporary model/tool/node context is supplied
automatically in memory because DeepEval's agentic metrics need execution evidence.
No custom trace corpus or Matrace folders are produced. DeepEval may maintain its
normal local `.deepeval` result metadata; the old saved reports are not used.

The version-pinned 4.3.4 adapter supplies observed in-memory context to agentic
metrics. Plan metrics read the actual public supervisor plan, never an invented
plan or private chain-of-thought. No plan means NOT APPLICABLE, not an automatic
perfect score. Missing tool arguments likewise do not earn a vacuous argument
correctness score.

Deterministic checks also verify expected status/reason, ownership, context
retention, consumed human replies, and unchanged fixture data. A judge pass does
not override failed checks. Review required is not review submitted or refund
approved. The scripts exit unsuccessfully on failed checks, incomplete metric
coverage, model/judge failures, or a zero-case run.

## Validation

```bash
python -m unittest discover -s tests -v
```

Tests exercise real graphs, current fixture tools, all 25 supported cases,
all seven native metrics and native `evaluate` output with controlled actors and
judge responses. They are integration tests, not live model benchmark scores.
This cloud machine has no Ollama service and the registry is blocked, so run the
commands locally to obtain actual model scores.
