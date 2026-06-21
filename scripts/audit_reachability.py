"""Import-reachability audit for the refactorika package.

Parses every import statement (including nested/local imports, e.g. the lazy engine imports
inside transforms.base.dispatch), resolves relative imports, and BFS-traces from each entry
surface. Classifies every module as product-reachable (CLI + MCP), eval/scripts-only,
test-only, or orphaned — so "is all the code used?" has a deterministic answer.

    python scripts/audit_reachability.py

Caveats: this is *module-level* import reachability. It does not prove every function in a
reachable module is called (dead functions inside live modules need a finer pass), and it
cannot see imports built from runtime strings (none in this repo today). Docstring mentions of
a module are NOT imports and are correctly ignored.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "refactorika"
PRODUCT_ENTRIES = [f"{PKG}.cli", f"{PKG}.mcp_server"]


def _dotted(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _build_index() -> tuple[dict[str, Path], set[str]]:
    modules, pkgs = {}, set()
    for p in (ROOT / PKG).rglob("*.py"):
        name = _dotted(p)
        modules[name] = p
        if p.name == "__init__.py":
            pkgs.add(name)
    return modules, pkgs


MODULES, PKGS = _build_index()
KNOWN = set(MODULES)


def _pkg_of(mod: str) -> str:
    if mod in PKGS:
        return mod
    return mod.rsplit(".", 1)[0] if "." in mod else ""


def imports_of(path: Path, modname: str) -> set[str]:
    """Refactorika modules imported by this file (any import node, anywhere in the tree)."""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    pkg = _pkg_of(modname)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                for i in range(len(parts), 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in KNOWN:
                        out.add(cand)
                        break
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg
                for _ in range(node.level - 1):
                    base = base.rsplit(".", 1)[0] if "." in base else ""
                base = f"{base}.{node.module}" if node.module else base
            else:
                base = node.module or ""
            if not base.startswith(PKG):
                continue
            if base in KNOWN:
                out.add(base)
            for a in node.names:
                if (cand := f"{base}.{a.name}") in KNOWN:
                    out.add(cand)
    return out


EDGES = {m: imports_of(p, m) for m, p in MODULES.items()}


def _bfs(seeds: set[str]) -> set[str]:
    seen, stack = set(), list(seeds)
    while stack:
        m = stack.pop()
        if m in seen or m not in EDGES:
            continue
        seen.add(m)
        stack.extend(EDGES[m])
    return seen


def _external_seed(paths: list[Path]) -> set[str]:
    seed: set[str] = set()
    for p in paths:
        if p.exists():
            seed |= imports_of(p, "")
    return seed


def main() -> None:
    product = _bfs(set(PRODUCT_ENTRIES))
    eval_reach = _bfs(_external_seed(
        list((ROOT / "eval").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py"))
    ))
    test_reach = _bfs(_external_seed(list((ROOT / "tests").rglob("*.py"))))
    orphan = set(MODULES) - product - eval_reach - test_reach

    def show(title: str, mods: set[str]) -> None:
        print(f"\n{title}: {len(mods)}")
        for m in sorted(mods):
            print(f"   {m}")

    print(f"TOTAL {PKG} modules: {len(MODULES)}")
    show("PRODUCT-reachable (cli + mcp_server)", product)
    show("EVAL/SCRIPTS-only", eval_reach - product)
    show("TEST-only", test_reach - product - eval_reach)
    show("ORPHANED (nothing reaches it)", orphan)


if __name__ == "__main__":
    main()
