"""Decision memory — the shared brain that keeps refactoring consistent across a repo.

Each refactor decision (smell shape -> transform applied -> helper names chosen) is stored in
Redis (agent memory) and indexed by an embedding of the code it acted on. Before deciding on a
new node, the engine recalls *semantically similar* prior decisions and reuses the naming — so
the 2nd, 5th, Nth similar function is handled the same way. This generalizes the exact-shape
match: near-duplicates (not just identical functions) now share conventions, which mirrors
RefactorBench's state-awareness finding.

Backends: Redis (live, via the vector index + agent-memory hashes) with a local-JSON fallback.
Recall resolves exact structural matches first (cheap, precise), then semantic similarity when
an embedding provider is available; with neither, it degrades to no recall (still correct).
"""

from __future__ import annotations

from typing import Optional

from refactorika.core.schema import RefactorDecision
from refactorika.core.storage import Storage
from refactorika.llm.providers import EmbeddingProvider, get_embedding_provider
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.vector_index import VectorIndex

# Cosine similarity above which a prior decision is considered "the same situation".
_DEFAULT_THRESHOLD = 0.86


class DecisionMemory:
    def __init__(
        self,
        storage: Storage,
        agent_memory: Optional[AgentMemory] = None,
        embed_provider: Optional[EmbeddingProvider] = None,
        vector_index: Optional[VectorIndex] = None,
        threshold: float = _DEFAULT_THRESHOLD,
    ):
        self.storage = storage
        self.agent = agent_memory or AgentMemory(storage)
        self.embed = embed_provider or get_embedding_provider()
        self.vectors = vector_index or VectorIndex(storage)
        self.threshold = threshold
        self.semantic = self.embed.available()
        self.last_match: Optional[dict] = None  # what recall() returned last (for --show-memory)

    def record(self, decision: RefactorDecision, embed_text: str) -> None:
        """Persist a decision and index it by the embedding of the code it acted on."""
        self.agent.put_decision(decision)
        if self.semantic:
            vecs = self.embed.embed([embed_text])
            if vecs:
                self.vectors.upsert(decision.pattern, vecs[0], {"name": decision.pattern})

    def recall(self, embed_text: str, pattern: str) -> Optional[RefactorDecision]:
        """Return the most relevant prior decision (exact shape first, then semantic), or None."""
        exact = self.agent.get_decision(pattern)
        if exact is not None:
            self.last_match = {"how": "exact-shape", "pattern": pattern, "score": 1.0}
            return exact
        if self.semantic:
            vecs = self.embed.embed([embed_text])
            if vecs:
                hits = self.vectors.query(vecs[0], k=1, threshold=self.threshold)
                if hits:
                    found = self.agent.get_decision(hits[0].key)
                    if found is not None:
                        self.last_match = {"how": "semantic", "pattern": hits[0].key,
                                           "score": round(hits[0].score, 3)}
                        return found
        self.last_match = None
        return None

    def all_decisions(self) -> list[RefactorDecision]:
        return self.agent.all_decisions()
