"""Structure-aware analysis via tree-sitter. Read-only; results cached on AST signature."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from .schema import AnalysisResult, Opportunity
from .storage import Storage

_PY_LANGUAGE = Language(tspython.language())

# Thresholds tuned for "simple codebases" — generous enough not to nag tidy files.
MAX_FILE_LINES = 150
MAX_FUNC_LINES = 30
MAX_NESTING = 3

_NESTERS = {"if_statement", "for_statement", "while_statement", "with_statement", "try_statement"}


def _signature(content: str) -> str:
    return hashlib.sha1(content.encode()).hexdigest()


def _funcs(node: Node):
    if node.type == "function_definition":
        yield node
    for c in node.children:
        yield from _funcs(c)


def _max_depth(node: Node, depth: int = 0) -> int:
    best = depth
    for c in node.children:
        nd = depth + 1 if c.type in _NESTERS else depth
        best = max(best, _max_depth(c, nd))
    return best


def _func_name(node: Node) -> str:
    n = node.child_by_field_name("name")
    return n.text.decode() if n else "<anonymous>"


def _import_lines(root: Node) -> list[tuple[int, str]]:
    out = []
    for c in root.children:
        if c.type in ("import_statement", "import_from_statement"):
            out.append((c.start_point[0] + 1, c.text.decode()))
    return out


def _module_of(stmt: str) -> str:
    parts = stmt.split()
    if parts and parts[0] == "from":
        return parts[1].split(".")[0]
    if parts and parts[0] == "import":
        return parts[1].split(".")[0]
    return ""


def _bucket(mod: str) -> int:
    """0 = stdlib, 1 = third-party, 2 = local (heuristic)."""
    if mod in sys.stdlib_module_names:
        return 0
    if mod.startswith(".") or mod == "":
        return 2
    return 1


def _analyze(content: str, file: str) -> AnalysisResult:
    tree = Parser(_PY_LANGUAGE).parse(content.encode())
    root = tree.root_node
    opps: list[Opportunity] = []

    n_lines = content.count("\n") + 1
    if n_lines > MAX_FILE_LINES:
        opps.append(
            Opportunity("split_module", file, f"{n_lines} lines (> {MAX_FILE_LINES})", rank=n_lines)
        )

    imports = _import_lines(root)
    seen: set[str] = set()
    dupes: list[str] = []
    for _, stmt in imports:
        if stmt in seen:
            dupes.append(stmt)
        seen.add(stmt)
    buckets = [_bucket(_module_of(s)) for _, s in imports]
    unordered = any(buckets[i] > buckets[i + 1] for i in range(len(buckets) - 1))
    if dupes or unordered:
        why = []
        if dupes:
            why.append(f"{len(dupes)} duplicate(s)")
        if unordered:
            why.append("not stdlib->third-party->local")
        opps.append(Opportunity("reorder_imports", file, "; ".join(why), rank=40))

    for fn in _funcs(root):
        name = _func_name(fn)
        span = fn.end_point[0] - fn.start_point[0] + 1
        loc = f"{name} (line {fn.start_point[0] + 1})"
        if span > MAX_FUNC_LINES:
            opps.append(
                Opportunity("split_function", loc, f"{span} lines (> {MAX_FUNC_LINES})", rank=span)
            )
        depth = _max_depth(fn)
        if depth > MAX_NESTING:
            opps.append(
                Opportunity("flatten_nesting", loc, f"nesting depth {depth} (> {MAX_NESTING})", rank=depth * 20)
            )

    opps.sort(key=lambda o: o.rank, reverse=True)
    return AnalysisResult(file=file, opportunities=opps)


def analyze_file(path: str, storage: Optional[Storage] = None) -> AnalysisResult:
    content = Path(path).read_text()
    key = _signature(content)
    if storage:
        cached = storage.cache_get(key)
        if cached:
            return AnalysisResult(
                file=cached["file"],
                opportunities=[Opportunity(**o) for o in cached["opportunities"]],
            )
    result = _analyze(content, path)
    if storage:
        storage.cache_set(key, result.to_dict())
    return result
