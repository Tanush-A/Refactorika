"""Deterministic cleanup — behavior-preserving LOC reduction with zero LLM calls.

Chains autoflake (unused imports/variables) → ruff --fix (simplifications, modern
syntax, import order) → ruff format. Every step is reference-local and the gate stack
proves behavior is preserved, so this is the cheapest, safest win in the pipeline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from refactorika.core.schema import TransformSpec
from refactorika.graph.model import Graph
from refactorika.transforms.base import EditMap

# Auto-fixable rule families: F=pyflakes, I=isort, SIM=flake8-simplify,
# C4=comprehensions, UP=pyupgrade.
_RUFF_SELECT = "F,I,SIM,C4,UP"


def _ruff(args: list[str], source: str, filename: str) -> str:
    ruff = shutil.which("ruff")
    if ruff is None:
        return source
    proc = subprocess.run(
        [ruff, *args, "--stdin-filename", filename, "-"],
        input=source,
        capture_output=True,
        text=True,
    )
    # ruff writes the transformed file to stdout; on error it leaves stdout empty.
    return proc.stdout if proc.stdout else source


def clean_source(source: str, filename: str = "module.py") -> str:
    """Return a cleaned version of *source* (pure; no disk writes)."""
    try:
        import autoflake

        source = autoflake.fix_code(
            source,
            remove_all_unused_imports=True,
            remove_unused_variables=True,
            remove_duplicate_keys=True,
        )
    except Exception:
        pass
    source = _ruff(["check", "--fix", "--select", _RUFF_SELECT], source, filename)
    source = _ruff(["format"], source, filename)
    return source


def cleanup(spec: TransformSpec, root: str, graph: Graph) -> EditMap:
    """Clean the file(s) named in spec.params['files'], or the target symbol's file."""
    files = spec.params.get("files")
    if not files:
        sym = graph.symbols.get(spec.target)
        if sym is None:
            return {}
        files = [sym.file]

    edits: EditMap = {}
    for f in files:
        p = Path(f).resolve()
        if not p.exists():
            continue
        original = p.read_text(encoding="utf-8")
        cleaned = clean_source(original, p.name)
        if cleaned != original:
            edits[str(p)] = cleaned
    return edits
