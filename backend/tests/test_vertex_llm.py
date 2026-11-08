"""The Vertex AI LLM: cost accounting, retries, and the fake-client path.

The real client is never constructed here (no GCP credentials in
CI) — the tests inject a fake with the same shape as the
google-genai client (client.models.generate_content returning a
response with .text and .usage_metadata).

The contract test builds a REAL google-genai response object from
wire-format JSON. That matters: the first live run broke because
the code read usage.response_token_count, a field that does not
exist (the real one is candidates_token_count) — and the fake
here used the same wrong name, so the tests passed and the live
run failed. A fake built from the real SDK type cannot repeat
that: the wrong field raises AttributeError on the real object.
The contract test is skipped where google-genai is not installed
(the gcp extra is live-only); the cost tests below use a fake
with the real field names and run everywhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rca_agent.llm.vertex import VertexLLM

# One real Vertex AI response, in the wire format the REST API
# returns (usageMetadata with promptTokenCount,
# candidatesTokenCount, thoughtsTokenCount). The reply itself is
# 12 characters of JSON; the thinking behind it is 140 tokens —
# that ratio is the live cost story (see the thinking-budget
# note in vertex.py).
REAL_RESPONSE_JSON = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": '{"sql": "SELECT 1"}'}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 1200,
        "candidatesTokenCount": 152,
        "thoughtsTokenCount": 140,
        "totalTokenCount": 1492,
    },
    "modelVersion": "gemini-3.8-flash",
}


def test_contract_real_sdk_response_shape() -> None:
    """The code reads the fields a real SDK response actually has.

    Builds a real GenerateContentResponse from wire JSON and
    passes it through the LLM. If the code read a field that does
    not exist (as response_token_count did), this fails with
    AttributeError — which is exactly the live failure.
    """
    pytest.importorskip("google.genai")
    from google.genai import types

    response = types.GenerateContentResponse.model_validate(REAL_RESPONSE_JSON)
    # The trap, stated plainly: this attribute does not exist on
    # the real usage object.
    assert not hasattr(response.usage_metadata, "response_token_count")

    class _RealResponseClient:
        def __init__(self) -> None:
            self.models = self

        def generate_content(self, *, model: str, contents: str, config: dict[str, Any]) -> object:
            return response

    llm = VertexLLM(project="test-project", client=_RealResponseClient())
    result = llm.complete("prompt")
    assert result.text == '{"sql": "SELECT 1"}'
    assert result.tokens_in == 1200
    # Candidates (152) + thinking (140), both billed as output.
    assert result.tokens_out == 292
    assert abs(result.cost_usd - (1200 * 0.75 + 292 * 3.75) / 1_000_000) < 1e-9


class _FakeModels:
    def __init__(
        self, text: str, prompt_tokens: int, candidates_tokens: int, thoughts: int = 0
    ) -> None:
        self.text = text
        self.prompt_tokens = prompt_tokens
        self.candidates_tokens = candidates_tokens
        self.thoughts = thoughts
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> SimpleNamespace:
        self.calls.append({"model": model, "contents": contents, "config": config})
        usage = SimpleNamespace(
            prompt_token_count=self.prompt_tokens,
            candidates_token_count=self.candidates_tokens,
            thoughts_token_count=self.thoughts,
        )
        return SimpleNamespace(text=self.text, usage_metadata=usage)


def _llm(text: str = '{"sql": "SELECT 1"}', **kwargs: int) -> tuple[VertexLLM, _FakeModels]:
    models = _FakeModels(
        text, kwargs.pop("prompt_tokens", 1000), kwargs.pop("candidates_tokens", 500)
    )
    client = SimpleNamespace(models=models)
    return VertexLLM(project="test-project", client=client), models


def test_complete_returns_text_tokens_and_cost() -> None:
    llm, models = _llm(prompt_tokens=1000, candidates_tokens=500)
    response = llm.complete("[STEP sql]\nWrite a query.")
    assert response.text == '{"sql": "SELECT 1"}'
    assert response.tokens_in == 1000
    assert response.tokens_out == 500
    # Global-endpoint prices: 1000 * 0.75 + 500 * 3.75, per 1M tokens.
    assert abs(response.cost_usd - (1000 * 0.75 + 500 * 3.75) / 1_000_000) < 1e-9
    assert models.calls[0]["model"] == "gemini-3.8-flash"
    assert models.calls[0]["config"]["response_mime_type"] == "application/json"


def test_thinking_tokens_are_billed_as_output() -> None:
    models = _FakeModels(
        '{"sql": "SELECT 1"}', prompt_tokens=1000, candidates_tokens=500, thoughts=200
    )
    client = SimpleNamespace(models=models)
    llm = VertexLLM(project="test-project", client=client)
    response = llm.complete("prompt")
    assert response.tokens_out == 700
    assert abs(response.cost_usd - (1000 * 0.75 + 700 * 3.75) / 1_000_000) < 1e-9


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


def test_defaults_are_global_location_and_global_prices() -> None:
    llm = VertexLLM(project="test-project")
    assert llm.location == "global"
    assert llm.input_price_per_million == 0.75
    assert llm.output_price_per_million == 3.75


class _FlakyModels:
    """Fails with a coded error N times, then succeeds."""

    def __init__(self, fail_codes: list[int]) -> None:
        self.fail_codes = fail_codes
        self.calls = 0

    def generate_content(
        self, *, model: str, contents: str, config: dict[str, Any]
    ) -> SimpleNamespace:
        self.calls += 1
        if self.calls <= len(self.fail_codes):
            error = Exception(f"HTTP {self.fail_codes[self.calls - 1]}")
            error.code = self.fail_codes[self.calls - 1]  # type: ignore[attr-defined]
            raise error
        usage = SimpleNamespace(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0
        )
        return SimpleNamespace(text="{}", usage_metadata=usage)


def test_retries_on_429_then_succeeds() -> None:
    models = _FlakyModels([429, 429])
    llm = VertexLLM(
        project="test-project",
        client=SimpleNamespace(models=models),
        retry_base_delay_seconds=0.0,
    )
    response = llm.complete("prompt")
    assert response.text == "{}"
    assert models.calls == 3


def test_retries_on_503_then_succeeds() -> None:
    models = _FlakyModels([503])
    llm = VertexLLM(
        project="test-project",
        client=SimpleNamespace(models=models),
        retry_base_delay_seconds=0.0,
    )
    llm.complete("prompt")
    assert models.calls == 2


def test_no_retry_on_400() -> None:
    models = _FlakyModels([400])
    llm = VertexLLM(
        project="test-project",
        client=SimpleNamespace(models=models),
        retry_base_delay_seconds=0.0,
    )
    with pytest.raises(Exception, match="HTTP 400"):
        llm.complete("prompt")
    assert models.calls == 1


def test_retries_give_up_after_max_retries() -> None:
    models = _FlakyModels([429, 429, 429, 429, 429])
    llm = VertexLLM(
        project="test-project",
        client=SimpleNamespace(models=models),
        max_retries=3,
        retry_base_delay_seconds=0.0,
    )
    with pytest.raises(Exception, match="HTTP 429"):
        llm.complete("prompt")
    assert models.calls == 4  # 1 attempt + 3 retries


def test_thinking_budget_is_sent_when_set() -> None:
    llm, models = _llm()
    llm.thinking_budget = 512
    llm.complete("prompt")
    assert models.calls[0]["config"]["thinking_config"] == {"thinking_budget": 512}


def test_no_thinking_config_by_default() -> None:
    llm, models = _llm()
    llm.complete("prompt")
    assert "thinking_config" not in models.calls[0]["config"]
