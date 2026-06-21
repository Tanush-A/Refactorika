"""Provider-agnostic harness: selection, the Ollama path (mocked), and cache keying."""

from __future__ import annotations

from refactorika.llm import providers as P
from refactorika.llm.client import LLMClient
from refactorika.llm.providers import (
    AnthropicProvider,
    LocalEmbeddingProvider,
    OllamaEmbeddingProvider,
    OllamaProvider,
    get_embedding_provider,
    get_generation_provider,
)


def test_default_generation_provider_is_anthropic(monkeypatch):
    monkeypatch.delenv("REFACTORIKA_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("REFACTORIKA_LLM_MODEL", raising=False)
    p = get_generation_provider()
    assert isinstance(p, AnthropicProvider)
    assert p.name == "anthropic"
    assert "claude" in p.model


def test_env_selects_ollama_generation(monkeypatch):
    monkeypatch.setenv("REFACTORIKA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("REFACTORIKA_LLM_MODEL", "llama3.2")
    monkeypatch.setenv("REFACTORIKA_LLM_BASE_URL", "http://localhost:9999")
    p = get_generation_provider()
    assert isinstance(p, OllamaProvider)
    assert p.model == "llama3.2"
    assert p.base_url == "http://localhost:9999"


def test_default_embedding_is_local_and_separate_from_generation(monkeypatch):
    monkeypatch.setenv("REFACTORIKA_LLM_PROVIDER", "anthropic")  # no embeddings API
    monkeypatch.delenv("REFACTORIKA_EMBED_PROVIDER", raising=False)
    emb = get_embedding_provider()
    assert isinstance(emb, LocalEmbeddingProvider)  # embeddings work regardless of generation


def test_env_selects_ollama_embedding(monkeypatch):
    monkeypatch.setenv("REFACTORIKA_EMBED_PROVIDER", "ollama")
    monkeypatch.setenv("REFACTORIKA_EMBED_MODEL", "nomic-embed-text")
    emb = get_embedding_provider()
    assert isinstance(emb, OllamaEmbeddingProvider)
    assert emb.model == "nomic-embed-text"


def test_ollama_generation_via_mock(monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout=60.0):
        captured["url"] = url
        captured["model"] = payload["model"]
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setattr(P, "_http_post_json", fake_post)
    out = OllamaProvider("llama3.1").complete([{"role": "user", "content": "hi"}])
    assert out == '{"ok": true}'
    assert captured["url"].endswith("/api/chat")
    assert captured["model"] == "llama3.1"


def test_ollama_embedding_via_mock(monkeypatch):
    def fake_post(url, payload, timeout=60.0):
        return {"embedding": [0.1, 0.2]}

    monkeypatch.setattr(P, "_http_post_json", fake_post)
    vecs = OllamaEmbeddingProvider().embed(["a", "b"])
    assert vecs == [[0.1, 0.2], [0.1, 0.2]]


def test_cache_key_includes_provider(monkeypatch):
    a = LLMClient(provider=AnthropicProvider("claude-sonnet-4-6"))
    o = LLMClient(provider=OllamaProvider("llama3.1"))
    # same prompt, different provider -> different cache key (records don't collide)
    assert a.cache_key("sys", "p") != o.cache_key("sys", "p")


def test_client_replays_from_cache_without_a_live_provider(monkeypatch):
    # Ollama provider with no server; replay_only forces cache-only.
    client = LLMClient(provider=OllamaProvider("llama3.1"), replay_only=True)
    key = client.cache_key("sys", "prompt")
    client._cache[key] = {"answer": 42}
    assert client.complete_json("sys", "prompt") == {"answer": 42}
    assert client.complete_json("sys", "uncached") is None
