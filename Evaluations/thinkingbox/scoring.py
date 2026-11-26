"""Convert observed native evidence into the two DeepEval test cases."""

import json

from Evaluations import model  # noqa: F401 -- configure DeepEval before importing it

from deepeval.models import DeepEvalBaseLLM  # isort: skip
from deepeval.test_case import LLMTestCase, ToolCall  # isort: skip
from thinkingbox.common.chat_types import Text

from Evaluations.thinkingbox.ollama_session import OllamaSession


class LocalJudge(DeepEvalBaseLLM):
    """Reuse the configured context size, JSON schemas and no-thinking local API."""

    def __init__(self, name, base_url, num_ctx):
        self.session = OllamaSession(name, base_url, num_ctx)
        super().__init__(name)

    def load_model(self):
        return self.session

    def get_model_name(self):
        return self.name

    def generate(self, prompt, schema=None):
        messages = self.session.get_completion_sync(
            conversation=[Text(role="user", content=prompt)],
            response_schema=schema.model_json_schema() if schema else None,
        )
        text = next(message.content for message in messages if isinstance(message, Text))
        return schema.model_validate_json(text) if schema else text

    async def a_generate(self, prompt, schema=None):
        messages = await self.session.get_completion(
            conversation=[Text(role="user", content=prompt)],
            response_schema=schema.model_json_schema() if schema else None,
        )
        text = next(message.content for message in messages if isinstance(message, Text))
        return schema.model_validate_json(text) if schema else text


def customer_text(text):
    """Remove only the source's trailing control marker from customer-facing text."""
    return text.strip().removesuffix("<DONE>").rstrip()


def build_case(name, task, result):
    context = result.test_context
    if context is None:
        raise ValueError("Missing native test context")
    # tc.init and user_context NEVER enter the MAS factory. Only this grader sees goals.
    reference = {server: init.get("golden_test_case", {}) for server, init in task.init.items()}
    requirements = (
        "CUSTOMER GOAL:\n"
        + task.query
        + "\nPRIVATE USER CONTEXT (constraints/preferences, not wording to repeat):\n"
        + (task.user_context or "")
        + "\nREQUIRED BACKEND OUTCOMES (reference only; these actions were NOT necessarily executed):\n"
        + json.dumps(reference, ensure_ascii=False)
        + "\nReference writes specify outcomes, not an exact tool sequence or verbatim customer answer. "
        "Only observed tool calls/results establish what the agent actually did."
    )
    observed = [
        {
            "tool": item.tool_call.name,
            "arguments": item.tool_call.arguments,
            "result": item.tool_response.content,
        }
        for item in context.tool_calls
    ]
    conversation = [
        {
            "role": msg.role,
            "content": customer_text(msg.content) if msg.role == "assistant" else msg.content,
        }
        for msg in result.messages
        if isinstance(msg, Text) and msg.is_visible and msg.role != "system"
    ]
    policy = "\n\n".join(
        msg.content for msg in result.messages if isinstance(msg, Text) and msg.role == "system"
    )
    native = (
        {
            key: getattr(result.test_result, key)
            for key in ("result", "reward", "is_system_error", "line_content", "metadata")
        }
        if result.test_result
        else None
    )
    # Expected database diffs/hashes are grader artifacts, not observed MAS actions.
    # Keep them in the saved native report, outside the actual workflow supplied to
    # DeepEval's outcome extractor. Source instructions are also not executed steps.
    workflow = {
        "conversation": conversation,
        "operations": observed,
        "observed_tool_call_count": len(observed),
        "finish_reason": result.finish_reason,
    }
    answer = customer_text(context.response)
    case = LLMTestCase(
        name=name,
        input=task.query,
        actual_output=answer,
        expected_output=requirements,
        context=[
            "SOURCE POLICY AND INSTRUCTIONS:\n" + policy,
            "OBSERVED WORKFLOW (including earlier clarification):\n"
            + json.dumps(workflow, ensure_ascii=False),
            "INDEPENDENT NATIVE CHECK RESULT (grader metadata, not agent activity):\n"
            + json.dumps(native, ensure_ascii=False),
        ],
        tools_called=[
            ToolCall(
                name=item.tool_call.name,
                input_parameters=item.tool_call.arguments,
                output=item.tool_response.content,
            )
            for item in context.tool_calls
        ],
        metadata={"source_policy": policy, "native_checks": native},
    )
    # DeepEval 4.3.4's native agentic metric consumes this in-memory evidence.
    # This is evaluation input, not a separate trace collection pipeline.
    case._trace_dict = {
        "type": "agent",
        "name": "thinkingbox_retail_mas",
        "input": task.query,
        "output": answer,
        **workflow,
    }
    return case


def score(case, judge, metric_names):
    from Evaluations.thinkingbox.answer_correctness import build_metric as answer
    from Evaluations.thinkingbox.task_completion import build_metric as completion

    results = {}
    builders = {"Task Completion": completion, "Answer Correctness": answer}
    for name in metric_names:
        try:
            if name == "Answer Correctness" and not case.actual_output.strip():
                results[name] = {
                    "status": "scored",
                    "score": 0.0,
                    "passed": False,
                    "method": "empty_answer_check",
                    "reason": "No substantive customer-facing answer; a control marker alone is not an answer.",
                }
                continue
            metric = builders[name](judge)
            metric.measure(case)
            if metric.score is None:
                raise ValueError("Judge returned no score")
            results[name] = {
                "status": "scored",
                "score": metric.score,
                "passed": metric.is_successful(),
                "reason": metric.reason,
            }
        except Exception as error:
            results[name] = {
                "status": "error",
                "score": None,
                "passed": False,
                "reason": f"{type(error).__name__}: {error}",
            }
    return results
