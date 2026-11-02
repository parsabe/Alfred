"""Local OpenAI-compatible model server (Ollama, vLLM, LM Studio).

Default target is Gemma 3 12B on Ollama (`ollama pull gemma3:12b`), base URL
`http://localhost:11434/v1`.
"""

from __future__ import annotations

from app.llm.openai import OpenAIProvider

DEFAULT_LOCAL_MODEL = "gemma3:12b"


class LocalProvider(OpenAIProvider):
    name = "local"
    max_tokens_param = "max_tokens"

    def __init__(self, base_url: str, model: str = "", **kwargs: object) -> None:
        super().__init__(
            kwargs.pop("api_key", "") or "local",  # type: ignore[arg-type]
            model or DEFAULT_LOCAL_MODEL,
            base_url=base_url,
            **kwargs,  # type: ignore[arg-type]
        )
