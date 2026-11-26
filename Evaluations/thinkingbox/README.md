# ThinkingBox-Bench with the MAS

This branch evaluates **all 98 retail and ecommerce tasks** from ThinkingBox-Bench
v1.0 using the retail supervisor/identity/order/policy MAS and DeepEval **Task
Completion** and **Answer Correctness**. Running without `--limit` executes all
98 retail tasks. Other domains cannot be selected through `--case` or the task
loader, and only the retail MCP server is enabled.

The upstream release contains 507 tasks across five domains. The full pinned
canonical task list is retained for provenance; this runner selects only its
`sandbox_external_retail_group1.py:` entries. It preserves the original retail
task queries, fixtures, policies, tools, user simulator protocol and native checks.

## Run locally

Use Python 3.12+, Git, and Ollama. Everything uses the existing **root `.venv`**;
there is no nested evaluation project. From the repository root:

```bash
git fetch origin
git switch ThinkingBox
git pull --ff-only
uv sync --frozen --extra thinkingbox
source .venv/bin/activate

ollama pull qwen3:4b
ollama pull qwen3:0.6b
ollama pull llama3.1:8b
```

Start Ollama if it is not already running (`ollama serve` in another terminal).
Then validate the source and run **all 98 retail tasks with both metrics**:

```bash
python Evaluations/thinkingbox/run.py --audit
python Evaluations/thinkingbox/run.py
```

The first audit downloads the pinned data checkout into `.cache/thinkingbox-data`.
The run downloads Typesense **30.1** using the architecture-specific checksum
from the pinned framework installer. Linux x86/ARM and macOS Intel/Apple Silicon
downloads are supported. The native macOS binary is selected on a Mac Mini;
Docker is not required. macOS execution has not been tested in this Linux cloud.

The runner starts its own Typesense and source MCP session proxy on loopback
ports **8108 and 7111** (plus Typesense peering port **8107**), and stops the processes it started on exit. Those ports
must be free; it leaves existing services alone. Typesense may download its
`ts/e5-small` retrieval model on first use, so allow that download as well as the
Typesense binary download. Every task receives fresh native sandbox state.
Only `sandbox_external_retail` is enabled.

Run a small connectivity check first if desired:

```bash
python Evaluations/thinkingbox/run.py --limit 1
python Evaluations/thinkingbox/run.py --list
```

For separate metric scripts, each executes the selected tasks and native checks:

```bash
python Evaluations/thinkingbox/task_completion.py
python Evaluations/thinkingbox/answer_correctness.py
```

Use `run.py` to score both metrics on the **same conversations** instead of
executing tasks twice. Both use threshold **0.8** and native DeepEval metrics:
`TaskCompletionMetric` and `GEval`. Each metric definition is in its own file.
The existing 25-task retail suite remains under `Evaluations/` with its data
and reports unchanged.

The first recorded retail run is reviewed in
[Results_Thinking_analysis.md](Results_Thinking_analysis.md), including all 98
case scores and the confirmed zero-tool first case. It prompted protocol
`thinkingbox-retail-mas-v3`; keep its reruns separate from the original output.

## Models and the agent being tested

Defaults: supervisor `qwen3:4b`, specialists `qwen3:0.6b`, user simulator and
judge `llama3.1:8b`. The 8B judge is a configurable starting point, not a claim
of measured superiority. To use the 4B judge or an 8B specialist:

```bash
python Evaluations/thinkingbox/run.py --model qwen3:4b
python Evaluations/thinkingbox/run.py --specialist-model llama3.1:8b
```

The ThinkingBox adapter in `mas.py` uses a LangGraph supervisor and the familiar
identity/order/policy specialist structure. The supervisor routes retail identity,
order and policy work. All specialists have the retail source scenario's tools;
roles guide responsibility without hiding a tool needed for a valid workflow.
Source instructions control authentication, confirmation and ticket lifecycle.
The source `think` agent's termination/builtin-tool protocol is preserved.
This tests the **ThinkingBox MAS adapter**, not the unchanged read-only retail
application: that application cannot perform the benchmark's transactions.

The supervisor returns only `{agent, next_step}`. State consists of observed
conversation messages, that route, and the next completion. Tools execute in
the native ThinkingBox agent session, one at a time. One malformed-argument
or premature/empty-response repair is allowed before execution; successful writes are never replayed by
the adapter. No task gold answers, initial database patches, reference writes
or private simulator context are passed to the MAS. The simulator gets only
its source user context and the conversation, as upstream defines.

The router receives a classification request containing the source policy, tool
names and observed history as labeled data. Model requests use one leading system
message containing source and node instructions. A final `<DONE>` response must
contain substantive text and must not ask for missing information or accompany
an unexecuted tool request. A final resolution before any tool result also
triggers the bounded repair, because this source requires a ticket workflow.
Persistent invalid output is recorded as an error; the adapter does not invent
a replacement answer. Repair reasons and rejected proposals are retained in
the routing metadata, outside the customer conversation.

`--num-ctx` defaults to 32768 for every local model call. Increase it if a case
exhausts context. Large source policies, many tools and long conversations need
substantial memory and may exceed small models' effective capacity. Near-limit
input and output-limit errors are recorded. Ollama's context behavior and local
simulator/judge quality can affect results; inspect individual cases rather than
treating local scores as released leaderboard numbers.

## Results and interpretation

Results go to a new `evaluation/thinkingbox/runs/<UTC timestamp>/` directory:

- `manifest.json`: exact source/model/protocol configuration and selected IDs.
- `NNN.native.json`: source conversation, actual tool results, effects and checks.
- `results.json`: native result, routes/repairs, actual tool names/count, final
  customer answer, both metric scores/reasons, errors. Console case summaries
  also show this execution evidence.
- `summary.json`: requested/recorded counts, native results and DeepEval means.

Files are saved after each task. Existing result directories are never overwritten.
These are normal evaluation artifacts; no separate trace collection service is
required. The first implementation runs sequentially without automatic resume.

Native executable assertions and database end-state checks remain authoritative
for native task success. DeepEval adds Task Completion and Answer Correctness;
it cannot turn a failing native check into a pass. A combined pass requires native
checks, normal source termination, and the selected DeepEval metrics to pass.
The summary keeps native success and LLM scores separate. Blocked runs, missing
answers, simulator errors, native test errors and judge errors never receive
automatic passing scores. Missing scores are `null`, not zero or perfect.

Answer Correctness sees only the final customer-visible answer as actual output.
Its trailing `<DONE>` control marker is removed in the evaluator representation;
the native conversation and native checks retain it. An empty substantive answer
receives zero correctness with `method: empty_answer_check`, a deterministic
prerequisite result rather than a generated GEval score. Other answers use GEval.
The summary's metric `methods` counts distinguish these checks from DeepEval calls.
Earlier clarification and operations are evidence, so a short final answer need
not repeat every earlier step. Source reference writes describe expected backend
outcomes, **not a required read/tool sequence or verbatim answer**. The judge
also receives the applicable policy and observed tool results; unsupported claims
of successful operations remain errors. Reference material is evaluator-only.
Task Completion's outcome extractor sees actual conversation and operations;
reference database differences, golden hashes and policy instructions are not
included as observed actions. Policy/reference goals are supplied separately as
requirements. Native effects remain available in the full native artifacts.

Protocol v3 changes actor and evaluator inputs. New scores alone cannot establish
an improvement over v2: inspect requested/scored counts, errors, actual tool use
and native checks as well. An 8B specialist comparison is available through
`--specialist-model llama3.1:8b`; the default remains 0.6B pending a live comparison.

This is a custom MAS/local-simulator/local-judge evaluation,
**not a leaderboard-comparable score**. Benchmark tasks are used for evaluation;
the adapter contains no task-specific golden examples or optimized answers.

## Pinned sources and license

The user's [Microsoft article](https://commandline.microsoft.com/thinkingbox-bench-agent-benchmarking/)
identifies ThinkingBox-Bench. The official data repository URL returned 404
during setup. This implementation therefore uses a **public mirror**, pinned
and disclosed in every result manifest:

- Framework: `microsoft/thinkingbox` at `ef2546974a286afa3f189dc711d31bc5aa6d1f39`.
- Data: `MrSergSanch/thinkingbox-data` at `4a4a3a28c395af51f2c77ab72805d0ee0f94b539`.
- Canonical list: `releases/thinkingbox_bench_v1/testlist_thinkingbox_bench_v1.yaml`.

The mirror is labeled v1.0 by its contents; equivalence to an official release
tag has **not** been independently verified. `source/manifest.json` records the
actual fetch URLs, commits, task counts and canonical-list checksum. A cached
checkout must remain clean and at the pinned commit; it is never reset silently.
The canonical list is redistributed alongside the source's full
[license](source/LICENSE.txt): data is CDLA-Permissive-2.0; server code is MIT.

The cloud validation can hydrate all 98 retail tasks without Ollama. Live MAS and
DeepEval scores require the local models; a successful audit alone is not a
completed benchmark run.
