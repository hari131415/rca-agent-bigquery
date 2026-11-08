"""A Gemini model on Vertex AI, behind the LLM interface.

Uses the google-genai SDK with vertexai=True (the model runs in
the user's GCP project, authenticated by Application Default
Credentials — no API key). The client is built lazily (inside
_get_client) so importing this module needs no Google library —
the tests inject a fake client.

Location and prices: gemini-3.8-flash is available ONLY on the
`global` location for this project (the us-central1 endpoint
returned 404 in the first live run, 2026-11-08; `global` worked).
The global-endpoint prices are $0.75 input / $3.75 output per 1M
tokens. Thinking tokens are billed as output, so they are added
to the output token count for cost.

Usage accounting: the SDK's usage object carries
`candidates_token_count` for the model's reply tokens — there is
no `response_token_count` field (accessing it raises
AttributeError). The first live run exposed this: the code read
the wrong field and the fake in the tests used the same wrong
name, so the tests passed and the live run broke. The contract
test in test_vertex_llm.py builds a real SDK response object
from wire-format JSON, so a wrong field name fails the test.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

from .base import LLMResponse

# Global-endpoint prices for gemini-3.8-flash, per 1M tokens
# (the live run 2026-11-08 ran on the global endpoint).
DEFAULT_INPUT_PRICE_PER_MILLION = 0.75
DEFAULT_OUTPUT_PRICE_PER_MILLION = 3.75

# The retryable HTTP codes: 429 (quota / rate limit) and 503
# (the model is overloaded). Anything else (400/401/403/404)
# fails fast — retrying it would only burn quota.
_RETRYABLE_CODES = frozenset({429, 503})
_MAX_RETRY_DELAY_SECONDS = 30.0


def _is_retryable(exc: Exception) -> bool:
    """True for a transient Vertex AI error (429 or 503).

    The google-genai SDK raises APIError subclasses (ClientError
    for 4xx, ServerError for 5xx) carrying a `.code` attribute;
    verified against google-genai 3.0.0. Duck-typed so this needs
    no SDK import at module level (the library is a live-only
    extra, not installed in CI).
    """
    return getattr(exc, "code", None) in _RETRYABLE_CODES


@dataclass
class VertexLLM:
    """A Gemini model on Vertex AI. Implements the LLM protocol."""

    project: str
    location: str = "global"
    model_id: str = "gemini-3.8-flash"
    input_price_per_million: float = DEFAULT_INPUT_PRICE_PER_MILLION
    output_price_per_million: float = DEFAULT_OUTPUT_PRICE_PER_MILLION
    temperature: float = 0.0
    # Cap on the model's thinking tokens (thinking_config /
    # thinking_budget in the generate-content config; verified
    # against google-genai 3.0.0). None sends no thinking config,
    # i.e. the SDK default. Thinking dominates the live cost, so a
    # budget is the lever for it — but a low budget can hurt the
    # SQL the model writes, so it is off by default.
    thinking_budget: int | None = None
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    # Injected for tests; otherwise built lazily from ADC.
    client: Any = None

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        from google import genai

        return genai.Client(vertexai=True, project=self.project, location=self.location)

    def _generate_with_retry(self, prompt: str, config: dict[str, Any]) -> Any:
        """One generate call, retried on 429/503 with exponential
        backoff and full jitter. Anything else raises at once."""
        client = self._get_client()
        attempt = 0
        while True:
            try:
                return client.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 - retried selectively below
                if not _is_retryable(exc) or attempt >= self.max_retries:
                    raise
                delay = min(
                    _MAX_RETRY_DELAY_SECONDS,
                    self.retry_base_delay_seconds * (2**attempt),
                )
                time.sleep(random.uniform(0, delay))
                attempt += 1

    def complete(self, prompt: str) -> LLMResponse:
        config: dict[str, Any] = {
            "temperature": self.temperature,
            "response_mime_type": "application/json",
        }
        if self.thinking_budget is not None:
            config["thinking_config"] = {"thinking_budget": self.thinking_budget}
        response = self._generate_with_retry(prompt, config)
        usage = response.usage_metadata
        tokens_in = 0
        tokens_out = 0
        if usage is not None:
            tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
            # Thinking tokens are billed as output.
            tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
                getattr(usage, "thoughts_token_count", 0) or 0
            )
        cost_usd = (
            tokens_in * self.input_price_per_million + tokens_out * self.output_price_per_million
        ) / 1_000_000
        return LLMResponse(
            text=response.text or "",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
