"""The LLM interface: one method, text in, text out.

The graph never depends on a concrete provider. The mock (mock.py)
implements this interface deterministically for offline runs and
tests; the Vertex AI implementation arrives in Phase 8, when the
model ID and prices are verified against Google's own pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    """What a completion returns, with the accounting the traces need."""

    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float = 0.0


class LLM(Protocol):
    """A text completion model. Prompts carry a [STEP ...] marker line
    so the mock can return the right canned output; a real model
    ignores the marker."""

    def complete(self, prompt: str) -> LLMResponse: ...
