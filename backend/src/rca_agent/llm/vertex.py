"""A Gemini model on Vertex AI, behind the LLM interface.

Uses the google-genai SDK with vertexai=True (the model runs in
the user's GCP project, authenticated by Application Default
Credentials — no API key). The model ID and prices were verified
2026-11-07 against Google's own pages: gemini-3.8-flash (GA,
released 2026-09-02), $0.825 input / $4.125 output per 1M tokens
in us-central1 through 2026-12-31, then $1.65 / $8.25 from
2027-01-01. Thinking tokens are billed as output, so they are
added to the output token count for cost. The client is built
lazily (inside _get_client) so importing this module needs no
Google library — the tests inject a fake client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import LLMResponse

# Verified 2026-11-07 against cloud.google.com/vertex-ai/
# generative-ai/pricing (us-central1, non-global endpoint).
DEFAULT_INPUT_PRICE_PER_MILLION = 0.825
DEFAULT_OUTPUT_PRICE_PER_MILLION = 4.125


@dataclass
class VertexLLM:
    """A Gemini model on Vertex AI. Implements the LLM protocol."""

    project: str
    location: str = "us-central1"
    model_id: str = "gemini-3.8-flash"
    input_price_per_million: float = DEFAULT_INPUT_PRICE_PER_MILLION
    output_price_per_million: float = DEFAULT_OUTPUT_PRICE_PER_MILLION
    temperature: float = 0.0
    # Injected for tests; otherwise built lazily from ADC.
    client: Any = None

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        from google import genai

        return genai.Client(vertexai=True, project=self.project, location=self.location)

    def complete(self, prompt: str) -> LLMResponse:
        client = self._get_client()
        response = client.models.generate_content(
            model=self.model_id,
            contents=prompt,
            # The prompts all ask for JSON only; enforcing the MIME
            # type makes the JSON parsing in the graph nodes robust
            # instead of relying on the model to comply.
            config={"temperature": self.temperature, "response_mime_type": "application/json"},
        )
        usage = response.usage_metadata
        tokens_in = int(usage.prompt_token_count) if usage and usage.prompt_token_count else 0
        tokens_out = 0
        if usage:
            # Thinking tokens are billed as output.
            tokens_out = int(usage.response_token_count or 0) + int(usage.thoughts_token_count or 0)
        cost_usd = (
            tokens_in * self.input_price_per_million + tokens_out * self.output_price_per_million
        ) / 1_000_000
        return LLMResponse(
            text=response.text or "",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )
