"""ThinkingBox's LLM interface over Ollama's local API (also used by its judge)."""

import asyncio
import json
from uuid import uuid4

import httpx
from thinkingbox.common.chat_types import ParallelToolCall, Text, ToolCall, ToolResponse
from thinkingbox.common.llm_session_base import LLMSessionBase
from thinkingbox.common.usage_types import Usage


def encode(messages):
    result = []
    systems = []
    for message in messages:
        if isinstance(message, Text):
            if message.is_visible:
                if message.role == "system":
                    systems.append(message.content)
                else:
                    result.append({"role": message.role, "content": message.content})
        elif isinstance(message, (ParallelToolCall, ToolCall)):
            calls = message.tool_calls if isinstance(message, ParallelToolCall) else [message]
            result.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in calls
                    ],
                }
            )
        elif isinstance(message, ToolResponse):
            result.append(
                {
                    "role": "tool",
                    "tool_name": message.name,
                    "tool_call_id": message.id,
                    "content": message.content,
                }
            )
        else:
            raise TypeError(f"Unsupported ThinkingBox message: {type(message).__name__}")
    # Present source policy and node instructions as one leading system message,
    # independent of how an Ollama model's template handles later system turns.
    return ([{"role": "system", "content": "\n\n".join(systems)}] if systems else []) + result


class OllamaSession(LLMSessionBase):
    def __init__(self, model, base_url="http://127.0.0.1:11434", num_ctx=32768, timeout=600):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.num_ctx, self.timeout = num_ctx, timeout
        self.messages, self.tools = [], {}

    @property
    def tool_names(self):
        return self.tools.keys()

    def add_tools(self, tools):
        self.tools.update({tool.name: tool for tool in tools})

    def reset_tools(self):
        self.tools.clear()

    def add_messages(self, messages):
        self.messages.extend(messages)

    def reset_messages(self):
        self.messages.clear()

    def get_internal_conversation(self):
        return encode(self.messages)

    async def get_completion(
        self, conversation=None, update_conversation=True, response_schema=None
    ):
        messages = self.messages if conversation is None else conversation
        payload = {
            "model": self.model,
            "messages": encode(messages),
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": self.num_ctx, "num_predict": 4096},
        }
        if self.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in self.tools.values()
            ]
        if response_schema is not None:
            payload["format"] = response_schema
        # No automatic request retries: execution is controlled by the native agent loop.
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.base_url + "/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        if data.get("done_reason") == "length":
            raise RuntimeError("Ollama output limit reached; completion not executed")
        # Ollama can silently discard old input when context is too small.
        if data.get("prompt_eval_count", 0) >= self.num_ctx - 4096:
            raise RuntimeError("Input approaches context capacity; increase --num-ctx")
        raw = data["message"]
        output = []
        if raw.get("content", "").strip():
            output.append(Text(role="assistant", content=raw["content"]))
        if raw.get("tool_calls"):
            calls = []
            for call in raw["tool_calls"]:
                function = call["function"]
                arguments = function["arguments"]
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                calls.append(
                    ToolCall(
                        name=function["name"],
                        arguments=arguments,
                        id=call.get("id") or "call_" + uuid4().hex,
                    )
                )
            output.append(ParallelToolCall(tool_calls=calls))
        if not output:
            raise RuntimeError("Ollama returned no text or tool calls")
        self.last_usage = Usage(
            input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0)
        )
        if conversation is None and update_conversation:
            self.add_messages(output)
        return output

    def get_completion_sync(
        self, conversation=None, update_conversation=True, response_schema=None
    ):
        return asyncio.run(self.get_completion(conversation, update_conversation, response_schema))

    @classmethod
    def from_config(cls, config):
        return cls(**config.model_dump(exclude={"type", "factory"}))


def create_session(**kwargs):
    """Importable factory for the native evaluator's isolated test subprocess."""
    return OllamaSession(**kwargs)
