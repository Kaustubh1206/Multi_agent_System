# Simple retail support graph

The supervisor uses Ollama **qwen3:4b** to classify intent, choose a specialist,
and ask for missing information. **qwen3:0.6b** is configured for the specialist
agents. Specialists still return placeholder messages; their model calls and
business logic will be implemented separately.

## Run

With Python 3.12 and uv:

```bash
uv sync --frozen
ollama pull qwen3:4b
ollama pull qwen3:0.6b
```

Run `ollama serve` if the local Ollama service is not already running.
For direct Python use, create a graph with a checkpointer:

```python
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from src.graphs import build_graph

graph = build_graph(InMemorySaver())
config = {"configurable": {"thread_id": "retail-demo-1"}}
result = graph.invoke(
    {"messages": [HumanMessage(content="Where is my order?")]}, config
)
if result.get("__interrupt__"):
    print(result["__interrupt__"][0].value["question"])
    # The application collects the human answer, then resumes the SAME thread.
    result = graph.invoke(Command(resume="ORD-001"), config)
```

An unclear answer may cause another interrupt. Keep collecting answers and
resuming until the graph finishes. Human-in-the-loop here means asking the
customer for information, not staff approval of a business action.

The exported `src.graphs:graph` is for the LangGraph development server, which
supplies checkpointing. Direct callers must use `build_graph(checkpointer)`.
`InMemorySaver` lasts only within the current process; use persistent checkpoint
storage when conversations must survive process restarts.

## State and traces

- `messages` retains user messages, supervisor questions, human replies, and
  specialist responses in order.
- `supervisor_history` appends each structured decision, including intent,
  intended specialist, extracted identifiers, and clarification question.
- `clarification_question` holds the pending question and clears when resolved.
  The supervisor checkpoints its decision before looping back to interrupt;
  resuming does not repeat the model call that originally asked the question.
- `verified_user_id` is trusted application/session input. Extracted `user_id`
  is conversational information and does not verify identity. Map the simple
  dataset's `customer_id` to `verified_user_id` in the evaluation harness.

Use one thread ID per conversation. LangGraph checkpoint history
(`graph.get_state_history(config)`) retains step snapshots; streaming with
`stream_mode="updates"` exposes node updates. The conversation and decision
history provide inputs for future metrics, but are not a complete model trace.
For full graph/model runs, inputs/outputs, latency, and model usage metadata
when provided by Ollama, attach LangChain callbacks through the run config or
configure LangSmith tracing (`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`,
`LANGSMITH_PROJECT`) before starting the process. Credentials belong in secure
environment settings. Full tracing must be enabled at execution time; metrics
and a trace exporter are not implemented yet.

## Data and offline checks

`evaluation/simple-retail/` contains 10 fictional customers with one order each,
five simple scenarios, and a short policy. Scenario S02 expects a supervisor
interrupt. This dataset is not injected into the model or connected to
specialist tools yet.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The graph tests use controlled model responses to verify routing, checkpointing,
interrupt/resume, and history retention. They do not measure live model quality.
