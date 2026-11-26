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


def build_case(name, task, result):
    context = result.test_context
    if context is None:
        raise ValueError("Missing native test context")
    # tc.init and user_context NEVER enter the MAS factory. Only this grader sees goals.
    reference = {server: init.get("golden_test_case", {}) for server, init in task.init.items()}
    requirements = {
        "request": task.query,
        "private_user_goal": task.user_context,
        "reference_backend_outcomes": reference,
        "interpretation": "Reference writes specify outcomes, not an exact tool sequence "
        "or verbatim customer answer. Actual writes must be verified independently.",
    }
    observed = [
        {
            "tool": item.tool_call.name,
            "arguments": item.tool_call.arguments,
            "result": item.tool_response.content,
        }
        for item in context.tool_calls
    ]
    conversation = [
        {"role": msg.role, "content": msg.content}
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
    # Native artifacts retain whole databases. The judge needs outcomes and diffs,
    # not three copies of every unrelated customer/record in the sandbox.
    effects = {
        server: {
            key: value
            for key, value in evidence.items()
            if key not in {"initial_db_state", "result_db_state", "golden_db_state"}
        }
        for server, evidence in context.effects.items()
    }
    workflow = {
        "conversation": conversation,
        "operations": observed,
        "native_checks": native,
        "effects": effects,
        "finish_reason": result.finish_reason,
    }
    case = LLMTestCase(
        name=name,
        input=task.query,
        actual_output=context.response,
        expected_output=json.dumps(requirements, ensure_ascii=False),
        context=[
            "SOURCE POLICY AND INSTRUCTIONS:\n" + policy,
            "OBSERVED WORKFLOW (including earlier clarification):\n"
            + json.dumps(workflow, ensure_ascii=False),
        ],
        tools_called=[
            ToolCall(
                name=item.tool_call.name,
                input_parameters=item.tool_call.arguments,
                output=item.tool_response.content,
            )
            for item in context.tool_calls
        ],
    )
    # DeepEval 4.3.4's native agentic metric consumes this in-memory evidence.
    # This is evaluation input, not a separate trace collection pipeline.
    case._trace_dict = {
        "type": "agent",
        "name": "thinkingbox_retail_mas",
        "input": task.query,
        "output": context.response,
        "source_policy": policy,
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
