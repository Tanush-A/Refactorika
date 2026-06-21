"""Vector index backed by RedisVL hybrid search or brute-force JSON fallback.

Index name pattern: refactorika:vec:{provider}:{dim}

Primary backend: RedisVL (`SearchIndex`) over a Redis hash, supporting both
vector-only (`VectorQuery`) and hybrid BM25 + vector (`HybridQuery`) search.

Fallback (always works, fully offline): vectors stored via the storage
`vector_*` helpers and queried with brute-force numpy cosine similarity. The
fallback drops BM25 — hybrid queries degrade to vector-only.

All redisvl imports are lazy/guarded so importing this module never fails when
redisvl (or a live Redis) is absent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from redisvl.query.filter import FilterExpression

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


_RETURN_FIELDS = ["file", "module", "name", "line", "fingerprint"]


class VectorIndex:
    """Vector similarity index with RedisVL hybrid search or JSON brute-force fallback."""

    def __init__(self, storage: "Storage") -> None:
        self._storage = storage

        # CONTRACT: provider_dim() -> (str, int). Default defensively if missing.
        try:
            from refactorika.analysis.embeddings import provider_dim

            provider, dim = provider_dim()
        except Exception:
            provider, dim = ("none", 0)
        self._provider = provider
        self._dim = int(dim)
        self._index_name = f"refactorika:vec:{provider}:{dim}"

        self._index = None
        self._use_redisvl = self._ensure_index()

    # ------------------------------------------------------------------
    # Index setup
    # ------------------------------------------------------------------

    def _ensure_index(self) -> bool:
        """Create/verify the RedisVL index. Returns False (fallback) on any problem."""
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
                    {"name": "file", "type": "tag"},
                    {"name": "module", "type": "tag"},
                    {"name": "name", "type": "tag"},
                    {"name": "fingerprint", "type": "tag"},
                    {"name": "line", "type": "numeric"},
                ],
            }
            index = SearchIndex.from_dict(
                schema, redis_url=os.environ.get("REDIS_URL")
            )
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(
        self,
        key: str,
        vector: list[float],
        meta: dict | None = None,
        *,
        text: str = "",
    ) -> None:
        """Store or update a vector with metadata (`meta`) and optional `text` body."""
        meta = meta or {}
        if self._use_redisvl:
            try:
                self._index.load(
                    [
                        {
                            "embedding": np.array(
                                vector, dtype=np.float32
                            ).tobytes(),
                            "body": text,
                            "file": meta.get("file", ""),
                            "module": meta.get("module", ""),
                            "name": meta.get("name", ""),
                            "fingerprint": meta.get("fingerprint", ""),
                            "line": int(meta.get("line", 0) or 0),
                        }
                    ],
                    keys=[self._doc_id(key)],
                )
                return
            except Exception:
                self._use_redisvl = False  # fall through to JSON

        self._storage.vector_upsert(
            key, {"vector": vector, "text": text, "meta": meta}
        )

    def query(
        self, vector: list[float], k: int = 5, threshold: float = 0.0
    ) -> list[Neighbor]:
        """Return up to k nearest neighbors with score >= threshold, sorted descending."""
        if self._use_redisvl:
            try:
                return self._redisvl_query(vector, k, threshold)
            except Exception:
                self._use_redisvl = False

        return self._json_query(vector, k, threshold)

    def query_hybrid(
        self,
        vector: list[float],
        text: str,
        k: int = 5,
        filters: "FilterExpression | None" = None,
    ) -> list[Neighbor]:
        """Hybrid BM25 + vector search. Offline fallback drops BM25 → vector-only."""
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
            )
            docs = self._index.query(hq)
            neighbors: list[Neighbor] = []
            for doc in docs:
                score = float(
                    doc.get("hybrid_score", doc.get("vector_distance", 0)) or 0
                )
                neighbors.append(
                    Neighbor(
                        key=self._strip_prefix(doc.get("id", "")),
                        score=score,
                        meta=self._doc_meta(doc),
                    )
                )
            return neighbors
        except Exception:
            self._use_redisvl = False
            return self.query(vector, k, threshold=0.0)

    def module_filter(self, module: str) -> "FilterExpression | None":
        """Build a module tag filter (or None when redisvl is unavailable)."""
        try:
            from redisvl.query.filter import Tag

            return Tag("module") == module
        except Exception:
            return None

    def drop(self) -> None:
        """Remove all vectors from the index."""
        if self._use_redisvl:
            try:
                self._index.delete(drop=True)
            except Exception:
                pass
            self._use_redisvl = False
            return

        self._storage.vector_delete_all()

    # ------------------------------------------------------------------
    # Backend query helpers
    # ------------------------------------------------------------------

    def _redisvl_query(
        self, vector: list[float], k: int, threshold: float
    ) -> list[Neighbor]:
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
            # RedisVL returns cosine distance (0=identical). similarity = 1 - distance.
            distance = float(doc.get("vector_distance", 1.0) or 1.0)
            similarity = 1.0 - distance
            if similarity >= threshold:
                neighbors.append(
                    Neighbor(
                        key=self._strip_prefix(doc.get("id", "")),
                        score=similarity,
                        meta=self._doc_meta(doc),
                    )
                )
        neighbors.sort(key=lambda n: n.score, reverse=True)
        return neighbors

    def _json_query(
        self, vector: list[float], k: int, threshold: float
    ) -> list[Neighbor]:
        vectors_store = self._storage.vector_get_all()

        scored: list[Neighbor] = []
        for key, entry in vectors_store.items():
            stored_vec = entry.get("vector", [])
            if not stored_vec:
                continue
            score = _cosine(vector, stored_vec)
            if score >= threshold:
                scored.append(
                    Neighbor(key=key, score=score, meta=entry.get("meta", {}))
                )

        scored.sort(key=lambda n: n.score, reverse=True)
        return scored[:k]
