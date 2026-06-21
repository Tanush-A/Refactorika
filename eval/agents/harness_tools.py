"""Refactorika-only context and verified mutation tools for the harness arm."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from refactorika.analysis.audit import audit_repo, build_plan
from refactorika.core.storage import Storage
from refactorika.harness import verify_edits
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.context import ContextRetriever

from .schema import RefactorPlan
from .tools import DeveloperTools, ToolResult

REQUIRED_GATES = ("lint", "typecheck", "tests")
_DIAGNOSTIC = re.compile(r"(?P<path>[^:|]+\.py):(?P<line>\d+): (?P<message>[^|]+)")


@dataclass(frozen=True)
class HarnessContext:
    audit: dict[str, Any]
    dependency_plan: dict[str, Any]
    architecture_notes: dict[str, str]
    remembered_context: dict[str, Any]


def bootstrap_harness_context(
    repo: Path,
    *,
    max_entries: int = 10,
    max_note_chars: int = 12_000,
) -> HarnessContext:
    """Run product analysis before the harness agent's first model call."""

    root = repo.resolve()
    storage = Storage(redis_url=None, json_path=root / ".refactorika" / "agent-state.json")
    audit = audit_repo(str(root), storage).to_dict()
    plan = build_plan(str(root), storage).to_dict()
    audit["entries"] = audit.get("entries", [])[:max_entries]
    plan["tasks"] = plan.get("tasks", [])[:max_entries]

    notes: dict[str, str] = {}
    remaining = max_note_chars
    for path in sorted(root.rglob("*.md")):
        if ".refactorika" in path.parts or remaining <= 0:
            continue
        relative = path.relative_to(root).as_posix()
        text = path.read_text(errors="replace")[:remaining]
        notes[relative] = text
        remaining -= len(text)

    memory = AgentMemory(storage)
    retriever = ContextRetriever(storage, memory)
    remembered: dict[str, Any] = {}
    for task in plan.get("tasks", [])[:max_entries]:
        file = str(task.get("file", ""))
        if not file:
            continue
        path = Path(file)
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        remembered[relative] = {
            "history": memory.history(file)[-3:],
            "conventions": retriever.conventions(file),
            "dependents": task.get("dependents", []),
        }

    return HarnessContext(audit, plan, notes, remembered)


class HarnessMutationExecutor:
    """Submit the shared patch shape through atomic Refactorika verification."""

    def __init__(self, repo: Path, *, timeout: int = 180) -> None:
        self.repo = repo.resolve()
        self.timeout = timeout

    def submit_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        edits = payload.get("edits")
        if not isinstance(edits, dict) or not edits:
            return _error("invalid_patch", "edits must be a non-empty path-to-content object")
        if not all(isinstance(path, str) and isinstance(text, str) for path, text in edits.items()):
            return _error("invalid_patch", "every edit path and content must be a string")
        if any(path.startswith("tests/") for path in edits):
            return _error("invalid_patch", "test-file mutations are forbidden")
        for relative in edits:
            target = (self.repo / relative).resolve()
            try:
                target.relative_to(self.repo)
            except ValueError:
                return _error("invalid_patch", f"path escapes repository: {relative}")

        record = verify_edits(
            self.repo,
            edits,
            test_command=[sys.executable, "-m", "pytest", "-q"],
            required_gates=REQUIRED_GATES,
            timeout=self.timeout,
        )
        failed_gate = record.failure_reason.split(":", 1)[0] if record.failure_reason else None
        diagnostics = _diagnostics(record.gate_details)
        return {
            "status": record.status,
            "failed_gate": failed_gate,
            "diagnostics": diagnostics,
            "changed_paths": sorted(edits),
            "rollback_complete": record.status != "rolled-back"
            or all(
                not (self.repo / path).exists() or (self.repo / path).read_text() != content
                for path, content in edits.items()
            ),
            "checks": asdict(record.checks),
            "refactor_kind": str(payload.get("refactor_kind", "refactor")),
            "plan_step": payload.get("plan_step"),
        }


class HarnessDeveloperTools(DeveloperTools):
    """The shared developer tools with only patch execution overridden."""

    def __init__(self, repo: Path | str, **kwargs: Any) -> None:
        super().__init__(repo, **kwargs)
        self._mutation_executor = HarnessMutationExecutor(self.repo, timeout=int(self.timeout))

    def submit_patch(
        self,
        *,
        edits: dict[str, str],
        refactor_kind: str,
        plan_step: str,
    ) -> ToolResult:
        started = time.monotonic()
        result = self._mutation_executor.submit_patch(
            {
                "edits": edits,
                "refactor_kind": refactor_kind,
                "plan_step": plan_step,
            }
        )
        status = str(result.get("status", "error"))
        ok = status == "committed"
        return ToolResult(
            status="ok" if ok else "error",
            data=result,
            error=None if ok else str(result.get("message") or result.get("failed_gate")),
            error_class=None if ok else str(result.get("error_class") or "VerificationRejected"),
            seconds=time.monotonic() - started,
            metadata={"verification_status": status},
        )


def completion_audit(
    repo: Path,
    plan: RefactorPlan,
    baseline: dict[str, str],
) -> dict[str, Any]:
    """Check plan completion and scope before the workflow may enter DONE."""

    root = repo.resolve()
    changed = {
        path
        for path, original in baseline.items()
        if (root / path).is_file() and (root / path).read_text() != original
    }
    failures: list[str] = []
    incomplete = [step.id for step in plan.steps if not step.completed]
    if incomplete:
        failures.append(f"incomplete plan steps: {', '.join(incomplete)}")
    missing = set(plan.affected_paths) - changed
    if missing:
        failures.append(f"planned paths unchanged: {', '.join(sorted(missing))}")
    forbidden = sorted(path for path in changed if path.startswith("tests/"))
    if forbidden:
        failures.append(f"forbidden test changes: {', '.join(forbidden)}")
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "changed_paths": sorted(changed),
        "completed_steps": [step.id for step in plan.steps if step.completed],
    }


def _diagnostics(details: dict[str, str]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for gate, detail in details.items():
        for match in _DIAGNOSTIC.finditer(detail):
            diagnostics.append(
                {
                    "gate": gate,
                    "path": match.group("path").strip(),
                    "line": int(match.group("line")),
                    "message": match.group("message").strip(),
                }
            )
        if not any(item["gate"] == gate for item in diagnostics):
            diagnostics.append({"gate": gate, "path": None, "line": None, "message": detail})
    return diagnostics[:20]


def _error(error_class: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error_class": error_class,
        "message": message,
        "changed_paths": [],
        "rollback_complete": True,
    }
