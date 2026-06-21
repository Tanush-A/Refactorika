"""Embedding provider abstraction.

Supports two backends:
  - OpenAI text-embedding-3-small (1536-dim): PRIMARY when OPENAI_API_KEY is set
    and the `openai` package is importable.
  - sentence-transformers (offline fallback): all-MiniLM-L6-v2, 384-dim.

Selection logic (shared by available(), provider_dim(), embed()):
  - REFACTORIKA_EMBED=local  -> force sentence-transformers
  - else if OPENAI_API_KEY set AND openai importable -> OpenAI
  - else if sentence-transformers importable        -> sentence-transformers
  - else -> neither

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


def _ensure_dotenv() -> None:
    """Populate os.environ from .env if not already loaded. Never raises."""
    try:
        from refactorika.core.storage import _load_dotenv

        _load_dotenv()
    except Exception:
        pass


def _openai_importable() -> bool:
    try:
        import openai  # noqa: F401

        return True
    except ImportError:
        return False


def _st_importable() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def _select_provider() -> str:
    """Return the intended provider name without any network/model call.

    One of: "openai" | "sentence-transformers" | "none".
    Pure: env + import-availability checks only. Never raises.
    """
    _ensure_dotenv()

    mode = os.environ.get("REFACTORIKA_EMBED", "").lower()

    if mode == "local":
        return "sentence-transformers" if _st_importable() else "none"

    if os.environ.get("OPENAI_API_KEY") and _openai_importable():
        return "openai"

    if _st_importable():
        return "sentence-transformers"

    return "none"


def provider_dim() -> tuple[str, int]:
    """Return the intended (provider_name, embedding_dim) without any model call.

    - OpenAI chosen              -> ("openai", 1536)
    - sentence-transformers chosen -> ("sentence-transformers", 384)
    - neither available          -> ("none", 0)

    Deterministic and pure — lets the vector index compute its name before the
    first embed.
    """
    provider = _select_provider()
    if provider == "openai":
        return ("openai", 1536)
    if provider == "sentence-transformers":
        return ("sentence-transformers", 384)
    return ("none", 0)


def available() -> bool:
    """Return True if at least one embedding provider is importable and usable.

    Never raises.
    """
    return _select_provider() != "none"


def _get_openai_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed via OpenAI text-embedding-3-small."""
    global _PROVIDER, _DIM
    from openai import OpenAI

    resp = OpenAI().embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    vectors = [d.embedding for d in resp.data]
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

    provider = _select_provider()

    if provider == "openai":
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
        "Install sentence-transformers or set OPENAI_API_KEY (with the openai package)."
    )


def embed_one(text: str) -> list[float]:
    """Embed a single string. Convenience wrapper around embed()."""
    return embed([text])[0]
