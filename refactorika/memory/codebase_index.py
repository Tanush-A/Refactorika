"""Semantic codebase index — embed every symbol into the vector store, in Redis.

This is the *semantic* layer that sits on top of the exact Jedi dependency graph. The graph
answers structural questions precisely ("who calls X", "what breaks if I change X"); embeddings
answer the orthogonal question the graph is blind to: "what code is *like* this". We index one
vector per symbol (function/method/class), keyed by qualname, with the dependency context folded
into the embedded text so similarity reflects a little structure too.

It feeds **judgment**, never correctness: decompose-prompt context and consistency recall read it,
but nothing in the verified spine depends on it. Fully degrades when no embedding provider is
available (no-op) and when there's no Redis (the vector index's JSON fallback).

Incremental: each symbol's source is hashed; unchanged symbols are skipped on re-index so we don't
re-embed the whole repo every run (the graph is rebuilt per pipeline item).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from refactorika.analysis.parser import function_text, get_tree
from refactorika.graph.model import KIND_MODULE, Graph

if TYPE_CHECKING:
    from refactorika.core.storage import Storage
    from refactorika.llm.providers import EmbeddingProvider
    from refactorika.memory.vector_index import Neighbor, VectorIndex

# Codebase symbols live in their own vector space so they never collide with the
# decision-memory vectors (both use VectorIndex; recall assumes its nearest neighbor is a
# decision, so the two stores must be disjoint).
_NAMESPACE = "codebase"


def codebase_vector_index(
    storage: "Storage", embed_provider: "Optional[EmbeddingProvider]" = None
) -> "VectorIndex":
    """The VectorIndex for codebase symbols (namespaced away from decision vectors)."""
    from refactorika.memory.vector_index import VectorIndex

    return VectorIndex(storage, embed_provider=embed_provider, namespace=_NAMESPACE)


@dataclass
class IndexStats:
    embedded: int = 0       # symbols (re)embedded this run
    skipped: int = 0        # unchanged, skipped
    total: int = 0          # candidate symbols
    available: bool = True  # was an embedding provider usable


def _source_of(graph: Graph) -> dict[str, str]:
    """Map qualname -> source text for every non-module symbol, via tree-sitter.

    Symbols are matched to AST nodes by (name, 1-based line) — the convention the resolver
    records — which disambiguates same-named methods across classes.
    """
    by_file: dict[str, list] = {}
    for q, s in graph.symbols.items():
        if s.kind != KIND_MODULE:
            by_file.setdefault(s.file, []).append((q, s))

    out: dict[str, str] = {}
    for file, syms in by_file.items():
        try:
            source = Path(file).read_text(encoding="utf-8")
            tree = get_tree(source)
        except Exception:
            continue
        nodes: dict[tuple[str, int], object] = {}

        def _walk(node) -> None:
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    name = child.child_by_field_name("name")
                    if name is not None and name.text:
                        nodes[(name.text.decode(), child.start_point[0] + 1)] = child
                _walk(child)

        _walk(tree.root_node)
        for q, s in syms:
            node = nodes.get((s.name, s.line))
            if node is not None:
                out[q] = function_text(node, source)
    return out


def _embed_text(graph: Graph, qual: str, source: str) -> str:
    """Source plus a compact dependency-context header, so similarity carries some structure."""
    def short(qn: str) -> str:
        return qn.rsplit(".", 1)[-1]

    sym = graph.symbols[qual]
    calls = sorted(short(d) for d in graph.outgoing(qual))[:8]
    callers = sorted(short(s) for s in graph.incoming(qual))[:8]
    header = f"# {sym.kind} {qual}\n"
    if calls:
        header += f"# calls: {', '.join(calls)}\n"
    if callers:
        header += f"# called by: {', '.join(callers)}\n"
    return header + source


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_codebase_index(
    graph: Graph,
    root: str,
    vectors: "VectorIndex",
    embed_provider: "Optional[EmbeddingProvider]" = None,
) -> IndexStats:
    """Embed every (changed) symbol into *vectors*. No-op when embeddings are unavailable."""
    from refactorika.llm.providers import get_embedding_provider

    provider = embed_provider or get_embedding_provider()
    sources = _source_of(graph)
    stats = IndexStats(total=len(sources), available=provider.available())
    if not stats.available:
        return stats

    pending_keys: list[str] = []
    pending_texts: list[str] = []
    pending_meta: list[dict] = []
    for qual, source in sources.items():
        text = _embed_text(graph, qual, source)
        sha = _sha(source)  # hash the code, not the context header (context is a minor signal)
        prior = vectors.get_meta(qual)
        if prior and prior.get("sha") == sha:
            stats.skipped += 1
            continue
        sym = graph.symbols[qual]
        pending_keys.append(qual)
        pending_texts.append(text)
        pending_meta.append({
            "qualname": qual, "name": sym.name, "kind": sym.kind,
            "file": sym.file, "line": sym.line, "sha": sha,
        })

    if not pending_texts:
        return stats

    embedded = provider.embed(pending_texts)
    if embedded is None:  # provider went unavailable mid-run
        stats.available = False
        return stats
    for key, vec, meta in zip(pending_keys, embedded, pending_meta):
        vectors.upsert(key, vec, meta)
        stats.embedded += 1
    return stats


def similar_symbols(
    qualname: str,
    graph: Graph,
    vectors: "VectorIndex",
    embed_provider: "Optional[EmbeddingProvider]" = None,
    k: int = 5,
    threshold: float = 0.0,
) -> "list[Neighbor]":
    """Nearest semantic neighbors of *qualname* (excluding itself). Empty if unavailable."""
    from refactorika.llm.providers import get_embedding_provider

    provider = embed_provider or get_embedding_provider()
    if not provider.available() or qualname not in graph.symbols:
        return []
    source = _source_of(graph).get(qualname)
    if source is None:
        return []
    vecs = provider.embed([_embed_text(graph, qualname, source)])
    if not vecs:
        return []
    hits = vectors.query(vecs[0], k=k + 1, threshold=threshold)
    return [n for n in hits if n.key != qualname][:k]
