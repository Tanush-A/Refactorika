"""Decision memory: semantic recall (offline stub embedder) + the live Redis path (fakeredis)."""

from __future__ import annotations

from refactorika.core.schema import RefactorDecision
from refactorika.core.storage import Storage
from refactorika.llm.providers import EmbeddingProvider
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.decision_memory import DecisionMemory
from refactorika.memory.vector_index import VectorIndex


class _FakeEmbed(EmbeddingProvider):
    """Deterministic 2-D embedding: domain-similar texts get the same vector.

    Any text mentioning 'discount' maps to one direction; others to the orthogonal one — so
    two differently-shaped but same-domain functions are 'semantically similar' (cosine 1.0).
    """

    name = "fake"

    def __init__(self):
        super().__init__("fake")

    def available(self) -> bool:
        return True

    def embed(self, texts):
        return [[1.0, 0.0] if "discount" in t else [0.0, 1.0] for t in texts]


def _decision(pattern: str) -> RefactorDecision:
    return RefactorDecision(pattern=pattern, transform_kind="decompose_function",
                            target="x", choice={"helper_names": ["_apply_discount"]})


def test_semantic_recall_matches_near_duplicate(tmp_path):
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    dm = DecisionMemory(storage, embed_provider=_FakeEmbed(), vector_index=VectorIndex(storage))

    # Record a decision made on function A (shape S1, mentions 'discount').
    dm.record(_decision("shapeS1"), "def a(): apply discount and tax")

    # A *different-shaped* function B (shape S2) in the same domain has no exact match,
    # but is semantically similar -> recall returns A's decision.
    got = dm.recall("def b(): compute discount differently", pattern="shapeS2")
    assert got is not None
    assert got.choice["helper_names"] == ["_apply_discount"]
    assert dm.last_match["how"] == "semantic"


def test_exact_shape_recall_is_preferred(tmp_path):
    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    dm = DecisionMemory(storage, embed_provider=_FakeEmbed(), vector_index=VectorIndex(storage))
    dm.record(_decision("shapeS1"), "discount")
    got = dm.recall("discount", pattern="shapeS1")
    assert got is not None
    assert dm.last_match["how"] == "exact-shape"


def test_no_recall_without_embeddings(tmp_path):
    class _Unavailable(_FakeEmbed):
        def available(self):
            return False

    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    dm = DecisionMemory(storage, embed_provider=_Unavailable(), vector_index=VectorIndex(storage))
    dm.record(_decision("shapeS1"), "discount")
    # different shape, embeddings off -> no recall (still correct, just not consistent)
    assert dm.recall("discount", pattern="shapeS2") is None


def test_decisions_round_trip_through_real_redis(tmp_path):
    """The live Redis path: store + retrieve a decision via a fakeredis-backed Storage."""
    import fakeredis

    storage = Storage(redis_url=None, json_path=tmp_path / "s.json")
    storage._redis = fakeredis.FakeStrictRedis(decode_responses=True)
    storage.backend = "redis"
    mem = AgentMemory(storage)

    mem.put_decision(_decision("shapeS1"))
    got = mem.get_decision("shapeS1")
    assert got is not None and got.choice["helper_names"] == ["_apply_discount"]
    # it actually lives in (fake) Redis, not the JSON file
    assert storage._redis.hexists("refactorika:memory:decisions", "shapeS1")
