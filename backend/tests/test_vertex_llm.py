"""The Vertex AI LLM: cost accounting and the fake-client path.

The real client is never constructed here (no GCP credentials in
CI) — the tests inject a fake with the same shape as the
google-genai client (client.models.generate_content returning a
response with .text and .usage_metadata).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rca_agent.llm.vertex import VertexLLM


class _FakeModels:
    def __init__(
        self, text: str, prompt_tokens: int, response_tokens: int, thoughts: int = 0
    ) -> None:
        self.text = text
        self.prompt_tokens = prompt_tokens
        self.response_tokens = response_tokens
        self.thoughts = thoughts
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> SimpleNamespace:
        self.calls.append({"model": model, "contents": contents, "config": config})
        usage = SimpleNamespace(
            prompt_token_count=self.prompt_tokens,
            response_token_count=self.response_tokens,
            thoughts_token_count=self.thoughts,
        )
        return SimpleNamespace(text=self.text, usage_metadata=usage)


def _llm(text: str = '{"sql": "SELECT 1"}', **kwargs: int) -> tuple[VertexLLM, _FakeModels]:
    models = _FakeModels(
        text, kwargs.pop("prompt_tokens", 1000), kwargs.pop("response_tokens", 500)
    )
    client = SimpleNamespace(models=models)
    return VertexLLM(project="test-project", client=client), models


def test_complete_returns_text_tokens_and_cost() -> None:
    llm, models = _llm(prompt_tokens=1000, response_tokens=500)
    response = llm.complete("[STEP sql]\nWrite a query.")
    assert response.text == '{"sql": "SELECT 1"}'
    assert response.tokens_in == 1000
    assert response.tokens_out == 500
    # 1000 * 0.825 + 500 * 4.125, per 1M tokens.
    assert abs(response.cost_usd - (1000 * 0.825 + 500 * 4.125) / 1_000_000) < 1e-9
    assert models.calls[0]["model"] == "gemini-3.8-flash"
    assert models.calls[0]["config"]["response_mime_type"] == "application/json"


def test_thinking_tokens_are_billed_as_output() -> None:
    models = _FakeModels(
        '{"sql": "SELECT 1"}', prompt_tokens=1000, response_tokens=500, thoughts=200
    )
    client = SimpleNamespace(models=models)
    llm = VertexLLM(project="test-project", client=client)
    response = llm.complete("prompt")
    assert response.tokens_out == 700
    assert abs(response.cost_usd - (1000 * 0.825 + 700 * 4.125) / 1_000_000) < 1e-9


def test_missing_usage_metadata_is_zero() -> None:
    class NoUsage:
        def generate_content(
            self, *, model: str, contents: str, config: dict[str, Any]
        ) -> SimpleNamespace:
            return SimpleNamespace(text="{}", usage_metadata=None)

    llm = VertexLLM(project="test-project", client=SimpleNamespace(models=NoUsage()))
    response = llm.complete("prompt")
    assert response.tokens_in == 0
    assert response.tokens_out == 0
    assert response.cost_usd == 0.0
