# Native τ-bench retail MAS

Branch: `evaluation/tau-retail-native`. This is a separate runtime from the
read-only application graph and the adapted probes. It uses the original retail
tasks, upstream simulator, source policy and transactional tools. No native task
is rewritten as a status question.

Upstream is pinned to Sierra's
[v2.0.0 commit](https://github.com/sierra-research/tau2-bench/tree/989d3285ae669e49503324bdb9981bf14fccedeb).
The root lockfile pins the Python package to that same Git commit. Data setup
creates a separate sparse checkout at `.cache/tau2-native/`; it checks the
revision, clean tracked files and retail snapshot hashes before a run.
Existing application customers, policies and evaluation reports are untouched.

## Install and smoke-test

Use Python **3.12 or 3.13**. Native dependencies are an optional extra in the
same root environment, not a nested environment:

```bash
uv sync --frozen --extra tau-native
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --setup
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --dry-run
```

Setup requires Git and network access once. The checkout is cached and ignored
by Git. An existing checkout is verified rather than reset. For an existing clean
pinned checkout, use `--data-dir /path/to/tau2-bench/data` or set
`TAU2_NATIVE_DATA_DIR`. No voice/audio dependencies are installed.

Start Ollama, install the same MAS tags plus the simulator/judge tag, then run
one native task:

```bash
ollama pull qwen3:4b
ollama pull qwen3:0.6b
ollama pull llama3.1:8b
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --task-id 69
```

The default command without selection also runs one task. Test a few or all:

```bash
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --limit 3
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --all --dry-run
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --all
```

The pinned English runtime exposes **114 original tasks**, with upstream's 2.0
reviewed replacements applied through its language binding. `--all` uses that
population; this command does not claim to run the released 50-task multilingual
text benchmark manifest. Inspect `--dry-run` for selected IDs, exact models and
artifact pins. `--trials 3` repeats each selected task with a new fresh environment
and seed for each trial. It does not reuse the mutated database from another task.

## Models and roles

| Component | Default | Responsibility |
|---|---|---|
| Supervisor | `qwen3:4b` | Route requests; retain original intent and public plan |
| Identity specialist | `qwen3:0.6b` | Extract email or name plus zip; use native lookup tools |
| Order specialist | `qwen3:0.6b` | Read facts, choose variants, propose source tool calls |
| Policy specialist | `qwen3:0.6b` | Answer from the supplied source policy |
| Upstream user simulator | `ollama_chat/llama3.1:8b` | Follow the task's private customer scenario |
| Native assertion/communication judges | `ollama_chat/llama3.1:8b` | Judge required assertions using upstream evaluator prompts |

The MAS still uses two model sizes. The user simulator and judge are benchmark
components, not additional MAS specialists. All defaults run locally without a
hosted API key. `OLLAMA_HOST`, `.env`, or `--ollama-url` sets the server.

The small specialist now has a harder job than topic classification. To compare
it with an installed 8B model, use:

```bash
uv run --frozen --extra tau-native python Evaluations/tau_native/run.py --task-id 69 --specialist-model llama3.1:8b
```

No winner has been measured. `--num-ctx` defaults to 16384; `--max-steps` defaults
to 100. `--supervisor-model`, `--user-model` and `--judge-model` are recorded in
run metadata. User/judge local tags use the `ollama_chat/` provider prefix.
`--judge-model upstream` restores the pinned upstream assertion and communication
judges, which require their provider credentials. That option keeps the local
user simulator; it alone does not reproduce the released benchmark configuration.

## Runtime behavior

`src/tau_native/` contains small role prompts, structured state and a LangGraph
with supervisor/identity/order/policy nodes. It implements the native
`HalfDuplexAgent` protocol. There is no customer-input node: the upstream user
simulator supplies customer turns, including clarification and confirmation.

The agent factory receives tools and public policy. It deliberately ignores the
registry's task argument. Private user instructions, reference actions and the
gold database are never supplied to MAS prompts. The source policy replaces the
project's earlier category/membership rules in this runtime.

Identification requires a successful native email or name-plus-zip lookup even
when a user ID is claimed in chat. A successful lookup loads the profile;
subsequent private order requests must belong to that customer. Only one customer
can be served in the conversation. The adapter handles fresh conversations,
including upstream's initial greeting; pre-populated task histories are rejected
rather than silently treated as authenticated sessions. The current 114-task
population has no pre-populated task histories.

The order role can request all upstream retail read/write operations: order
cancellation, item/address/payment changes, return/exchange requests and default
address updates. Upstream tools enforce their actual business rules. Each turn
emits either one tool call or one customer message, matching the native protocol.

For every write, the runtime:

1. Validates the proposed tool arguments and checks customer/order scope.
2. Stores the exact proposal and shows its details to the customer.
3. Waits for a clear confirmation such as `yes`, `yes please`, or `I confirm`.
   A refusal, changed request or further question does not confirm the old action.
4. Re-reads the order/profile. A changed record requires a new proposal and
   confirmation; otherwise it emits exactly the stored write arguments.
5. Records the actual tool outcome. The same write is not automatically repeated
   after success or an error with uncertain outcome.

The orchestrator executes each emitted call against the in-memory source
database. The agent does not execute a hidden second tool call. Native source
files are not rewritten by transactions. Return/exchange requested, refund
creation, and bank posting retain their source meanings. Generated customer
answers remain model behavior to evaluate; tool guards do not prove that every
explanation is correct or every task will succeed.

Confirmation is deliberately conservative in this first version. A reply adding
new conditions must be handled as a change rather than a blanket approval.
Retries/recovery guidance and Matrace training are not implemented here.

## Native scoring and result files

The runner uses upstream `build_text_orchestrator` and `run_simulation` with
`EvaluationType.ALL`. It preserves task-specific `reward_basis`, database
comparison, required natural-language assertions and applicable communication
checks. Extra valid lookups are allowed: reference actions derive a target
database, not an exact required tool sequence. A DB match cannot compensate for
a failed mandatory assertion. No DeepEval score replaces native reward.

The local NL judge is configured through the pinned evaluator's module-level
model settings because that API has no per-run NL model argument. Runs are
serial, settings are restored on error, and prompts/scoring algorithms remain
upstream's. Missing, duplicated or changed mandatory judge assertions are
reported as evaluation errors rather than accepted as vacuous success.

Results go to a new directory under `evaluation/tau-native/runs/`:

- `config.json`: exact upstream revision, language/task pins, model settings,
  selected tasks and custom-run status.
- `task-…-trial-….json`: native `SimulationRun`, conversation, source policy,
  termination and reward details.
- `task-…-trial-….steps.json`: observed role decisions, proposals, confirmation
  events and correlated tool requests/results. Proposals are distinct from
  actual tool outcomes. No private reasoning is requested.
- `task-…-trial-….incomplete.json`: unscored conversation retained when execution
  or evaluation raises before a complete native result is available.
- `results.json` and `summary.json`: per-trial rewards/errors and aggregate
  completion. Errors remain explicit with null reward; they are not passes.

`--output /new/directory` selects a destination; existing directories are never
overwritten. The process exits unsuccessfully on a failed task or scoring error.
For a partially scored run, `native_mean_reward` averages scored trials only;
`complete`, error counts and `success_rate_over_requested` retain the requested
denominator. These are not pass^k or released leaderboard statistics.

**This is a native custom run, not a leaderboard-comparable result.** The MAS and
local user/judge models differ from released configurations. It retains native
tasks/tools/reward meaning, but numerical results cannot be presented as scores
from an official published configuration. DeepEval can be added later as a
separate diagnostic; its seven existing scripts remain available for the older
read-only suite. No native-to-DeepEval score replacement is implemented.

## Validation

```bash
uv run --frozen --extra tau-native python -m unittest discover -s tests -v
```

Native tests use real tools, database writes, the user simulator class,
orchestrator, language builder and native evaluator, with controlled model
replies. They cover all seven write types, confirmation/refusal, changed records,
ownership, duplicate/failed writes, fresh fixtures, gold isolation, mandatory
assertions and failed-evaluation artifacts. Native tests skip if the optional
extra or pinned data is absent. These are integration results, not live model
benchmark performance. The cloud has no running Ollama service; obtain the first
live baseline locally before tuning prompts or beginning Matrace recovery work.
