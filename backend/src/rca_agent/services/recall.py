"""Semantic recall: past investigations similar to the current
question, offered to the plan step as context.

The interface is a stub for now: NoopRecall returns nothing, so
the graph behaves exactly as it did before recall existed. Real
recall (Vertex embeddings over past investigations, off by
default) is a Phase 8 item; the interface is what the graph and
the API depend on, so the wiring is in place.
"""

from __future__ import annotations

from typing import Any, Protocol


class Recall(Protocol):
    def find_similar(self, question: str, k: int = 3) -> list[dict[str, Any]]: ...


class NoopRecall:
    """No recall: returns no past investigations."""

    def find_similar(self, question: str, k: int = 3) -> list[dict[str, Any]]:
        return []
