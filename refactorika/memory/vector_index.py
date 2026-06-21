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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from refactorika.core.storage import Storage


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


def _current_index_name() -> str:
    """Return the RediSearch index name based on current embedding provider/dim."""
    try:
        from refactorika.analysis import embeddings

        provider = embeddings._PROVIDER if embeddings._PROVIDER != "none" else "local"
        dim = embeddings._DIM if embeddings._DIM else 384
    except Exception:
        provider = "local"
        dim = 384
    return f"refactorika:vec:{provider}:{dim}"


def _current_dim() -> int:
    try:
        from refactorika.analysis import embeddings

        return embeddings._DIM if embeddings._DIM else 384
    except Exception:
        return 384


class VectorIndex:
    """Vector similarity index with RediSearch HNSW or JSON brute-force fallback."""

    def __init__(self, storage: "Storage") -> None:
        self._storage = storage
        self._redis_client = storage._redis  # may be None
        self._use_redis = False
        self._index_name: str = _current_index_name()

        if self._redis_client is not None:
            self._use_redis = self._ensure_redis_index()

    # ------------------------------------------------------------------
    # Redis backend helpers
    # ------------------------------------------------------------------

    def _ensure_redis_index(self) -> bool:
        """Try to create or verify the RediSearch vector index. Returns True on success."""
        try:
            from redis.commands.search.field import VectorField, TextField, NumericField
            from redis.commands.search.indexDefinition import IndexDefinition, IndexType
        except ImportError:
            return False

        try:
            dim = _current_dim()
            index_name = _current_index_name()
            self._index_name = index_name

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
        """Read the full JSON state dict from storage."""
        data = self._storage._read_json()
        if "vectors" not in data:
            data["vectors"] = {}
        return data

    def _json_write(self, data: dict) -> None:
        """Write the full JSON state dict back to storage."""
        self._storage._write_json(data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, key: str, vector: list[float], meta: dict) -> None:
        """Store or update a vector with associated metadata."""
        if self._use_redis:
            try:
                import struct

                blob = struct.pack(f"{len(vector)}f", *vector)
                mapping: dict = {
                    "embedding": blob,
                    "file": meta.get("file", ""),
                    "name": meta.get("name", ""),
                    "line": meta.get("line", 0),
                }
                self._redis_client.hset(self._redis_doc_key(key), mapping=mapping)
                return
            except Exception:
                self._use_redis = False  # fall through to JSON

        data = self._json_read()
        data["vectors"][key] = {"vector": vector, "meta": meta}
        self._json_write(data)

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
        vectors_store: dict = data.get("vectors", {})

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
        data["vectors"] = {}
        self._json_write(data)
