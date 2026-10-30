from langchain_ollama import ChatOllama


llm = ChatOllama(
    model="qwen3:4b",
    temperature=0,
    num_ctx=4096,
    num_predict=256,
    reasoning=False
)

