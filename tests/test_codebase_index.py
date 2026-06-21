"""Semantic codebase index: embedding, incremental skip, neighbor search, namespace isolation.

Uses a deterministic offline fake embedder (no sentence-transformers needed) so the logic is
tested without the heavyweight 'semantic' extra.
"""

from __future__ import annotations

from refactorika.core.schema import RefactorDecision
from refactorika.core.storage import Storage
from refactorika.graph.resolver import build_graph
from refactorika.llm.providers import EmbeddingProvider
from refactorika.memory.codebase_index import (
    build_codebase_index,
    codebase_vector_index,
    similar_symbols,
)
from refactorika.memory.decision_memory import DecisionMemory
from refactorika.memory.vector_index import VectorIndex

# Two functions about discounts should be neighbors; the unrelated one should not.
_SRC = '''\
def apply_discount(price, pct):
    """Apply a discount."""
    return price - price * pct


def compute_discount(total, rate):
    """Compute a discount amount."""
    return total * rate


def render_banner(text):
    return "*** " + text + " ***"
'''


class _FakeEmbed(EmbeddingProvider):
    """'discount'-mentioning code -> one direction; everything else -> orthogonal."""

    name = "fake"

    def __init__(self):
        super().__init__("fake")

    def available(self) -> bool:
        return True

    def embed(self, texts):
        return [[1.0, 0.0] if "discount" in t.lower() else [0.0, 1.0] for t in texts]


def _repo(tmp_path):
    (tmp_path / "shop.py").write_text(_SRC)
    return build_graph(str(tmp_path))


def test_index_embeds_every_function(tmp_path):
    g = _repo(tmp_path)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    vi = codebase_vector_index(storage, embed_provider=_FakeEmbed())

    stats = build_codebase_index(g, str(tmp_path), vi, embed_provider=_FakeEmbed())
    assert stats.available is True
    assert stats.embedded == 3  # three top-level functions
    assert stats.skipped == 0


def test_incremental_skip_on_unchanged(tmp_path):
    g = _repo(tmp_path)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    fake = _FakeEmbed()
    vi = codebase_vector_index(storage, fake)
    build_codebase_index(g, str(tmp_path), vi, embed_provider=fake)

    # Re-index the unchanged repo: every symbol's sha matches -> all skipped.
    stats = build_codebase_index(
        g, str(tmp_path), codebase_vector_index(storage, fake), embed_provider=fake
    )
    assert stats.embedded == 0
    assert stats.skipped == 3


def test_similar_symbols_finds_domain_peer(tmp_path):
    g = _repo(tmp_path)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    fake = _FakeEmbed()
    vi = codebase_vector_index(storage, fake)
    build_codebase_index(g, str(tmp_path), vi, embed_provider=fake)

    target = next(q for q in g.symbols if q.endswith("apply_discount"))
    hits = similar_symbols(target, g, vi, embed_provider=fake, k=5, threshold=0.5)
    names = [h.meta.get("qualname", h.key) for h in hits]

    assert target not in names  # excludes itself
    assert any(n.endswith("compute_discount") for n in names)  # same-domain peer
    assert not any(n.endswith("render_banner") for n in names)  # orthogonal, below threshold


def test_unavailable_provider_is_noop(tmp_path):
    class _Down(_FakeEmbed):
        def available(self) -> bool:
            return False

    g = _repo(tmp_path)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    stats = build_codebase_index(
        g, str(tmp_path), codebase_vector_index(storage, _Down()), embed_provider=_Down()
    )
    assert stats.available is False
    assert stats.embedded == 0


def test_codebase_vectors_do_not_pollute_decision_recall(tmp_path):
    """The codebase namespace must be disjoint from decision vectors, or recall breaks."""
    g = _repo(tmp_path)
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    fake = _FakeEmbed()

    # Index the codebase (namespace='codebase').
    cb = codebase_vector_index(storage, fake)
    build_codebase_index(g, str(tmp_path), cb, embed_provider=fake)

    # Decision memory uses the default namespace. Recall on a 'discount' query must return the
    # recorded decision, not a codebase symbol (which would make get_decision miss).
    dm = DecisionMemory(storage, embed_provider=fake, vector_index=VectorIndex(storage))
    dm.record(
        RefactorDecision(pattern="decompose:abc", transform_kind="decompose_function",
                         target="shop.apply_discount", choice={"helper_names": ["_apply_pct"]}),
        "discount logic",
    )
    found = dm.recall("a function about discount", "decompose:zzz")  # no exact match
    assert found is not None
    assert found.choice["helper_names"] == ["_apply_pct"]
