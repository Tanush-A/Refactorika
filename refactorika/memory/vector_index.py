"""Vector index backed by RediSearch HNSW or brute-force JSON fallback.

Index name pattern: refactorika:vec:{provider}:{dim}

Each document key has the form "{file}:{fn}" and stores:
  - embedding (FLOAT32 binary blob in Redis; list in JSON)
  - file, name, line (metadata)

Fallback (always works): vectors stored under the "vectors" key in the
storage JSON state file and queried with brute-force cosine similarity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from refactorika.core.storage import Storage
    from refactorika.llm.providers import EmbeddingProvider


@dataclass
class Neighbor:
    key: str
    score: float
    meta: dict = field(default_factory=dict)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    try:
        import numpy as np

        av = np.array(a, dtype=float)
        bv = np.array(b, dtype=float)
        return float(np.dot(av, bv) / (np.linalg.norm(av) * np.linalg.norm(bv) + 1e-9))
    except ImportError:
        # Pure Python fallback (slow but always available)
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b + 1e-9)


def _provider_name_and_dim(embed_provider: "Optional[EmbeddingProvider]") -> tuple[str, int]:
    """(name, dim) for the active embedding provider — the single source of index identity.

    Falls back to ("local", 384) so the index name is stable even when no provider is
    importable (the JSON fallback path doesn't care about the name).
    """
    try:
        provider = embed_provider
        if provider is None:
            from refactorika.llm.providers import get_embedding_provider

            provider = get_embedding_provider()
        return provider.name, provider.dim()
    except Exception:
        return "local", 384


class VectorIndex:
    """Vector similarity index with RediSearch HNSW or JSON brute-force fallback."""

    def __init__(
        self,
        storage: "Storage",
        embed_provider: "Optional[EmbeddingProvider]" = None,
        namespace: str = "",
    ) -> None:
        self._storage = storage
        self._redis_client = storage._redis  # may be None
        self._use_redis = False
        # True only when a RedisVL hybrid (BM25 + vector) backend is active. This engine's
        # Redis backend is RediSearch KNN, so hybrid degrades to vector-only and this stays
        # False; the flag exists so query_hybrid()/callers can branch and tests can assert it.
        self._use_redisvl = False
        self._provider_name, self._dim = _provider_name_and_dim(embed_provider)
        # A namespace gives an independent vector space (e.g. "codebase" symbols must not
        # collide with decision-memory vectors, which share this class but a different store).
        suffix = f":{namespace}" if namespace else ""
        self._index_name: str = f"refactorika:vec:{self._provider_name}:{self._dim}{suffix}"
        self._bucket: str = f"vectors:{namespace}" if namespace else "vectors"

        if self._redis_client is not None:
            self._use_redis = self._ensure_redis_index()

    # ------------------------------------------------------------------
    # Redis backend helpers
    # ------------------------------------------------------------------

    def _ensure_redis_index(self) -> bool:
        """Try to create or verify the RediSearch vector index. Returns True on success."""
        try:
            from redis.commands.search.field import NumericField, TextField, VectorField
            from redis.commands.search.indexDefinition import IndexDefinition, IndexType
        except ImportError:
            return False

        try:
            dim = self._dim
            index_name = self._index_name

            try:
                self._redis_client.ft(index_name).info()
                return True  # index already exists
            except Exception:
                pass  # need to create it

            schema = (
                VectorField(
                    "embedding",
                    "HNSW",
                    {
                        "TYPE": "FLOAT32",
                        "DIM": dim,
                        "DISTANCE_METRIC": "COSINE",
                    },
                ),
                TextField("file"),
                TextField("name"),
                NumericField("line"),
                TextField("body"),  # source text, for future BM25/hybrid scoring
                TextField("meta_json"),  # full meta dict (qualname, kind, sha, …)
            )
            definition = IndexDefinition(
                prefix=[f"{index_name}:doc:"],
                index_type=IndexType.HASH,
            )
            self._redis_client.ft(index_name).create_index(schema, definition=definition)
            return True
        except Exception:
            return False

    def _redis_doc_key(self, key: str) -> str:
        return f"{self._index_name}:doc:{key}"

    # ------------------------------------------------------------------
    # JSON fallback helpers
    # ------------------------------------------------------------------

    def _json_read(self) -> dict:
        """Read the full JSON state dict from storage (ensuring this index's bucket exists)."""
        data = self._storage._read_json()
        if self._bucket not in data:
            data[self._bucket] = {}
        return data

    def _json_write(self, data: dict) -> None:
        """Write the full JSON state dict back to storage."""
        self._storage._write_json(data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(
        self, key: str, vector: list[float], meta: "Optional[dict]" = None, *, text: str = ""
    ) -> None:
        """Store or update a vector with associated metadata and an optional text body.

        ``text`` is the source the vector was computed from; it is persisted for BM25/hybrid
        scoring (see query_hybrid) and ignored by pure-vector queries.
        """
        meta = meta or {}
        if self._use_redis:
            try:
                import json
                import struct

                blob = struct.pack(f"{len(vector)}f", *vector)
                mapping: dict = {
                    "embedding": blob,
                    "file": meta.get("file", ""),
                    "name": meta.get("name", ""),
                    "line": meta.get("line", 0),
                    "body": text,
                    "meta_json": json.dumps(meta),
                }
                self._redis_client.hset(self._redis_doc_key(key), mapping=mapping)
                return
            except Exception:
                self._use_redis = False  # fall through to JSON

        data = self._json_read()
        data[self._bucket][key] = {"vector": vector, "text": text, "meta": meta}
        self._json_write(data)

    def get_meta(self, key: str) -> "Optional[dict]":
        """Return the stored meta for *key*, or None if absent (for incremental re-index)."""
        if self._use_redis:
            try:
                import json

                raw = self._redis_client.hget(self._redis_doc_key(key), "meta_json")
                if raw is None:
                    return None
                return json.loads(raw)
            except Exception:
                self._use_redis = False

        entry = self._json_read()[self._bucket].get(key)
        return entry.get("meta") if entry else None

    def query(
        self, vector: list[float], k: int = 5, threshold: float = 0.0
    ) -> list[Neighbor]:
        """Return up to k nearest neighbors with score >= threshold, sorted descending."""
        if self._use_redis:
            try:
                return self._redis_query(vector, k, threshold)
            except Exception:
                self._use_redis = False

        return self._json_query(vector, k, threshold)

    def query_hybrid(
        self, vector: list[float], text: str, k: int = 5, filters=None
    ) -> list[Neighbor]:
        """Hybrid BM25 + vector search when a RedisVL backend is active; vector-only otherwise.

        This engine's Redis backend is RediSearch KNN (not RedisVL), so today this degrades to
        pure vector similarity — identical to the offline JSON path and to the offline behavior
        of the hybrid backend. The ``text`` body is still stored on every upsert, so wiring full
        RedisVL/BM25 hybrid later is a drop-in. ``text``/``filters`` are accepted for API parity.
        """
        return self.query(vector, k, threshold=0.0)

    def module_filter(self, module: str):
        """Build a module filter for hybrid search, or None when no filtering backend is active.

        Returns None on the vector-only path (the current default), where query_hybrid ignores
        filters; kept for API parity with the RedisVL hybrid backend.
        """
        return None

    def _redis_query(
        self, vector: list[float], k: int, threshold: float
    ) -> list[Neighbor]:
        import struct

        from redis.commands.search.query import Query

        blob = struct.pack(f"{len(vector)}f", *vector)
        q = (
            Query(f"*=>[KNN {k} @embedding $vec AS score]")
            .sort_by("score")
            .paging(0, k)
            .dialect(2)
        )
        results = self._redis_client.ft(self._index_name).search(
            q, query_params={"vec": blob}
        )
        neighbors: list[Neighbor] = []
        for doc in results.docs:
            # RediSearch returns COSINE distance (0=identical, 2=opposite).
            # Convert to similarity: similarity = 1 - distance.
            distance = float(getattr(doc, "score", 1.0))
            similarity = 1.0 - distance
            if similarity >= threshold:
                key = doc.id.replace(f"{self._index_name}:doc:", "", 1)
                raw = getattr(doc, "meta_json", "")
                if raw:
                    import json

                    meta = json.loads(raw)
                else:  # docs written before meta_json existed
                    meta = {
                        "file": getattr(doc, "file", ""),
                        "name": getattr(doc, "name", ""),
                        "line": int(getattr(doc, "line", 0)),
                    }
                neighbors.append(Neighbor(key=key, score=similarity, meta=meta))

        neighbors.sort(key=lambda n: n.score, reverse=True)
        return neighbors

    def _json_query(
        self, vector: list[float], k: int, threshold: float
    ) -> list[Neighbor]:
        data = self._json_read()
        vectors_store: dict = data.get(self._bucket, {})

        scored: list[Neighbor] = []
        for key, entry in vectors_store.items():
            stored_vec = entry.get("vector", [])
            if not stored_vec:
                continue
            score = _cosine(vector, stored_vec)
            if score >= threshold:
                scored.append(Neighbor(key=key, score=score, meta=entry.get("meta", {})))

        scored.sort(key=lambda n: n.score, reverse=True)
        return scored[:k]

    def drop(self) -> None:
        """Remove all vectors from the index."""
        if self._use_redis:
            try:
                self._redis_client.ft(self._index_name).dropindex(delete_documents=True)
                self._use_redis = False
                return
            except Exception:
                pass

        data = self._json_read()
        data[self._bucket] = {}
        self._json_write(data)
