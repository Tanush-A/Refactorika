"""Embedding provider abstraction.

Supports two backends:
  - sentence-transformers (default, offline): all-MiniLM-L6-v2, 384-dim
  - OpenAI text-embedding-3-small (1536-dim): requires OPENAI_API_KEY env var
    AND REFACTORIKA_EMBED=openai

Importing this module never raises — all heavyweight imports are lazy and
guarded by try/except ImportError.

Module-level vars set lazily on first embed call:
  _PROVIDER: str   e.g. "sentence-transformers" | "openai" | "none"
  _DIM: int        embedding dimensionality
"""

from __future__ import annotations

import os

# Lazy, set on first successful embed call
_PROVIDER: str = "none"
_DIM: int = 384


def available() -> bool:
    """Return True if at least one embedding provider is importable and usable.

    Never raises.
    """
    try:
        _use_openai = (
            os.environ.get("REFACTORIKA_EMBED", "").lower() == "openai"
            and bool(os.environ.get("OPENAI_API_KEY"))
        )
        if _use_openai:
            import openai  # noqa: F401
            return True
    except ImportError:
        pass

    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        pass

    return False


def _get_openai_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed via OpenAI text-embedding-3-small."""
    global _PROVIDER, _DIM
    import openai

    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    vectors = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
    _PROVIDER = "openai"
    _DIM = len(vectors[0]) if vectors else 1536
    return vectors


def _get_st_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed via sentence-transformers (offline)."""
    global _PROVIDER, _DIM
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    vectors = model.encode(texts, convert_to_numpy=True).tolist()
    _PROVIDER = "sentence-transformers"
    _DIM = len(vectors[0]) if vectors else 384
    return vectors


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings. Returns a list of float vectors.

    Raises RuntimeError if no provider is available — callers should check
    available() first.
    """
    if not texts:
        return []

    use_openai = (
        os.environ.get("REFACTORIKA_EMBED", "").lower() == "openai"
        and bool(os.environ.get("OPENAI_API_KEY"))
    )

    if use_openai:
        try:
            return _get_openai_embeddings(texts)
        except Exception:
            pass  # fall through to sentence-transformers

    try:
        return _get_st_embeddings(texts)
    except ImportError:
        pass

    raise RuntimeError(
        "No embedding provider available. "
        "Install sentence-transformers or set OPENAI_API_KEY + REFACTORIKA_EMBED=openai."
    )


def embed_one(text: str) -> list[float]:
    """Embed a single string. Convenience wrapper around embed()."""
    return embed([text])[0]
