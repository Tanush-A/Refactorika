"""Shared pytest config — keep the suite offline, deterministic, and free.

Real embedding providers (OpenAI / sentence-transformers) make network calls and
cost money. By default every test runs with embeddings *disabled* (tier-2
semantic duplicate detection is skipped), so `pytest` never touches the network.
A test that wants embeddings either marks itself `@pytest.mark.real_embeddings`
(uses the real provider) or injects its own deterministic stub after this fixture
runs (its monkeypatch wins).
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "real_embeddings: allow real embedding-provider network calls"
    )


@pytest.fixture(autouse=True)
def _offline_embeddings(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    if request.node.get_closest_marker("real_embeddings"):
        return  # opted into the real provider
    import refactorika.analysis.embeddings as emb

    monkeypatch.setattr(emb, "available", lambda: False)
