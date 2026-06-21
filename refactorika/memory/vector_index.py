"""Vector index: RedisVL hybrid (BM25 + vector) when live, brute-force JSON fallback offline.

Index identity comes from the active embedding provider (name + dim) plus an optional
``namespace``, so independent vector spaces (decision memory vs the codebase index) never
collide. Primary backend is RedisVL (`SearchIndex`) supporting vector-only (`VectorQuery`) and
hybrid BM25 + vector (`HybridQuery`, RRF). Offline (no redisvl / no live Redis) it stores vectors
in per-namespace JSON buckets and queries with numpy cosine; hybrid degrades to vector-only.

All redisvl imports are lazy/guarded so importing this module never fails when redisvl (or a
live Redis) is absent.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

# RedisVL's FT.HYBRID surface warns "experimental" on every query; silence it for clean output.
warnings.filterwarnings("ignore", message=r".*[Ee]xperimental.*")

if TYPE_CHECKING:
    from redisvl.query.filter import FilterExpression

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
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b + 1e-9)


# Bump when the index schema changes so a fresh index is created (old one orphaned).
_SCHEMA_VERSION = "v2"
# HybridQuery results don't carry the doc id, so we store the upsert key as its own field.
_RETURN_FIELDS = ["key", "file", "module", "name", "line", "fingerprint"]


def _provider_name_and_dim(embed_provider: "Optional[EmbeddingProvider]") -> tuple[str, int]:
    """(name, dim) for the active embedding provider — the single source of index identity.

    Falls back to ("local", 384) so the index name is stable even when no provider is importable
    (the JSON fallback path doesn't care about the name).
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
    """Vector similarity index: RedisVL hybrid when live, JSON brute-force fallback otherwise."""

    def __init__(
        self,
        storage: "Storage",
        embed_provider: "Optional[EmbeddingProvider]" = None,
        namespace: str = "",
    ) -> None:
        self._storage = storage
        self._provider_name, self._dim = _provider_name_and_dim(embed_provider)
        # A namespace gives an independent vector space (e.g. "codebase" symbols must not collide
        # with decision-memory vectors, which share this class but a logically separate store).
        suffix = f":{namespace}" if namespace else ""
        self._index_name = (
            f"refactorika:vec:{_SCHEMA_VERSION}:{self._provider_name}:{self._dim}{suffix}"
        )
        self._bucket = f"vectors:{namespace}" if namespace else "vectors"
        self._index = None
        self._use_redisvl = self._ensure_index()

    # ------------------------------------------------------------------ Redis (RedisVL) backend
    def _ensure_index(self) -> bool:
        """Create/verify the RedisVL index. Returns False (JSON fallback) on any problem."""
        if self._storage._redis is None:
            return False
        try:
            from redisvl.index import SearchIndex
        except Exception:
            return False
        try:
            schema = {
                "index": {
                    "name": self._index_name,
                    "prefix": f"{self._index_name}:doc",
                    "storage_type": "hash",
                },
                "fields": [
                    {
                        "name": "embedding",
                        "type": "vector",
                        "attrs": {
                            "dims": self._dim,
                            "distance_metric": "cosine",
                            "algorithm": "hnsw",
                            "datatype": "float32",
                        },
                    },
                    {"name": "body", "type": "text"},
                    {"name": "key", "type": "tag"},
                    {"name": "file", "type": "tag"},
                    {"name": "module", "type": "tag"},
                    {"name": "name", "type": "tag"},
                    {"name": "fingerprint", "type": "tag"},
                    {"name": "line", "type": "numeric"},
                ],
            }
            index = SearchIndex.from_dict(schema, redis_url=os.environ.get("REDIS_URL"))
            index.create(overwrite=False)
            self._index = index
            return True
        except Exception:
            return False

    def _doc_id(self, key: str) -> str:
        return f"{self._index_name}:doc:{key}"

    def _strip_prefix(self, doc_id: str) -> str:
        prefix = f"{self._index_name}:doc:"
        return doc_id.replace(prefix, "", 1) if doc_id.startswith(prefix) else doc_id

    @staticmethod
    def _doc_meta(doc: dict) -> dict:
        return {
            "file": doc.get("file", ""),
            "module": doc.get("module", ""),
            "name": doc.get("name", ""),
            "line": int(doc.get("line", 0) or 0),
            "fingerprint": doc.get("fingerprint", ""),
        }

    # ------------------------------------------------------------------ JSON fallback helpers
    def _json_read(self) -> dict:
        data = self._storage._read_json()
        if self._bucket not in data:
            data[self._bucket] = {}
        return data

    def _json_write(self, data: dict) -> None:
        self._storage._write_json(data)

    # ------------------------------------------------------------------ Public API
    def upsert(
        self, key: str, vector: list[float], meta: "Optional[dict]" = None, *, text: str = ""
    ) -> None:
        """Store/update a vector with metadata and an optional text body (for BM25/hybrid)."""
        meta = meta or {}
        if self._use_redisvl:
            try:
                import numpy as np

                self._index.load(
                    [{
                        "embedding": np.array(vector, dtype=np.float32).tobytes(),
                        "body": text,
                        "key": key,
                        "file": meta.get("file", ""),
                        "module": meta.get("module", ""),
                        "name": meta.get("name", ""),
                        "fingerprint": meta.get("fingerprint", ""),
                        "line": int(meta.get("line", 0) or 0),
                    }],
                    keys=[self._doc_id(key)],
                )
                return
            except Exception:
                self._use_redisvl = False  # fall through to JSON

        data = self._json_read()
        data[self._bucket][key] = {"vector": vector, "text": text, "meta": meta}
        self._json_write(data)

    def get_meta(self, key: str) -> "Optional[dict]":
        """Stored meta for *key*, or None (for incremental re-index).

        On the live RedisVL path this returns None (each live run re-indexes); the JSON fallback
        returns the full stored meta, preserving incremental behavior offline and in tests.
        """
        if self._use_redisvl:
            return None
        entry = self._json_read()[self._bucket].get(key)
        return entry.get("meta") if entry else None

    def query(self, vector: list[float], k: int = 5, threshold: float = 0.0) -> list[Neighbor]:
        """Up to k nearest neighbors with score >= threshold, sorted descending."""
        if self._use_redisvl:
            try:
                return self._redisvl_query(vector, k, threshold)
            except Exception:
                self._use_redisvl = False
        return self._json_query(vector, k, threshold)

    def query_hybrid(
        self, vector: list[float], text: str, k: int = 5,
        filters: "FilterExpression | None" = None,
    ) -> list[Neighbor]:
        """Hybrid BM25 + vector (RRF) when RedisVL is live; vector-only fallback otherwise."""
        if not self._use_redisvl:
            return self.query(vector, k, threshold=0.0)
        try:
            from redisvl.query import HybridQuery

            hq = HybridQuery(
                text=text,
                text_field_name="body",
                vector=vector,
                vector_field_name="embedding",
                combination_method="RRF",
                text_scorer="BM25STD",
                filter_expression=filters,
                num_results=k,
                return_fields=_RETURN_FIELDS,
                yield_combined_score_as="hybrid_score",
                stopwords=None,  # code identifiers aren't English stopwords; don't require nltk
            )
            docs = self._index.query(hq)
            neighbors: list[Neighbor] = []
            for doc in docs:
                score = float(doc.get("hybrid_score", doc.get("vector_distance", 0)) or 0)
                neighbors.append(Neighbor(
                    key=doc.get("key") or self._strip_prefix(doc.get("id", "")),
                    score=score, meta=self._doc_meta(doc),
                ))
            return neighbors
        except Exception:
            # Hybrid failed but the index is live — fall back to vector-only on the SAME index.
            try:
                return self._redisvl_query(vector, k, threshold=0.0)
            except Exception:
                self._use_redisvl = False
                return self.query(vector, k, threshold=0.0)

    def module_filter(self, module: str) -> "FilterExpression | None":
        """A module tag filter for hybrid search (or None when redisvl is unavailable)."""
        try:
            from redisvl.query.filter import Tag

            return Tag("module") == module
        except Exception:
            return None

    def drop(self) -> None:
        """Remove all vectors from this index/namespace."""
        if self._use_redisvl:
            try:
                self._index.delete(drop=True)
            except Exception:
                pass
            self._use_redisvl = False
            return
        data = self._json_read()
        data[self._bucket] = {}
        self._json_write(data)

    # ------------------------------------------------------------------ backend query helpers
    def _redisvl_query(self, vector: list[float], k: int, threshold: float) -> list[Neighbor]:
        from redisvl.query import VectorQuery

        vq = VectorQuery(
            vector=vector,
            vector_field_name="embedding",
            num_results=k,
            return_fields=_RETURN_FIELDS,
            return_score=True,
        )
        docs = self._index.query(vq)
        neighbors: list[Neighbor] = []
        for doc in docs:
            distance = float(doc.get("vector_distance", 1.0) or 1.0)
            similarity = 1.0 - distance  # RedisVL returns cosine distance (0=identical)
            if similarity >= threshold:
                neighbors.append(Neighbor(
                    key=doc.get("key") or self._strip_prefix(doc.get("id", "")),
                    score=similarity, meta=self._doc_meta(doc),
                ))
        neighbors.sort(key=lambda n: n.score, reverse=True)
        return neighbors

    def _json_query(self, vector: list[float], k: int, threshold: float) -> list[Neighbor]:
        vectors_store = self._json_read().get(self._bucket, {})
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
