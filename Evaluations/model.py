"""Ollama judge configuration. No hosted API key is needed."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from deepeval.models import OllamaModel

# Edit this default, export EVAL_MODEL, or pass --model to any evaluation script.
eval_model = os.environ.get("EVAL_MODEL", "llama3.1:8b")


def ollama_url():
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    return host if host.startswith(("http://", "https://")) else "http://" + host


def build_judge(name=None):
    return OllamaModel(model=name or eval_model, base_url=ollama_url(), temperature=0,
                       generation_kwargs={"num_ctx": 16384, "num_predict": 2048})
