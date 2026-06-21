"""Swag metrics that are runnable now, with no agent.

- 4c Code-health: LOC, cyclomatic complexity (radon, optional), max nesting,
  longest function, context files. Before/after on what landed.
- 4a Comprehension proxy: deterministic "tokens to understand a module" =
  file tokens + dependency fan-out (imports). Validates *direction* before the
  real-agent ROI number (4a-real) exists. Labeled a proxy; never agent tokens.

Both reuse the production analyzer (`refactorika.core.analyze`) so the numbers
track the same structure thresholds the harness reasons about.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

# Make the in-repo package importable when run from the eval venv.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from refactorika.core.analyze import (  # noqa: E402
    _func_name,
    _funcs,
    _import_lines,
    _max_depth,
    analyze_file,
)

_PY_LANGUAGE = Language(tspython.language())

try:  # radon is optional; complexity degrades to None without it.
    from radon.complexity import cc_visit

    _HAS_RADON = True
except Exception:  # noqa: BLE001
    _HAS_RADON = False


def count_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate (~4 chars/token). Deterministic."""
    return max(1, len(text) // 4)


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _complexities(content: str) -> list[int]:
    if not _HAS_RADON:
        return []
    try:
        return [block.complexity for block in cc_visit(content)]
    except Exception:  # noqa: BLE001
        return []


@dataclass
class FileHealth:
    file: str
    loc: int
    max_nesting: int
    longest_fn: int
    num_opportunities: int
    complexities: list[int]


def _file_health(path: Path, repo_root: Path) -> FileHealth:
    content = path.read_text()
    tree = Parser(_PY_LANGUAGE).parse(content.encode())
    root = tree.root_node
    max_nesting = 0
    longest_fn = 0
    for fn in _funcs(root):
        max_nesting = max(max_nesting, _max_depth(fn))
        longest_fn = max(longest_fn, fn.end_point[0] - fn.start_point[0] + 1)
    opps = analyze_file(str(path)).opportunities
    return FileHealth(
        file=str(path.relative_to(repo_root)),
        loc=content.count("\n") + 1,
        max_nesting=max_nesting,
        longest_fn=longest_fn,
        num_opportunities=len(opps),
        complexities=_complexities(content),
    )


def repo_health(root: Path) -> dict:
    """4c code-health snapshot for a whole tree."""
    healths = [_file_health(p, root) for p in _py_files(root)]
    all_cx = [c for h in healths for c in h.complexities]
    context_files = len(list(root.rglob("*.context.md")))
    return {
        "loc": sum(h.loc for h in healths),
        "max_nesting": max((h.max_nesting for h in healths), default=0),
        "longest_fn": max((h.longest_fn for h in healths), default=0),
        "opportunities": sum(h.num_opportunities for h in healths),
        "avg_complexity": round(sum(all_cx) / len(all_cx), 1) if all_cx else None,
        "max_complexity": max(all_cx) if all_cx else None,
        "context_files": context_files,
        "complexity_tool": "radon" if _HAS_RADON else None,
        "per_file": {h.file: h.num_opportunities for h in healths},
    }


def _fan_out(content: str) -> int:
    """Local-import fan-out: a rough proxy for how much else you must load."""
    tree = Parser(_PY_LANGUAGE).parse(content.encode())
    return len(_import_lines(tree.root_node))


def comprehension_tokens(root: Path) -> dict:
    """4a proxy: per-module tokens-to-comprehend = file tokens + fan-out cost.

    Fan-out is charged a flat per-import surcharge (you must at least skim each
    dependency's surface). A generated context file, when present, offsets this.
    """
    per_module: dict[str, int] = {}
    for path in _py_files(root):
        if path.name.startswith("test_"):
            continue
        content = path.read_text()
        tokens = count_tokens(content) + _fan_out(content) * 200
        ctx = path.with_suffix(".context.md")
        if ctx.exists():
            tokens = max(count_tokens(ctx.read_text()), tokens // 4)
        per_module[str(path.relative_to(root))] = tokens
    avg = round(sum(per_module.values()) / len(per_module)) if per_module else 0
    return {"per_module": per_module, "avg": avg}


def health_delta(before: Path, after: Path) -> dict:
    """4c + 4a, before vs after. `after` is a tree with landed edits applied."""
    hb, ha = repo_health(before), repo_health(after)
    cb, ca = comprehension_tokens(before), comprehension_tokens(after)
    improved = sum(
        1
        for f, n in ha["per_file"].items()
        if f in hb["per_file"] and n < hb["per_file"][f]
    )
    return {
        "health_before": hb,
        "health_after": ha,
        "files_improved": improved,
        "files_touched": len(ha["per_file"]),
        "comprehension_before": cb["avg"],
        "comprehension_after": ca["avg"],
    }
