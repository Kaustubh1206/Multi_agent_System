from langchain_ollama import ChatOllama


def make_model(name: str, num_predict: int = 256) -> ChatOllama:
    return ChatOllama(
        model=name,
        temperature=0,
        num_ctx=4096,
        num_predict=num_predict,
        reasoning=False,
    )


supervisor_model = make_model("qwen3:4b", num_predict=512)
specialist_model = make_model("qwen3:0.6b")
