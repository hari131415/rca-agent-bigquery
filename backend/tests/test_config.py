"""GuardrailSettings.from_env: the GB env vars Terraform sets."""

from __future__ import annotations

import pytest

from rca_agent.config import GuardrailSettings, VertexSettings


def test_vertex_settings_default_to_global(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("VERTEX_LOCATION", "VERTEX_MODEL_ID", "VERTEX_THINKING_BUDGET"):
        monkeypatch.delenv(key, raising=False)
    settings = VertexSettings.from_env()
    assert settings.location == "global"
    assert settings.model_id == "gemini-3.8-flash"
    assert settings.thinking_budget is None


def test_vertex_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERTEX_LOCATION", "us-central1")
    monkeypatch.setenv("VERTEX_MODEL_ID", "gemini-3.8-flash")
    monkeypatch.setenv("VERTEX_THINKING_BUDGET", "512")
    settings = VertexSettings.from_env()
    assert settings.location == "us-central1"
    assert settings.thinking_budget == 512


def test_gb_env_vars_win_over_byte_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MAX_BYTES_PER_QUERY_GB", "2")
    monkeypatch.setenv("RCA_MAX_BYTES_PER_DAY_GB", "5")
    monkeypatch.setenv("RCA_MAX_BYTES_PER_QUERY", "123")
    monkeypatch.setenv("RCA_MAX_BYTES_PER_DAY", "456")
    settings = GuardrailSettings.from_env()
    assert settings.max_bytes_per_query == 2_000_000_000
    assert settings.max_bytes_per_day == 5_000_000_000


def test_byte_env_vars_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RCA_MAX_BYTES_PER_QUERY_GB", raising=False)
    monkeypatch.delenv("RCA_MAX_BYTES_PER_DAY_GB", raising=False)
    monkeypatch.setenv("RCA_MAX_BYTES_PER_QUERY", "123")
    monkeypatch.setenv("RCA_MAX_BYTES_PER_DAY", "456")
    settings = GuardrailSettings.from_env()
    assert settings.max_bytes_per_query == 123
    assert settings.max_bytes_per_day == 456


def test_defaults_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "RCA_MAX_BYTES_PER_QUERY_GB",
        "RCA_MAX_BYTES_PER_DAY_GB",
        "RCA_MAX_BYTES_PER_QUERY",
        "RCA_MAX_BYTES_PER_DAY",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = GuardrailSettings.from_env()
    assert settings.max_bytes_per_query == 1_000_000_000
    assert settings.max_bytes_per_day == 10_000_000_000
