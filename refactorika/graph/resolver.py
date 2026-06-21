"""Build a reference-correct symbol graph via Jedi static analysis.

This replaces the old ``analysis/call_graph.py`` heuristic (which resolved a call to
"any symbol whose unqualified name matches" — a documented false-positive source).
Here every reference is resolved with real name binding: Jedi follows imports,
aliases, scopes, and `self`/method dispatch to the *actual* definition. That is what
lets a downstream rename or dead-code removal touch exactly the right sites.

Entry points (reachability anchors for dead-code analysis) are detected with a small
textual pass: public module-level symbols, ``__all__``, ``if __name__ == "__main__"``
callees, test functions/files, and registration decorators.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import jedi

from refactorika.graph.model import (
    KIND_CLASS,
    KIND_FUNCTION,
    KIND_METHOD,
    KIND_MODULE,
    Graph,
    Symbol,
)

_SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "build", "dist", ".tox", "node_modules",
}

# Decorators that register a symbol with a framework — reached externally, treat as live.
_ENTRY_DECORATOR_HINTS = (
    "route", "command", "fixture", "task", "get", "post", "put", "delete",
    "patch", "websocket", "validator", "register", "cli", "rule", "callback",
)


# --------------------------------------------------------------------------- files
def collect_py_files(path: str) -> tuple[list[Path], Path]:
    """Return (list of .py files, root dir used for module naming)."""
    p = Path(path)
    if p.is_file():
        return [p.resolve()], p.resolve().parent
    root = p.resolve()
    files: list[Path] = []
    for f in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        files.append(f.resolve())
    return files, root


def module_name(file_path: Path, root: Path) -> str:
    """Dotted module name from a file path relative to root."""
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        rel = Path(file_path.name)
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else file_path.stem


# ---------------------------------------------------------------- textual heuristics
def _all_exports(source: str) -> set[str]:
    m = re.search(r"__all__\s*=\s*[\[(]([^\])]*)[\])]", source)
    if not m:
        return set()
    return set(re.findall(r'["\']([A-Za-z_]\w*)["\']', m.group(1)))


def _main_block_calls(source: str) -> set[str]:
    m = re.search(r'if\s+__name__\s*==\s*["\']__main__["\']\s*:(.*)', source, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", m.group(1)))


def _is_test_file(file_path: Path) -> bool:
    n = file_path.name
    return n.startswith("test_") or n.endswith("_test.py") or n == "conftest.py"


def _looks_like_entry_decorator(decorators: list[str]) -> bool:
    for d in decorators:
        tail = d.split(".")[-1].split("(")[0].strip().lower()
        if any(h in tail for h in _ENTRY_DECORATOR_HINTS):
            return True
    return False


# ------------------------------------------------------------------- jedi helpers
def _enclosing_qualname(name: Any) -> Optional[str]:
    """Module-qualified name of the nearest enclosing function/class, or None (module scope)."""
    try:
        par = name.parent()
    except Exception:
        return None
    while par is not None:
        try:
            if par.type in ("function", "class"):
                return par.full_name or None
            par = par.parent()
        except Exception:
            return None
    return None


def _decorators_for(name: Any) -> list[str]:
    """Best-effort decorator texts for a definition Name (scans the lines above it)."""
    # Jedi doesn't expose decorators directly; scan the lines just above the def.
    try:
        lines = (name.get_line_code(before=4, after=0) or "").splitlines()
    except Exception:
        return []
    decos = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("@"):
            decos.append(s[1:].strip())
    return decos


# -------------------------------------------------------------------------- builder
def build_graph(path: str) -> Graph:
    """Parse every .py file under *path* and construct the reference-correct graph."""
    files, root = collect_py_files(path)
    project = jedi.Project(path=str(root))
    graph = Graph()

    # (abspath, line) -> qualname, and full_name -> qualname, for resolving goto targets.
    def_index: dict[tuple[str, int], str] = {}
    fullname_index: dict[str, str] = {}
    abspaths: set[str] = set()
    scripts: dict[str, Any] = {}  # module -> jedi.Script
    sources: dict[str, str] = {}  # module -> source

    # ---- Pass 1: symbols (nodes) ----
    for fpath in files:
        ap = str(fpath)
        abspaths.add(ap)
        mod = module_name(fpath, root)
        try:
            source = fpath.read_text(encoding="utf-8", errors="replace")
            script = jedi.Script(code=source, path=ap, project=project)
        except Exception:
            continue
        scripts[mod] = script
        sources[mod] = source

        # module node
        graph.add_symbol(Symbol(qualname=mod, name=mod.split(".")[-1], kind=KIND_MODULE,
                                file=ap, line=1, is_exported=True))

        exports = _all_exports(source)
        try:
            defs = script.get_names(all_scopes=True, definitions=True, references=False)
        except Exception:
            defs = []
        for n in defs:
            if n.type not in ("function", "class"):
                continue
            # Skip imported names (e.g. `from typing import Optional`): their full_name
            # belongs to another module. Only node-ify definitions owned by this module.
            if not (n.full_name and n.full_name.startswith(mod + ".")):
                continue
            qual = n.full_name or f"{mod}.{n.name}"
            scope = _enclosing_qualname(n)
            scope_is_class = bool(scope and scope in graph.symbols
                                  and graph.symbols[scope].kind == KIND_CLASS)
            if n.type == "class":
                kind = KIND_CLASS
            else:
                kind = KIND_METHOD if scope_is_class else KIND_FUNCTION
            decos = _decorators_for(n)
            is_private = n.name.startswith("_")
            sym = Symbol(
                qualname=qual, name=n.name, kind=kind, file=ap, line=n.line,
                column=n.column, scope=scope, is_private=is_private,
                is_exported=(n.name in exports) or (scope is None and not is_private),
                decorators=decos,
            )
            graph.add_symbol(sym)
            def_index[(ap, n.line)] = qual
            fullname_index[qual] = qual

    # ---- Entry points (reachability anchors) ----
    for mod, script in scripts.items():
        source = sources[mod]
        fpath = Path(graph.symbols[mod].file)
        exports = _all_exports(source)
        main_calls = _main_block_calls(source)
        is_test = _is_test_file(fpath)
        for qual, sym in list(graph.symbols.items()):
            if sym.file != str(fpath) or sym.kind == KIND_MODULE:
                continue
            anchor = (
                (sym.scope is None and not sym.is_private)          # public top-level API
                or sym.name in exports                              # __all__
                or sym.name in main_calls                           # __main__ callee
                or sym.name.startswith("test_")                     # test function
                or is_test                                         # anything in a test file
                or _looks_like_entry_decorator(sym.decorators)      # registration decorator
            )
            if anchor:
                graph.add_entry_point(qual)

    # ---- Pass 2: edges (references) ----
    for mod, script in scripts.items():
        try:
            refs = script.get_names(all_scopes=True, definitions=False, references=True)
        except Exception:
            refs = []
        for n in refs:
            # Edges are symbol->symbol call/use edges. References at module scope
            # (imports, top-level constants) are captured by import_edges instead, so
            # they don't pollute impact analysis or test selection.
            enc = _enclosing_qualname(n)
            if not enc or enc not in graph.symbols:
                continue
            src = enc
            try:
                targets = n.goto(follow_imports=True)
            except Exception:
                continue
            for d in targets:
                tgt = _resolve_target(d, abspaths, def_index, fullname_index)
                if tgt and tgt in graph.symbols:
                    graph.add_edge(src, tgt)

    # ---- Module import edges (module -> imported repo module) ----
    _add_import_edges(graph, scripts, sources)
    return graph


def _resolve_target(
    d: Any,
    abspaths: set[str],
    def_index: dict[tuple[str, int], str],
    fullname_index: dict[str, str],
) -> Optional[str]:
    """Map a Jedi goto target to a known symbol qualname, or None if outside the repo."""
    try:
        mp = d.module_path
    except Exception:
        mp = None
    if mp is not None:
        ap = str(Path(mp).resolve())
        if ap in abspaths:
            q = def_index.get((ap, d.line))
            if q:
                return q
    try:
        fn = d.full_name
    except Exception:
        fn = None
    if fn and fn in fullname_index:
        return fullname_index[fn]
    return None


def _add_import_edges(graph: Graph, scripts: dict[str, Any], sources: dict[str, str]) -> None:
    known_modules = {s.qualname for s in graph.symbols.values() if s.kind == KIND_MODULE}
    for mod, source in sources.items():
        for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
                             source, re.MULTILINE):
            target = (m.group(1) or m.group(2) or "").lstrip(".")
            top = target.split(".")[0]
            for km in known_modules:
                if km == target or km.split(".")[0] == top:
                    graph.add_import_edge(mod, km)
