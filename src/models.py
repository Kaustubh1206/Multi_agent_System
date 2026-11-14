from langchain_ollama import ChatOllama


def make_model(name: str) -> ChatOllama:
    return ChatOllama(
        model=name,
        temperature=0,
        num_ctx=4096,
        num_predict=256,
        reasoning=False,
    )


supervisor_model = make_model("qwen3:4b")
specialist_model = make_model("qwen3:0.6b")
