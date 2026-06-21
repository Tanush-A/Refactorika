"""Provider-agnostic LLM harness: generation and embeddings as separate abstractions.

Generation (Claude, Ollama, …) and embeddings (local MiniLM, Ollama, …) are deliberately
*separate* hierarchies: Anthropic has no embeddings API, so the embedding backend must work
regardless of which generation provider is selected. Providers are chosen by env:

    REFACTORIKA_LLM_PROVIDER   anthropic | ollama        (default: anthropic)
    REFACTORIKA_LLM_MODEL      model id                  (provider default otherwise)
    REFACTORIKA_LLM_BASE_URL   for ollama                (default: http://localhost:11434)
    REFACTORIKA_LLM_API_KEY    falls back to ANTHROPIC_API_KEY for anthropic

    REFACTORIKA_EMBED_PROVIDER local | ollama            (default: local)
    REFACTORIKA_EMBED_MODEL    model id                  (provider default otherwise)

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
    return LocalEmbeddingProvider(model or _DEFAULT_LOCAL_EMBED)


def _http_post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local/trusted URL)
        return json.loads(resp.read().decode())
