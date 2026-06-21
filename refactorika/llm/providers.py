"""Provider-agnostic LLM harness: generation and embeddings as separate abstractions.

Generation (Claude, Ollama, …) and embeddings (local MiniLM, Ollama, OpenAI, …) are
deliberately *separate* hierarchies: Anthropic has no embeddings API, so the embedding backend
must work regardless of which generation provider is selected. This module is the single source
of truth for embeddings — `analysis/embeddings.py` is a thin shim over it. Providers chosen by env:

    REFACTORIKA_LLM_PROVIDER   anthropic | ollama        (default: anthropic)
    REFACTORIKA_LLM_MODEL      model id                  (provider default otherwise)
    REFACTORIKA_LLM_BASE_URL   for ollama                (default: http://localhost:11434)
    REFACTORIKA_LLM_API_KEY    falls back to ANTHROPIC_API_KEY for anthropic

    REFACTORIKA_EMBED_PROVIDER local | ollama | openai   (default: local)
    REFACTORIKA_EMBED_MODEL    model id                  (provider default otherwise)
    OPENAI_API_KEY             required for the openai embedding provider

The record/replay cache lives one layer up (in `client.py`) and is keyed by
(provider, model, prompt) so any provider is reproducible for the demo and the eval.
"""

from __future__ import annotations

import json
import os
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional

# Most capable default Claude for judgment work.
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OLLAMA_MODEL = "llama3.1"
_DEFAULT_OLLAMA_BASE = "http://localhost:11434"
_DEFAULT_LOCAL_EMBED = "all-MiniLM-L6-v2"
_DEFAULT_OLLAMA_EMBED = "nomic-embed-text"
_DEFAULT_OPENAI_EMBED = "text-embedding-3-small"

# Known output dimensionality by model substring. Used to name the vector index *before*
# the first embed call (RediSearch needs the dim at index-creation time). Falls back to the
# per-provider default when a model isn't listed.
_KNOWN_DIMS: dict[str, int] = {
    "minilm-l6": 384, "minilm-l12": 384, "mpnet": 768,  # sentence-transformers
    "nomic-embed-text": 768, "mxbai-embed-large": 1024,  # ollama
    "text-embedding-3-small": 1536, "text-embedding-3-large": 3072,  # openai
    "text-embedding-ada-002": 1536,
}


def _dim_for(model: str, default: int) -> int:
    m = model.lower()
    for frag, dim in _KNOWN_DIMS.items():
        if frag in m:
            return dim
    return default


def _load_dotenv(path: str = ".env") -> None:
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------- generation
class GenerationProvider(ABC):
    name: str = "abstract"

    def __init__(self, model: str):
        self.model = model
        # Token usage from the most recent live call: {"input": int, "output": int}.
        self.last_usage: dict = {"input": 0, "output": 0}

    @abstractmethod
    def complete(self, messages: list[dict], **opts) -> Optional[str]:
        """Return assistant text for chat *messages* ([{role, content}, …]), or None on error."""

    def available(self) -> bool:
        """Whether this provider can produce a *new* answer right now."""
        return True


class AnthropicProvider(GenerationProvider):
    name = "anthropic"

    def __init__(self, model: str = _DEFAULT_ANTHROPIC_MODEL, api_key: Optional[str] = None):
        super().__init__(model)
        self._api_key = api_key or os.environ.get("REFACTORIKA_LLM_API_KEY") \
            or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def available(self) -> bool:
        return bool(self._api_key)

    def complete(self, messages: list[dict], **opts) -> Optional[str]:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        try:
            if self._client is None:
                import anthropic

                self._client = anthropic.Anthropic(api_key=self._api_key)
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=opts.get("max_tokens", 2000),
                temperature=opts.get("temperature", 0),
                system=system,
                messages=turns,
            )
            usage = getattr(msg, "usage", None)
            if usage is not None:
                self.last_usage = {"input": getattr(usage, "input_tokens", 0),
                                   "output": getattr(usage, "output_tokens", 0)}
            return "".join(b.text for b in msg.content if b.type == "text")
        except Exception:
            return None


class OllamaProvider(GenerationProvider):
    name = "ollama"

    def __init__(self, model: str = _DEFAULT_OLLAMA_MODEL, base_url: str = _DEFAULT_OLLAMA_BASE):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")

    def complete(self, messages: list[dict], **opts) -> Optional[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": opts.get("temperature", 0)},
        }
        try:
            data = _http_post_json(f"{self.base_url}/api/chat", payload)
            self.last_usage = {"input": data.get("prompt_eval_count", 0),
                               "output": data.get("eval_count", 0)}
            return (data.get("message") or {}).get("content")
        except Exception:
            return None


# ----------------------------------------------------------------- embeddings
class EmbeddingProvider(ABC):
    name: str = "abstract"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """Return one vector per input text, or None if the backend is unavailable."""

    def dim(self) -> int:
        """Output dimensionality, known from the model id (for naming the vector index)."""
        return _dim_for(self.model, 384)

    def available(self) -> bool:
        return True


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers all-MiniLM-L6-v2 on CPU (the [semantic] extra). Offline, no key."""

    name = "local"

    def __init__(self, model: str = _DEFAULT_LOCAL_EMBED):
        super().__init__(model)
        self._model = None

    def available(self) -> bool:
        import importlib.util

        return importlib.util.find_spec("sentence_transformers") is not None

    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        try:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model)
            return [v.tolist() for v in self._model.encode(list(texts))]
        except Exception:
            return None


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(self, model: str = _DEFAULT_OLLAMA_EMBED, base_url: str = _DEFAULT_OLLAMA_BASE):
        super().__init__(model)
        self.base_url = base_url.rstrip("/")

    def dim(self) -> int:
        return _dim_for(self.model, 768)

    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        out: list[list[float]] = []
        try:
            for t in texts:
                data = _http_post_json(
                    f"{self.base_url}/api/embeddings", {"model": self.model, "prompt": t}
                )
                out.append(data["embedding"])
            return out
        except Exception:
            return None


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embeddings (e.g. text-embedding-3-small, 1536-dim). Requires OPENAI_API_KEY."""

    name = "openai"

    def __init__(self, model: str = _DEFAULT_OPENAI_EMBED, api_key: Optional[str] = None):
        super().__init__(model)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None

    def dim(self) -> int:
        return _dim_for(self.model, 1536)

    def available(self) -> bool:
        if not self._api_key:
            return False
        import importlib.util

        return importlib.util.find_spec("openai") is not None

    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        try:
            if self._client is None:
                import openai

                self._client = openai.OpenAI(api_key=self._api_key)
            resp = self._client.embeddings.create(model=self.model, input=list(texts))
            return [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]
        except Exception:
            return None


# -------------------------------------------------------------------- factories
def get_generation_provider() -> GenerationProvider:
    _load_dotenv()
    kind = os.environ.get("REFACTORIKA_LLM_PROVIDER", "anthropic").lower()
    model = os.environ.get("REFACTORIKA_LLM_MODEL")
    if kind == "ollama":
        base = os.environ.get("REFACTORIKA_LLM_BASE_URL", _DEFAULT_OLLAMA_BASE)
        return OllamaProvider(model or _DEFAULT_OLLAMA_MODEL, base_url=base)
    return AnthropicProvider(model or _DEFAULT_ANTHROPIC_MODEL)


def get_embedding_provider() -> EmbeddingProvider:
    _load_dotenv()
    kind = os.environ.get("REFACTORIKA_EMBED_PROVIDER", "local").lower()
    model = os.environ.get("REFACTORIKA_EMBED_MODEL")
    if kind == "ollama":
        base = os.environ.get("REFACTORIKA_LLM_BASE_URL", _DEFAULT_OLLAMA_BASE)
        return OllamaEmbeddingProvider(model or _DEFAULT_OLLAMA_EMBED, base_url=base)
    if kind == "openai":
        return OpenAIEmbeddingProvider(model or _DEFAULT_OPENAI_EMBED)
    return LocalEmbeddingProvider(model or _DEFAULT_LOCAL_EMBED)


def _http_post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local/trusted URL)
        return json.loads(resp.read().decode())
