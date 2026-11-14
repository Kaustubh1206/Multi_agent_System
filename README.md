# Simple retail support graph

The supervisor uses Ollama **qwen3:4b** for intent, routing, and human follow-ups.
Identity, order, and policy specialists use **qwen3:0.6b** for small structured
extraction/classification tasks. Python tools perform lookups and eligibility
checks; customer answers use those results rather than invented model facts.

## Files

| File | Responsibility |
|---|---|
| `src/prompts.py` | System prompts for all four agents |
| `src/state.py` | Shared conversation state and small identity/order/policy states |
| `src/objects.py` | Structured model outputs and shared `AgentResult` |
| `src/nodes.py` | Model calls, tool calls, agent results, and human interrupt |
| `src/graphs.py` | Nodes and conditional routing |
| `src/tools.py` | Read-only dataset lookup and policy checks |

## Behavior

- **Identity:** exact full-name or email match, ignoring case and surrounding
  whitespace. If both are supplied, both must match the same record. Missing,
  unknown, conflicting, or ambiguous input triggers a human follow-up. A customer
  ID extracted from chat does not identify someone by itself.
- **Order:** retrieves only the identified customer's order. Reports recorded
  status or checks return/cancellation eligibility. Unknown orders and orders
  owned by someone else return the same unavailable response.
- **Policy:** answers public return, refund, and cancellation policy questions
  without customer identification. Product details and policy topics absent
  from the dataset are unsupported.

A unique name/email match identifies a customer **for this fictional prototype**;
this is not production authentication. Optional `verified_user_id` remains a
trusted application/session input and takes priority over a name/email lookup.
Otherwise, the lookup tool sets `identity_state.customer_id`. `user_id` is only
conversational information and is never used to authorize an order lookup.

All operations are **read-only**. Eligibility is an answer, not a completed
return/cancellation. Explicit confirmation and mutation tools will be implemented
later; the graph does not currently submit returns, cancel orders, or pay refunds.
The policy retains those future action requirements. Dataset files are never
modified by the graph.

## Edges and human input

1. Supervisor asks for an order ID when an order-specific request lacks one.
2. Order requests go through identity first if no customer is identified.
3. Successful identification continues the original order request.
4. Any specialist needing information returns its question to the supervisor.
5. The supervisor pauses with `interrupt`; the application collects a human
   reply and resumes the same thread. It then reclassifies using the conversation.
6. Completed answers, denials, and failures end the run. There are no automatic
   retries or separate customer-input node.

Questions and decisions are checkpointed before interruption. Resumption does not
repeat the earlier lookup/model call just to display the same question. An unclear
answer can lead to another pause. Failures are recorded and stop rather than
being retried indefinitely.

## Run

With Python 3.12 and uv:

```bash
uv sync --frozen
ollama pull qwen3:4b
ollama pull qwen3:0.6b
```

Run `ollama serve` if the local service is not already running.
For direct Python use:

```python
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from src.graphs import build_graph

graph = build_graph(InMemorySaver())
config = {"configurable": {"thread_id": "retail-demo-1"}}
result = graph.invoke(
    {"messages": [HumanMessage(
        content="I am Avery Brooks. Where is order ORD-001?"
    )]}, config
)
while result.get("__interrupt__"):
    question = result["__interrupt__"][0].value["question"]
    result = graph.invoke(Command(resume=input(question + " ")), config)
print(result["agent_result"])
```

Use one thread per conversation. For offline scenarios, the dataset's non-null
`customer_id` can be mapped to trusted `verified_user_id` to skip identification.

The exported `src.graphs:graph` is for the LangGraph development server, which
supplies checkpointing. Direct callers need `build_graph(checkpointer)` for
interrupt/resume. `InMemorySaver` lasts only within the process. Use persistent
checkpoint storage when conversations must survive process restarts.

## Structured state and traces

Each specialist has its own small state:

| State | Fields |
|---|---|
| `identity_state` | Extracted name/email and successfully matched customer ID |
| `order_state` | Selected topic and authorized order record |
| `policy_state` | Selected topic and policy version |

Shared state retains messages, extracted identifiers, routing, the pending
question, and the latest `agent_result`. A result contains agent, status,
reason code, customer message, optional question, references, evidence IDs, and
an optional error type. Status distinguishes `completed`, `needs_information`,
`denied`, and `failed`; a policy denial is not an operational failure.

For later evaluation/recovery:

- `supervisor_history`: every successful classification/routing decision.
- `agent_history`: specialist results and supervisor failures/unsupported outcomes.
- `tool_history`: unique evidence ID, tool name, arguments, result or error type,
  start time, and duration. Agent evidence references these IDs.
- `graph.get_state_history(config)`: checkpoint snapshots across pauses/resumes.
- Streaming with `stream_mode="updates"`: node-by-node updates.

Models and tools use native LangChain run tracing. Model runs carry the
`retail-v2` prompt version; tool run metadata links back to the state evidence ID. Attach callbacks through the
run config or enable LangSmith tracing (`LANGSMITH_TRACING=true`, secure
`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`) before execution for model inputs,
outputs, exceptions, timing, parent/child runs, and model usage when Ollama
provides it. State summaries are not a complete model trace. Invalid structured
outputs, tool errors, and model failures have separate reason codes. No Matrace
adapter, metric scoring, or automatic recovery is implemented yet.

Tool histories contain customer information; keep them scoped to the conversation.
Expected scenario outcomes are evaluator inputs and are never sent to the models.

## Data and validation

`evaluation/simple-retail/` contains exactly 10 fictional customers, each with one
order, five scenarios, and the versioned policy. Tools use paths relative to the
source files, so startup does not depend on the shell working directory. Return
checks use the fixture date **2026-11-14**, not the host clock. The Python rule
implementation and policy version must be updated together when rules change.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests execute the real graph, checkpointing, and dataset tools with controlled
model responses. They check routing, interruptions, name/email matching,
ownership, eligibility boundaries, policy answers, failure categories, trace
links, and absence of dataset mutations. They do not measure live model quality.
