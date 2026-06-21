"""Context Retriever: structured + vector lookups for module context."""

from __future__ import annotations

from refactorika.core.storage import Storage
from refactorika.memory.agent_memory import AgentMemory


class ContextRetriever:
    def __init__(self, storage: Storage, agent_memory: AgentMemory) -> None:
        self._storage = storage
        self._memory = agent_memory
        # VectorIndex is optional — imported lazily so missing [semantic] doesn't crash.
        self._vector_index = None

    def _get_vector_index(self):
        if self._vector_index is not None:
            return self._vector_index
        try:
            from refactorika.memory.vector_index import VectorIndex  # noqa: PLC0415
            self._vector_index = VectorIndex(self._storage)
        except Exception:
            self._vector_index = None
        return self._vector_index

    def relevant(self, module: str, k: int = 3) -> list[dict]:
        """Return up to k related modules by vector similarity (or name proximity fallback)."""
        ctx = self._memory.get_context(module)
        if ctx is None:
            return []

        vi = self._get_vector_index()
        if vi is not None:
            try:
                from refactorika.analysis.embeddings import available, embed_one  # noqa: PLC0415
                if available():
                    summary = f"{ctx.purpose_hint} {' '.join(e.name for e in ctx.exports)}"
                    vec = embed_one(summary)
                    neighbors = vi.query(vec, k=k + 1, threshold=0.0)
                    results = []
                    for n in neighbors:
                        meta = n.meta
                        mod = meta.get("module", "")
                        if mod and mod != module:
                            results.append({"module": mod, "score": round(n.score, 4)})
                        if len(results) >= k:
                            break
                    return results
            except Exception:
                pass

        # Fallback: return modules sharing a prefix or that depend on this one.
        all_ctxs = self._memory.all_contexts()
        results = []
        for mod, other_ctx in all_ctxs.items():
            if mod == module:
                continue
            if module in other_ctx.dependents or mod.split(".")[0] == module.split(".")[0]:
                results.append({"module": mod, "score": 0.5})
        return results[:k]

    def conventions(self, path: str) -> dict:
        """Return observed import/naming conventions for a module (best-effort)."""
        try:
            from refactorika.analysis.parser import get_tree, iter_imports  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415
            source = Path(path).read_text()
            tree = get_tree(source)
            imports = list(iter_imports(tree))
            stdlib = [m for m, _ in imports if _is_stdlib(m)]
            third_party = [m for m, _ in imports if not _is_stdlib(m) and "." not in m]
            return {"stdlib": stdlib, "third_party": third_party, "import_count": len(imports)}
        except Exception:
            return {}

    def dependents(self, module: str, root: str | None = None) -> list[str]:
        """Return module names that depend on *module*, computed from the call graph.

        Reads the actual code (imports + cross-module references) via
        ``analysis.call_graph`` rather than trusting stored ``ctx.dependents``
        (which is circular and empty on a fresh repo). Best-effort: if the graph
        can't be built/resolved, falls back to the stored agent-memory view.
        """
        if root:
            try:
                deps = self._dependents_from_call_graph(module, root)
                if deps is not None:
                    return deps
            except Exception:
                pass

        # Fallback: stored agent-memory view (may be empty on a fresh repo).
        all_ctxs = self._memory.all_contexts()
        return [mod for mod, ctx in all_ctxs.items() if module in ctx.dependents]

    def _dependents_from_call_graph(self, module: str, root: str) -> list[str] | None:
        """Modules importing/referencing *module* (by final segment), via call graph."""
        from refactorika.analysis.call_graph import CallGraph  # noqa: PLC0415

        return CallGraph.build(root).dependents_of(module)


def _is_stdlib(module: str) -> bool:
    import sys  # noqa: PLC0415
    top = module.split(".")[0]
    return top in sys.stdlib_module_names
