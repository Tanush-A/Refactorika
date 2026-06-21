"""The Checker — the gate that turns an EditMap into a verified commit (or a revert).

Applies a multi-file edit atomically: snapshot every target, parse-gate the proposed
contents before touching disk, write, then run lint -> type -> tests. Tests are
*impact-scoped* — only the tests that can be affected by the change run, not the whole
suite — which is the efficiency win. All gates green: ``git`` commit. Any gate red or a
crash: restore every file to its snapshot. Tools are the arbiter; no LLM decides safety.
"""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Optional

from refactorika.core.gates import (
    lint_gate,
    parse_gate,
    ruff_baseline,
    test_gate,
    typecheck_gate,
)
from refactorika.core.schema import EditRecord
from refactorika.core.storage import Storage
from refactorika.graph.model import Graph
from refactorika.transforms.base import EditMap


def impacted_test_node_ids(graph: Graph, root: str, impact: list[str]) -> list[str]:
    """Map an impact set (qualnames) to pytest node ids for the test symbols in it."""
    root_p = Path(root).resolve()
    node_ids: list[str] = []
    for qual in impact:
        sym = graph.symbols.get(qual)
        if sym is None or not sym.name.startswith("test_"):
            continue
        try:
            rel = Path(sym.file).resolve().relative_to(root_p)
        except ValueError:
            rel = Path(sym.file).name
        node_ids.append(f"{rel}::{sym.name}")
    return sorted(set(node_ids))


class Checker:
    """Runs the gate stack and commits/rolls back a multi-file edit atomically."""

    def __init__(self, root: str, storage: Optional[Storage] = None, run_tests: bool = True):
        self.root = Path(root).resolve()
        self.storage = storage or Storage()
        self.run_tests = run_tests

    # ------------------------------------------------------------------ public
    def verify_apply(
        self,
        edits: EditMap,
        refactor_kind: str,
        test_node_ids: Optional[list[str]] = None,
        retries: int = 0,
    ) -> EditRecord:
        """Apply *edits* atomically through the gate stack; commit on green, revert on red."""
        paths = [Path(p).resolve() for p in edits]
        rel_paths = [self._rel(p) for p in paths]
        originals = {p: (p.read_text(encoding="utf-8") if p.exists() else "") for p in paths}
        diff = self._combined_diff(originals, edits)

        record = EditRecord(
            file=rel_paths[0] if rel_paths else "",
            files=rel_paths,
            refactor_kind=refactor_kind,
            retries=retries,
            diff=diff,
        )
        checks = record.checks

        # Gate 1 — parse every proposed file (before touching disk).
        for p in paths:
            ok, detail = parse_gate(edits[str(p)])
            checks.parse = ok
            if ok is False:
                return self._finalize(record, "rolled-back", f"{self._rel(p)}: {detail}")

        baselines = {p: ruff_baseline(p) for p in paths}
        for p in paths:
            p.write_text(edits[str(p)], encoding="utf-8")
        try:
            # Gate 2 — lint (new violations only), per touched file.
            for p in paths:
                ok, detail = lint_gate(p, baselines[p])
                checks.lint = ok
                if ok is False:
                    return self._rollback(record, originals, f"{self._rel(p)}: {detail}")
            # Gate 3 — types, per touched file.
            for p in paths:
                ok, detail = typecheck_gate(p)
                checks.typecheck = ok
                if ok is False:
                    return self._rollback(record, originals, f"{self._rel(p)}: {detail}")
            # Gate 4 — behavior (impact-scoped tests). Type-clean != behavior-preserving.
            if self.run_tests:
                ok, detail = test_gate(self.root, node_ids=test_node_ids)
                checks.tests = ok
                if ok is False:
                    return self._rollback(record, originals, detail)
        except Exception as exc:  # noqa: BLE001 — any gate crash must restore the tree
            return self._rollback(record, originals, f"gate crashed: {exc}")

        self._commit(paths, refactor_kind)
        return self._finalize(record, "committed", None)

    def run_full_suite(self) -> tuple[Optional[bool], str]:
        """Run the whole test suite — used for the baseline and the finale check."""
        return test_gate(self.root, node_ids=None)

    # ----------------------------------------------------------------- helpers
    def _rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.root))
        except ValueError:
            return str(p)

    def _combined_diff(self, originals: dict[Path, str], edits: EditMap) -> str:
        chunks = []
        for p, old in originals.items():
            new = edits[str(p)]
            name = self._rel(p)
            chunks.append("".join(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=f"a/{name}", tofile=f"b/{name}",
            )))
        return "".join(chunks)

    def _commit(self, paths: list[Path], refactor_kind: str) -> None:
        for p in paths:
            subprocess.run(["git", "-C", str(self.root), "add", str(p)], capture_output=True)
        names = ", ".join(self._rel(p) for p in paths)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-m", f"refactor({refactor_kind}): {names}"],
            capture_output=True,
        )

    def _rollback(self, record: EditRecord, originals: dict[Path, str], reason: str) -> EditRecord:
        for p, old in originals.items():
            if old or p.exists():
                p.write_text(old, encoding="utf-8")
        return self._finalize(record, "rolled-back", reason)

    def _finalize(self, record: EditRecord, status: str, reason: Optional[str]) -> EditRecord:
        record.status = status  # type: ignore[assignment]
        record.failure_reason = reason
        self.storage.append_log(record.to_dict())
        return record
