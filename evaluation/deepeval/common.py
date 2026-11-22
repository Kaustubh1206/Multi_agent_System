"""Dependency-free checks shared by the isolated evaluator and MAS runner."""
import json
import os
import urllib.request


def ollama_preflight(models, base_url=None):
    host = (base_url or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    with urllib.request.urlopen(host + "/api/tags", timeout=5) as response:
        installed = {item["name"] for item in json.load(response)["models"]}
    missing = set(models) - installed
    if missing:
        raise RuntimeError("Missing Ollama models: " + ", ".join(sorted(missing)))
