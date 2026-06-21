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
    from refactorika.llm import providers as prov

    monkeypatch.setattr(emb, "available", lambda: False)
    # The provider path (not the module fn above) is what DecisionMemory and the codebase index
    # actually consult. With the optional [semantic] extra installed these would otherwise go
    # live and make the suite non-deterministic (real embeddings change LLM prompts / recall).
    # Force every concrete provider unavailable so semantic recall stays off by default; tests
    # that want it inject their own provider (a separate subclass, unaffected by these patches).
    for cls in (
        prov.LocalEmbeddingProvider,
        prov.OllamaEmbeddingProvider,
        prov.OpenAIEmbeddingProvider,
    ):
        monkeypatch.setattr(cls, "available", lambda self: False)
