"""Embedding shim — delegates to the provider abstraction in ``llm/providers.py``.

This module used to carry its own sentence-transformers / OpenAI logic, which drifted from the
provider-agnostic harness (two sources of truth, mismatched index dims). It is now a thin
compatibility layer: ``available`` / ``embed`` / ``embed_one`` forward to the *active*
``EmbeddingProvider`` (selected via ``REFACTORIKA_EMBED_PROVIDER``: local | ollama | openai).

The module-level ``_PROVIDER`` / ``_DIM`` are kept for backward compatibility and updated on each
embed, but the vector index no longer derives its identity from them — it asks the provider
directly (see ``memory/vector_index.py``).
"""

from __future__ import annotations

from refactorika.llm.providers import get_embedding_provider

# Backward-compat globals; updated lazily on first successful embed.
_PROVIDER: str = "none"
_DIM: int = 384


def available() -> bool:
    """True if the active embedding provider can produce vectors. Never raises."""
    try:
        return get_embedding_provider().available()
    except Exception:
        return False


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings via the active provider.

    Raises RuntimeError if no provider is available — callers should check available() first.
    """
    if not texts:
        return []
    provider = get_embedding_provider()
    vectors = provider.embed(texts)
    if vectors is None:
        raise RuntimeError(
            "No embedding provider available. Install sentence-transformers, run Ollama, "
            "or set REFACTORIKA_EMBED_PROVIDER=openai with OPENAI_API_KEY."
        )
    global _PROVIDER, _DIM
    _PROVIDER = provider.name
    _DIM = len(vectors[0]) if vectors else provider.dim()
    return vectors


def embed_one(text: str) -> list[float]:
    """Embed a single string. Convenience wrapper around embed()."""
    return embed([text])[0]


def provider_dim() -> tuple[str, int]:
    """(provider name, embedding dim) for the active provider, known without embedding.

    Back-compat shim over the provider abstraction (callers/tests historically imported this
    from here). Falls back to ("none", 0) when no provider is importable.
    """
    try:
        provider = get_embedding_provider()
        return provider.name, provider.dim()
    except Exception:
        return "none", 0
