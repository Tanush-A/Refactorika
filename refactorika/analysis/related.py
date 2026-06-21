"""Impact / related-code retrieval — "if I change this, what else is affected?"

Two complementary signals for a file you're about to change:
  - SEMANTIC (the hybrid index): functions elsewhere in the repo that encode
    similar logic — likely parallel implementations / copy-paste-with-drift that
    a behavior change here should probably be mirrored in. This is what catches
    "fix the bug in one place, miss the other four."
  - STRUCTURAL (the call graph): modules that directly import/call this one —
    the conventional blast radius.

Public entry point:
    find_related(path, storage, vector_index, k=5, symbol="", threshold=0.0) -> dict
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from refactorika.analysis import embeddings
from refactorika.analysis.call_graph import CallGraph, _collect_py_files, _module_name
from refactorika.analysis.parser import function_text, get_tree, iter_functions
from refactorika.memory.vector_index import _cosine

if TYPE_CHECKING:
    from refactorika.core.storage import Storage
    from refactorika.memory.vector_index import VectorIndex


def _repo_root(p: Path) -> Path:
    """Git root of the file (so impact search spans the whole project), else its dir."""
    out = subprocess.run(
        ["git", "-C", str(p.parent), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return Path(out.stdout.strip()) if out.returncode == 0 else p.parent


def _gather_functions(files: list[Path], root: Path) -> dict[str, dict]:
    """key -> {file, module, name, line, text} for every function in the repo."""
    out: dict[str, dict] = {}
    for f in files:
        try:
            source = f.read_text()
        except OSError:
            continue
        module = _module_name(f, root)
        tree = get_tree(source)
        for node, name, line in iter_functions(tree):
            key = f"{f}:{name}"
            out[key] = {
                "file": str(f),
                "module": module,
                "name": name,
                "line": line,
                "text": function_text(node, source),
            }
    return out


def find_related(
    path: str,
    storage: "Storage",
    vector_index: "VectorIndex",
    k: int = 5,
    symbol: str = "",
    threshold: float = 0.5,
) -> dict:
    """Find code semantically similar to (and structurally dependent on) `path`.

    `threshold` is the minimum cosine similarity for a function to count as
    "related" (default 0.5 — filters clearly-unrelated code while keeping
    parallel implementations).
    """
    target = Path(path).resolve()
    repo_dir = _repo_root(target)
    files, root = _collect_py_files(str(repo_dir))

    # --- structural blast radius (always available, no embeddings) ----------
    try:
        module = _module_name(target, root)
        dependents = CallGraph.build(str(repo_dir)).dependents_of(module)
    except Exception:
        dependents = []

    result: dict = {
        "path": str(target),
        "symbol": symbol or None,
        "dependents": dependents,
        "related": [],
    }

    # --- semantic neighbours (needs embeddings) -----------------------------
    if not embeddings.available():
        result["related_note"] = (
            "semantic similarity unavailable — install/configure refactorika[semantic]"
        )
        return result

    funcs = _gather_functions(files, root)
    if not funcs:
        return result

    # Embed every function once, index it (so the hybrid index is populated).
    keys = list(funcs)
    try:
        vecs = embeddings.embed([funcs[key]["text"] for key in keys])
    except Exception:
        result["related_note"] = "embedding call failed"
        return result
    for key, vec in zip(keys, vecs):
        funcs[key]["vec"] = vec
        f = funcs[key]
        vector_index.upsert(
            key, vec,
            meta={"file": f["file"], "module": f["module"], "name": f["name"], "line": f["line"]},
            text=f["text"],
        )

    # Targets = functions defined in `path` (optionally a single `symbol`).
    targets = [
        key for key, f in funcs.items()
        if Path(f["file"]).resolve() == target and (not symbol or f["name"] == symbol)
    ]

    # For each target, pull similar functions in OTHER files; keep best per neighbour.
    best: dict[str, dict] = {}
    for tkey in targets:
        tvec, ttext = funcs[tkey]["vec"], funcs[tkey]["text"]
        for n in vector_index.query_hybrid(tvec, ttext, k=k + len(targets) + 1):
            nf = funcs.get(n.key)
            if nf is None or n.key == tkey:
                continue
            if Path(nf["file"]).resolve() == target:
                continue  # same file = the thing being changed, not "other logic"
            sim = _cosine(tvec, nf["vec"])
            if sim < threshold:
                continue
            prev = best.get(n.key)
            if prev is None or sim > prev["similarity"]:
                best[n.key] = {
                    "file": nf["file"],
                    "module": nf["module"],
                    "name": nf["name"],
                    "line": nf["line"],
                    "similarity": round(sim, 4),
                    "similar_to": funcs[tkey]["name"],
                }

    result["related"] = sorted(best.values(), key=lambda r: r["similarity"], reverse=True)[:k]
    return result
