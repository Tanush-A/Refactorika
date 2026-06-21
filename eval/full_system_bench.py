"""Full-system OFF-vs-ON benchmark for autonomous refactoring.

Both arms start from the exact user request ``refactor this codebase`` and from
separate copies of the same repository. OFF asks the model to form its own plan
before proposing edits. ON uses Refactorika's audit and dependency-ordered plan
to build the model prompt, then routes proposals through atomic verification and
gate-guided retries. Held-out tests are injected only by the final grader.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, cast

from refactorika.analysis.audit import build_plan
from refactorika.analysis.dead_code import find_dead_code as _rf_find_dead_code
from refactorika.analysis.duplicates import find_duplicates as _rf_find_duplicates
from refactorika.core.analyze import analyze_file as _rf_analyze_file
from refactorika.core.storage import Storage
from refactorika.harness import mark_escalated, verify_edits
from refactorika.memory.vector_index import VectorIndex
from refactorika.observability import (
    capture_benchmark_regression,
    capture_exception,
    init_sentry,
)

from eval.full_system_cases import ALL_CASES, USER_PROMPT
from eval.full_system_cases.behavior import BehaviorCase
from eval.full_system_cases.multifile import MultiFileCase, structural_failures
from eval.full_system_cases.recovery import RecoveryCase
from eval.full_system_cases.stress import (
    StressCase,
    structural_failures as stress_failures,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_GATES = ("lint", "typecheck", "tests")


@dataclass(frozen=True)
class CaseAdapter:
    name: str
    source: BehaviorCase | MultiFileCase | RecoveryCase | StressCase
    baseline_files: dict[str, str]
    hidden_tests: dict[str, str]
    user_prompt: str
    required_paths: frozenset[str]
    allowed_paths: frozenset[str]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0

    def cost(self, usage: Usage) -> float | None:
        rates = (
            self.input_per_mtok,
            self.output_per_mtok,
            self.cache_read_per_mtok,
            self.cache_write_per_mtok,
        )
        if not any(rates):
            return None
        return round(
            (
                usage.input_tokens * self.input_per_mtok
                + usage.output_tokens * self.output_per_mtok
                + usage.cache_read_tokens * self.cache_read_per_mtok
                + usage.cache_write_tokens * self.cache_write_per_mtok
            )
            / 1_000_000,
            6,
        )


@dataclass
class Completion:
    text: str
    usage: Usage
    seconds: float
    error: str | None = None
    error_class: str | None = None


@dataclass
class Proposal:
    edits: dict[str, str]
    usage: Usage
    seconds: float
    prompt: str
    plan: str | None = None
    error: str | None = None
    model_calls: int = 1
    error_class: str | None = None


class Backend(Protocol):
    name: str

    def complete(self, prompt: str) -> Completion: ...


def adapt_case(case: object) -> CaseAdapter:
    """Normalize fixture families into one runner contract."""

    if isinstance(case, BehaviorCase):
        hidden = dict(case.hidden_tests)
        required = frozenset(
            expectation.target_path for expectation in case.structural_expectations
        )
    elif isinstance(case, (MultiFileCase, RecoveryCase, StressCase)):
        hidden = {"tests/oracle/test_hidden.py": case.hidden_tests}
        if isinstance(case, MultiFileCase):
            required = frozenset(expectation.path for expectation in case.expectations)
        elif isinstance(case, StressCase):
            required = frozenset(
                expectation.path
                for expectation in case.expectations
                if expectation.kind != "unchanged"
            )
        else:
            required = frozenset(
                path
                for attempt in case.attempts
                for path in attempt
                if path.endswith(".py") and not path.startswith("tests/")
            )
    else:
        raise TypeError(f"unsupported full-system case: {type(case).__name__}")
    return CaseAdapter(
        name=case.name,
        source=case,
        baseline_files=dict(case.baseline_files),
        hidden_tests=hidden,
        user_prompt=case.user_prompt,
        required_paths=required,
        allowed_paths=required,
    )


CASES = tuple(adapt_case(case) for case in ALL_CASES)


def materialize(case: CaseAdapter, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for relative, content in case.baseline_files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return destination


def visible_snapshot(repo: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(p for p in repo.rglob("*") if p.is_file()):
        relative = path.relative_to(repo).as_posix()
        if relative.startswith(("tests/oracle/", ".refactorika/", ".git/")):
            continue
        if path.suffix in {".py", ".md", ".json", ".toml"}:
            files[relative] = path.read_text()
    return files


def build_off_prompt(case: CaseAdapter, repo: Path) -> str:
    """Ask the non-harness agent to select and implement a refactor in one call."""

    return (
        "You are an autonomous refactoring agent.\n"
        f"User request (verbatim): {case.user_prompt}\n\n"
        "Inspect the repository snapshot, choose the highest-value behavior-preserving "
        "refactor, update all affected call sites, and preserve compatibility. Hidden tests "
        "may exist. Return ONLY a JSON object mapping changed relative Python file paths to "
        "their complete new contents. Do not return markdown or edit tests.\n\n"
        f"Repository snapshot:\n{json.dumps(visible_snapshot(repo), sort_keys=True)}"
    )


def _architecture_notes(repo: Path) -> dict[str, str]:
    return {
        path.relative_to(repo).as_posix(): path.read_text()
        for path in sorted(repo.rglob("*.md"))
        if ".git" not in path.parts
    }


def build_harness_prompt(case: CaseAdapter, repo: Path) -> str:
    """Build a scoped edit prompt from Refactorika analysis, not benchmark answers."""

    state = repo / ".refactorika" / "benchmark-state.json"
    plan = build_plan(str(repo), Storage(redis_url=None, json_path=state)).to_dict()
    context = {
        "audit_plan": plan,
        "architecture_notes": _architecture_notes(repo),
    }
    return (
        "Refactorika received this user request verbatim: "
        f"{case.user_prompt}\n\n"
        "Refactorika audit and planning context follows. Select a coherent, scoped "
        "behavior-preserving refactor. Update every affected call site and preserve public "
        "compatibility. Do not assume visible tests are complete.\n\n"
        f"Harness context:\n{json.dumps(context, sort_keys=True)}"
    )


def build_edit_prompt(
    case: CaseAdapter, repo: Path, plan: str, *, failure: str | None = None
) -> str:
    prompt = (
        f"User request (verbatim): {case.user_prompt}\n\n"
        f"Refactoring plan/context:\n{plan}\n\n"
        "Return ONLY a JSON object mapping changed relative Python file paths to their "
        "complete new contents. Do not return markdown. Change at least one file, preserve "
        "behavior, and do not edit tests.\n\n"
        f"Repository snapshot:\n{json.dumps(visible_snapshot(repo), sort_keys=True)}"
    )
    if failure:
        prompt += (
            "\n\nRefactorika rejected the previous proposal. Use these exact diagnostics to "
            f"repair the proposal without broadening scope:\n{failure}"
        )
    return prompt


def _decode_patch(completion: Completion, prompt: str, plan: str | None = None) -> Proposal:
    if completion.error:
        return Proposal(
            {},
            completion.usage,
            completion.seconds,
            prompt,
            plan,
            completion.error,
            error_class=completion.error_class or "provider_failure",
        )
    raw = completion.text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        edits = json.loads(raw)
        if (
            not isinstance(edits, dict)
            or not edits
            or not all(
                isinstance(path, str) and isinstance(content, str)
                for path, content in edits.items()
            )
        ):
            raise ValueError("response must be a non-empty string-to-string patch object")
        if any(path.startswith("tests/") for path in edits):
            raise ValueError("agent attempted to edit tests")
        return Proposal(edits, completion.usage, completion.seconds, prompt, plan)
    except (json.JSONDecodeError, ValueError) as exc:
        return Proposal(
            {},
            completion.usage,
            completion.seconds,
            prompt,
            plan,
            str(exc),
            error_class="malformed_response",
        )


def propose_off(backend: Backend, case: CaseAdapter, repo: Path) -> Proposal:
    prompt = build_off_prompt(case, repo)
    return _decode_patch(backend.complete(prompt), prompt)


def propose_on(
    backend: Backend,
    case: CaseAdapter,
    repo: Path,
    *,
    harness_prompt: str,
    failure: str | None = None,
) -> Proposal:
    prompt = build_edit_prompt(case, repo, harness_prompt, failure=failure)
    return _decode_patch(backend.complete(prompt), prompt, harness_prompt)


def propose_agentic(backend: "AgenticBackend", case: CaseAdapter, repo: Path) -> Proposal:
    edits, usage, seconds, error, model_calls = backend.run(repo, case.user_prompt)
    if not error and not edits:
        error = "agent made no changes to source files"
    return Proposal(
        edits=edits if not error else {},
        usage=usage,
        seconds=seconds,
        prompt=f"[agentic:{backend.name}] {case.user_prompt}",
        error=error,
        model_calls=model_calls,
    )


def propose_agentic_mcp(
    backend: "AgenticHarnessBackend", case: CaseAdapter, repo: Path
) -> tuple[Proposal, list[dict]]:
    edits, usage, seconds, error, model_calls, gate_log = backend.run(repo, case.user_prompt)
    if not error and not edits:
        error = "agent made no changes to source files"
    proposal = Proposal(
        edits=edits if not error else {},
        usage=usage,
        seconds=seconds,
        prompt=f"[agentic+harness:{backend.name}] {case.user_prompt}",
        error=error,
        model_calls=model_calls,
    )
    return proposal, gate_log


def _write_patch(repo: Path, edits: dict[str, str]) -> str | None:
    if not edits:
        return "proposal contains no edits"
    changed = False
    for relative, content in edits.items():
        path = (repo / relative).resolve()
        try:
            path.relative_to(repo.resolve())
        except ValueError:
            return f"path escapes repository: {relative}"
        if (
            path.suffix != ".py"
            or (path.exists() and not path.is_file())
            or not path.parent.is_dir()
            or relative.startswith("tests/")
        ):
            return f"invalid editable path: {relative}"
        changed |= not path.is_file() or path.read_text() != content
        path.write_text(content)
    return None if changed else "proposal does not change repository contents"


def _behavior_structure_failures(case: BehaviorCase, repo: Path) -> list[str]:
    """Machine-check the core structural target without exposing it to the agent."""

    import ast

    target = case.structural_expectations[0].target_path
    path = repo / target
    if not path.is_file() or path.read_text() == case.baseline_files[target]:
        return [f"no effective refactor in {target}"]
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return [f"invalid Python in {target}"]
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    private = {node.name for node in functions if node.name.startswith("_")}
    if case.name in {"rounding_order", "near_duplicate_semantics"} and not private:
        return ["expected a shared private helper"]
    if case.name == "guard_clause_continue":
        if not any(isinstance(node, ast.Continue) for node in ast.walk(tree)):
            return ["expected loop guard clauses using continue"]
    return []


def grade_structure(case: CaseAdapter, repo: Path) -> list[str]:
    if isinstance(case.source, MultiFileCase):
        return structural_failures(case.source, repo)
    if isinstance(case.source, BehaviorCase):
        return _behavior_structure_failures(case.source, repo)
    if isinstance(case.source, StressCase):
        return stress_failures(case.source, repo)
    changed = any(
        (repo / path).is_file() and (repo / path).read_text() != content
        for path, content in case.baseline_files.items()
        if path.endswith(".py") and not path.startswith("tests/")
    )
    return [] if changed else ["no effective source refactor"]


def _run_hidden_tests(case: CaseAdapter, repo: Path) -> tuple[bool, str]:
    for relative, content in case.hidden_tests.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    shutil.rmtree(repo / "tests" / "oracle", ignore_errors=True)
    output = (result.stdout + "\n" + result.stderr).strip().splitlines()
    detail = output[-1] if output else f"pytest exit {result.returncode}"
    return result.returncode == 0, detail


def _run_visible_tests(repo: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip().splitlines()
    detail = output[-1] if output else f"pytest exit {result.returncode}"
    return result.returncode == 0, detail


def oracle_grade(case: CaseAdapter, repo: Path) -> tuple[bool, str, list[str]]:
    behavior_pass, detail = _run_hidden_tests(case, repo)
    structure = grade_structure(case, repo)
    return behavior_pass, detail, structure


def calibrate(cases: tuple[CaseAdapter, ...] = CASES) -> dict[str, object]:
    records = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix=f"full-cal-{case.name}-") as tmp:
            repo = materialize(case, Path(tmp) / "repo")
            visible, visible_detail = _run_visible_tests(repo)
            hidden, hidden_detail = _run_hidden_tests(case, repo)
            structure_missing = bool(grade_structure(case, repo))
            records.append(
                {
                    "case": case.name,
                    "visible_baseline_pass": visible,
                    "hidden_baseline_pass": hidden,
                    "hidden_baseline_expected": not isinstance(case.source, MultiFileCase),
                    "baseline_misses_target_structure": structure_missing,
                    "visible_detail": visible_detail,
                    "hidden_detail": hidden_detail,
                }
            )
    # Multi-file oracles import the intended post-refactor symbol and therefore
    # intentionally fail on the baseline. Behavior-only oracles must pass it.
    valid = all(
        record["visible_baseline_pass"]
        and record["baseline_misses_target_structure"]
        and (record["hidden_baseline_pass"] == record["hidden_baseline_expected"])
        for record in records
    )
    return {"valid": valid, "records": records}


def _outcome(landed: bool, behavior: bool, structure: list[str]) -> dict[str, object]:
    return {
        "landed": landed,
        "behavior_pass": behavior if landed else None,
        "structural_pass": not structure if landed else None,
        "correct_landed": landed and behavior and not structure,
        "regression_shipped": landed and not behavior,
        "incomplete_refactor_shipped": landed and behavior and bool(structure),
    }


def _grade_proposal(
    case: CaseAdapter, proposal: Proposal, destination: Path
) -> tuple[bool, str, list[str]]:
    repo = materialize(case, destination)
    error = proposal.error or _write_patch(repo, proposal.edits)
    return (False, error, []) if error else oracle_grade(case, repo)


def _change_metrics(
    case: CaseAdapter, edits: dict[str, str], structural_failures: list[str]
) -> dict[str, object]:
    touched: set[str] = set()
    added = 0
    deleted = 0
    new_files = 0
    for path, content in edits.items():
        if path.startswith("tests/") or not path.endswith(".py"):
            continue
        original = case.baseline_files.get(path)
        if original == content:
            continue
        touched.add(path)
        if original is None:
            new_files += 1
            added += len(content.splitlines())
            continue
        matcher = difflib.SequenceMatcher(a=original.splitlines(), b=content.splitlines())
        for tag, a1, a2, b1, b2 in matcher.get_opcodes():
            if tag in {"delete", "replace"}:
                deleted += a2 - a1
            if tag in {"insert", "replace"}:
                added += b2 - b1
    required_touched = touched & set(case.required_paths)
    allowed_touched = touched & set(case.allowed_paths)
    compatibility_failures = [
        failure for failure in structural_failures if failure.startswith("exports ")
    ]
    return {
        "files_changed": len(touched),
        "new_files": new_files,
        "lines_added": added,
        "lines_deleted": deleted,
        "total_churn": added + deleted,
        "required_path_recall": round(len(required_touched) / len(case.required_paths), 3)
        if case.required_paths
        else None,
        "unrelated_edit_precision": round(len(allowed_touched) / len(touched), 3)
        if touched
        else None,
        "structural_effect_recall": 0.0 if structural_failures else 1.0,
        "missed_call_sites": sum(
            failure.startswith(("calls ", "imports_from ")) for failure in structural_failures
        ),
        "compatibility_pass": not compatibility_failures,
        "patch_hash": hashlib.sha256(json.dumps(edits, sort_keys=True).encode()).hexdigest()[:16],
    }


def _usage_record(usage: Usage, calls: int, pricing: Pricing) -> dict[str, object]:
    return {
        "model_calls": calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "total_tokens": usage.total,
        "cost_dollars": pricing.cost(usage),
    }


def _run_pair(
    case: CaseAdapter,
    backend: Backend,
    trial: int,
    max_retries: int,
    pricing: Pricing,
    agentic_backend: "AgenticBackend | None" = None,
    agentic_mcp_backend: "AgenticHarnessBackend | None" = None,
) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix=f"full-{case.name}-") as tmp:
        pair_root = Path(tmp)
        off_repo = materialize(case, Path(tmp) / "off")
        on_repo = materialize(case, Path(tmp) / "on")

        off_started = time.perf_counter()
        off = propose_off(backend, case, off_repo)
        apply_started = time.perf_counter()
        off_error = off.error or _write_patch(off_repo, off.edits)
        off_apply_seconds = time.perf_counter() - apply_started
        grade_started = time.perf_counter()
        off_behavior, off_detail, off_structure = (
            (False, off_error, []) if off_error else oracle_grade(case, off_repo)
        )
        off_grading_seconds = time.perf_counter() - grade_started
        off_landed = not bool(off_error)
        off_outcome = _outcome(off_landed, off_behavior, off_structure)
        off_end_to_end = time.perf_counter() - off_started

        on_started = time.perf_counter()
        audit_started = time.perf_counter()
        harness_prompt = build_harness_prompt(case, on_repo)
        audit_seconds = time.perf_counter() - audit_started
        usage = Usage()
        model_seconds = 0.0
        gate_seconds = 0.0
        grading_seconds = 0.0
        final = None
        current: Proposal | None = None
        failure = None
        attempts: list[Proposal] = []
        attempt_records: list[dict[str, object]] = []
        initial_outcome: dict[str, object] | None = None
        initial_detail = ""
        initial_structure: list[str] = []
        initial_checks = None
        for attempt in range(max_retries + 1):
            current = propose_on(
                backend, case, on_repo, harness_prompt=harness_prompt, failure=failure
            )
            attempts.append(current)
            usage.add(current.usage)
            model_seconds += current.seconds
            if current.error:
                failure = current.error
                attempt_records.append(
                    {
                        "attempt": attempt,
                        "status": "proposal_error",
                        "error_class": current.error_class,
                        "failure_gate": None,
                        "checks": None,
                        "rollback_integrity": True,
                    }
                )
                if attempt == 0:
                    initial_outcome = _outcome(False, False, [])
                    initial_detail = current.error
                if current.error_class == "configuration_failure":
                    break
            else:
                try:
                    gates_started = time.perf_counter()
                    final = verify_edits(
                        on_repo,
                        current.edits,
                        test_command=[sys.executable, "-m", "pytest", "-q"],
                        required_gates=REQUIRED_GATES,
                        retries=attempt,
                    )
                    gate_seconds += time.perf_counter() - gates_started
                    failure_gate = (
                        final.failure_reason.split(":", 1)[0] if final.failure_reason else None
                    )
                    rollback_integrity = final.status == "committed" or (
                        all(
                            (on_repo / path).is_file() and (on_repo / path).read_text() == content
                            for path, content in case.baseline_files.items()
                        )
                        and all(
                            path in case.baseline_files or not (on_repo / path).exists()
                            for path in current.edits
                        )
                    )
                    attempt_records.append(
                        {
                            "attempt": attempt,
                            "status": final.status,
                            "error_class": None,
                            "failure_gate": failure_gate,
                            "checks": asdict(final.checks),
                            "rollback_integrity": rollback_integrity,
                        }
                    )
                    if attempt == 0:
                        initial_checks = asdict(final.checks)
                    if final.status == "committed":
                        break
                    failure = json.dumps(final.gate_details, sort_keys=True)
                    if attempt == 0:
                        initial_grade_started = time.perf_counter()
                        behavior, initial_detail, initial_structure = _grade_proposal(
                            case, current, pair_root / "initial-on-grade"
                        )
                        grading_seconds += time.perf_counter() - initial_grade_started
                        initial_outcome = _outcome(True, behavior, initial_structure)
                except ValueError as exc:
                    failure = str(exc)
                    attempt_records.append(
                        {
                            "attempt": attempt,
                            "status": "invalid_patch",
                            "error_class": "invalid_patch",
                            "failure_gate": "patch_validation",
                            "checks": None,
                            "rollback_integrity": True,
                        }
                    )
                    if attempt == 0:
                        initial_outcome = _outcome(False, False, [])
                        initial_detail = str(exc)
        committed = final is not None and final.status == "committed"
        if final is not None and not committed:
            mark_escalated(final)
        final_grade_started = time.perf_counter()
        on_behavior, on_detail, on_structure = (
            oracle_grade(case, on_repo) if committed else (False, "not landed", [])
        )
        grading_seconds += time.perf_counter() - final_grade_started
        on_outcome = _outcome(committed, on_behavior, on_structure)
        if initial_outcome is None:
            initial_outcome = dict(on_outcome)
            initial_detail = on_detail
            initial_structure = list(on_structure)
        on_end_to_end = time.perf_counter() - on_started

        common = {"case": case.name, "trial": trial, "initial_user_prompt": case.user_prompt}
        records = [
            {
                **common,
                "arm": "off",
                "status": "shipped" if not off_error else "error",
                **off_outcome,
                "oracle_pass": off_behavior,
                "structural_failures": off_structure,
                "detail": off_detail,
                "error_class": off.error_class,
                "tokens": off.usage.total,
                "seconds": round(off.seconds, 3),
                "initial": dict(off_outcome),
                "usage": _usage_record(off.usage, 1, pricing),
                "timing": {
                    "audit_seconds": 0.0,
                    "model_seconds": round(off.seconds, 3),
                    "gate_seconds": 0.0,
                    "application_seconds": round(off_apply_seconds, 3),
                    "grading_seconds": round(off_grading_seconds, 3),
                    "workflow_seconds": round(off.seconds + off_apply_seconds, 3),
                    "end_to_end_seconds": round(off_end_to_end, 3),
                },
                "change": _change_metrics(case, off.edits, off_structure),
                "plan": off.plan,
                "patch": off.edits,
            },
            {
                **common,
                "arm": "on",
                "status": "committed" if committed else "skipped-needs-human",
                **on_outcome,
                "oracle_pass": on_behavior if committed else None,
                "structural_failures": on_structure,
                "detail": on_detail,
                "tokens": usage.total,
                "seconds": round(model_seconds, 3),
                "retries": final.retries if final else max_retries,
                "initial": {
                    **initial_outcome,
                    "detail": initial_detail,
                    "structural_failures": initial_structure,
                    "checks": initial_checks,
                    "verification_status": attempt_records[0]["status"]
                    if attempt_records
                    else "proposal_error",
                },
                "attempts": attempt_records,
                "usage": _usage_record(usage, len(attempts), pricing),
                "timing": {
                    "audit_seconds": round(audit_seconds, 3),
                    "model_seconds": round(model_seconds, 3),
                    "gate_seconds": round(gate_seconds, 3),
                    "application_seconds": 0.0,
                    "grading_seconds": round(grading_seconds, 3),
                    "workflow_seconds": round(audit_seconds + model_seconds + gate_seconds, 3),
                    "end_to_end_seconds": round(on_end_to_end, 3),
                },
                "change": _change_metrics(case, current.edits if current else {}, on_structure),
                "harness_prompt": harness_prompt,
                "patch": current.edits if current else {},
                "checks": asdict(final.checks) if final else None,
            },
        ]

        if agentic_backend is not None:
            agentic_repo = materialize(case, Path(tmp) / "agentic")
            agentic_started = time.perf_counter()
            agentic = propose_agentic(agentic_backend, case, agentic_repo)
            agentic_end_to_end = time.perf_counter() - agentic_started
            agentic_landed = not bool(agentic.error)
            agentic_behavior, agentic_detail, agentic_structure = (
                oracle_grade(case, agentic_repo)
                if agentic_landed
                else (False, agentic.error or "not landed", [])
            )
            agentic_outcome = _outcome(agentic_landed, agentic_behavior, agentic_structure)
            records.append({
                **common,
                "arm": "agentic",
                "status": "shipped" if agentic_landed else "error",
                **agentic_outcome,
                "oracle_pass": agentic_behavior if agentic_landed else None,
                "structural_failures": agentic_structure,
                "detail": agentic_detail,
                "tokens": agentic.usage.total,
                "seconds": round(agentic.seconds, 3),
                "initial": dict(agentic_outcome),
                "usage": _usage_record(agentic.usage, agentic.model_calls, pricing),
                "timing": {
                    "audit_seconds": 0.0,
                    "model_seconds": round(agentic.seconds, 3),
                    "gate_seconds": 0.0,
                    "application_seconds": 0.0,
                    "grading_seconds": 0.0,
                    "workflow_seconds": round(agentic.seconds, 3),
                    "end_to_end_seconds": round(agentic_end_to_end, 3),
                },
                "change": _change_metrics(case, agentic.edits, agentic_structure),
                "plan": None,
                "patch": agentic.edits,
            })

        if agentic_mcp_backend is not None:
            agentic_mcp_repo = materialize(case, Path(tmp) / "agentic_mcp")
            agentic_mcp_started = time.perf_counter()
            agentic_mcp, agentic_mcp_gate_log = propose_agentic_mcp(agentic_mcp_backend, case, agentic_mcp_repo)
            agentic_mcp_end_to_end = time.perf_counter() - agentic_mcp_started
            agentic_mcp_landed = not bool(agentic_mcp.error)
            agentic_mcp_behavior, agentic_mcp_detail, agentic_mcp_structure = (
                oracle_grade(case, agentic_mcp_repo)
                if agentic_mcp_landed
                else (False, agentic_mcp.error or "not landed", [])
            )
            agentic_mcp_outcome = _outcome(
                agentic_mcp_landed, agentic_mcp_behavior, agentic_mcp_structure
            )
            records.append({
                **common,
                "arm": "agentic+harness",
                "status": "shipped" if agentic_mcp_landed else "error",
                **agentic_mcp_outcome,
                "oracle_pass": agentic_mcp_behavior if agentic_mcp_landed else None,
                "structural_failures": agentic_mcp_structure,
                "detail": agentic_mcp_detail,
                "tokens": agentic_mcp.usage.total,
                "seconds": round(agentic_mcp.seconds, 3),
                "initial": dict(agentic_mcp_outcome),
                "usage": _usage_record(agentic_mcp.usage, agentic_mcp.model_calls, pricing),
                "timing": {
                    "audit_seconds": 0.0,
                    "model_seconds": round(agentic_mcp.seconds, 3),
                    "gate_seconds": 0.0,   # gate time is embedded in model loop, not separately measurable
                    "application_seconds": 0.0,
                    "grading_seconds": 0.0,
                    "workflow_seconds": round(agentic_mcp.seconds, 3),
                    "end_to_end_seconds": round(agentic_mcp_end_to_end, 3),
                },
                "gate_log": agentic_mcp_gate_log,
                "gate_calls": len(agentic_mcp_gate_log),
                "gate_commits": sum(1 for g in agentic_mcp_gate_log if g["status"] == "committed"),
                "gate_rollbacks": sum(1 for g in agentic_mcp_gate_log if g["status"] == "rolled-back"),
                "change": _change_metrics(case, agentic_mcp.edits, agentic_mcp_structure),
                "plan": None,
                "patch": agentic_mcp.edits,
            })

        return records


def _clustered_delta_ci(
    records: list[dict], field: str, *, arm_a: str = "on", arm_b: str = "off", samples: int = 5000, seed: int = 7
) -> list[float]:
    by_case: dict[str, list[float]] = {}
    pairs: dict[tuple[str, int], dict[str, dict]] = {}
    for record in records:
        pairs.setdefault((record["case"], record["trial"]), {})[record["arm"]] = record
    for (case, _), pair in pairs.items():
        if arm_a not in pair or arm_b not in pair:
            continue
        on_value = pair[arm_a][field]
        off_value = pair[arm_b][field]
        by_case.setdefault(case, []).append(float(on_value) - float(off_value))
    case_deltas = [sum(values) / len(values) for values in by_case.values()]
    if not case_deltas:
        return [0.0, 0.0]
    rng = random.Random(seed)
    draws = sorted(
        sum(rng.choice(case_deltas) for _ in case_deltas) / len(case_deltas) for _ in range(samples)
    )
    return [round(draws[int(samples * 0.025)], 3), round(draws[int(samples * 0.975)], 3)]


def _paired_summary(
    records: list[dict], field: str, *, arm_a: str = "on", arm_b: str = "off"
) -> dict[str, object]:
    pairs: dict[tuple[str, int], dict[str, dict]] = {}
    for record in records:
        pairs.setdefault((record["case"], record["trial"]), {})[record["arm"]] = record
    wins = losses = ties = 0
    for pair in pairs.values():
        if arm_a not in pair or arm_b not in pair:
            continue
        a_value = bool(pair[arm_a][field])
        b_value = bool(pair[arm_b][field])
        wins += a_value and not b_value
        losses += b_value and not a_value
        ties += a_value == b_value
    return {
        f"{arm_a}_wins": wins,
        f"{arm_b}_wins": losses,
        "ties": ties,
        f"{arm_a}_minus_{arm_b}_ci95_case_clustered": _clustered_delta_ci(records, field, arm_a=arm_a, arm_b=arm_b),
    }


def aggregate(records: list[dict]) -> dict[str, object]:
    arms_present = sorted({row["arm"] for row in records})
    arms: dict[str, dict[str, object]] = {}
    for arm in arms_present:
        rows = [row for row in records if row["arm"] == arm]
        count = len(rows)
        case_rates: dict[str, float] = {}
        for case in sorted({row["case"] for row in rows}):
            case_rows = [row for row in rows if row["case"] == case]
            case_rates[case] = sum(row["correct_landed"] for row in case_rows) / len(case_rows)
        case_unique_rates = []
        for case in sorted({row["case"] for row in rows}):
            hashes = {
                row["change"]["patch_hash"] for row in rows if row["case"] == case
            }
            case_count = sum(row["case"] == case for row in rows)
            case_unique_rates.append(len(hashes) / case_count)
        arms[arm] = {
            "runs": count,
            "correct_landed": sum(row["correct_landed"] for row in rows),
            "correct_landed_rate": round(sum(row["correct_landed"] for row in rows) / count, 3)
            if count
            else 0.0,
            "regressions_shipped": sum(row["regression_shipped"] for row in rows),
            "incomplete_refactors_shipped": sum(row["incomplete_refactor_shipped"] for row in rows),
            "escalations": sum(row["status"] == "skipped-needs-human" for row in rows),
            "initial_correct_landed": sum(row["initial"]["correct_landed"] for row in rows),
            "initial_correct_landed_rate": round(
                sum(row["initial"]["correct_landed"] for row in rows) / count, 3
            )
            if count
            else 0.0,
            "case_macro_correct_landed_rate": round(sum(case_rates.values()) / len(case_rates), 3)
            if case_rates
            else 0.0,
            "safe_escalation_rate": round(
                sum(row["status"] == "skipped-needs-human" for row in rows) / count, 3
            )
            if count
            else 0.0,
            "model_calls": sum(row["usage"]["model_calls"] for row in rows),
            "input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
            "output_tokens": sum(row["usage"]["output_tokens"] for row in rows),
            "cache_read_tokens": sum(row["usage"]["cache_read_tokens"] for row in rows),
            "cache_write_tokens": sum(row["usage"]["cache_write_tokens"] for row in rows),
            "tokens": sum(row["tokens"] for row in rows),
            "cost_dollars": round(sum(row["usage"]["cost_dollars"] or 0.0 for row in rows), 6)
            if any(row["usage"]["cost_dollars"] is not None for row in rows)
            else None,
            "seconds": round(sum(row["seconds"] for row in rows), 3),
            "end_to_end_seconds": round(
                sum(row["timing"]["end_to_end_seconds"] for row in rows), 3
            ),
            "required_path_recall": round(
                sum(row["change"]["required_path_recall"] or 0.0 for row in rows) / count,
                3,
            )
            if count
            else 0.0,
            "unrelated_edit_precision": round(
                sum(row["change"]["unrelated_edit_precision"] or 0.0 for row in rows) / count,
                3,
            )
            if count
            else 0.0,
            "total_churn": sum(row["change"]["total_churn"] for row in rows),
            "unique_patch_rate": round(
                sum(case_unique_rates) / len(case_unique_rates), 3
            )
            if case_unique_rates
            else 0.0,
        }
    on_rows = [row for row in records if row["arm"] == "on"]
    rejected_initial = [
        row for row in on_rows if row["initial"].get("verification_status") == "rolled-back"
    ]
    initial_bad = [row for row in rejected_initial if not row["initial"]["correct_landed"]]
    false_rejections = [row for row in rejected_initial if row["initial"]["correct_landed"]]
    gate_rejections: dict[str, int] = {}
    all_attempts = [attempt for row in on_rows for attempt in row.get("attempts", [])]
    for attempt in all_attempts:
        if gate := attempt.get("failure_gate"):
            gate_rejections[str(gate)] = gate_rejections.get(str(gate), 0) + 1
    proposal_errors = [
        row.get("error_class") for row in records if row.get("error_class") is not None
    ] + [
        attempt.get("error_class")
        for attempt in all_attempts
        if attempt.get("error_class") is not None
    ]
    result: dict[str, object] = {
        "arms": arms,
        "paired_final": _paired_summary(records, "correct_landed"),
        "paired_initial": _paired_summary(
            [
                {**row, "initial_correct_landed": row["initial"]["correct_landed"]}
                for row in records
            ],
            "initial_correct_landed",
        ),
        "harness": {
            "initial_rejections": len(rejected_initial),
            "initial_bad_proposals_rejected": len(initial_bad),
            "bad_proposals_caught_or_safely_escalated": sum(
                not row["regression_shipped"] for row in initial_bad
            ),
            "false_rejections": len(false_rejections),
            "repair_successes": sum(row["correct_landed"] for row in rejected_initial),
            "repair_success_rate": round(
                sum(row["correct_landed"] for row in rejected_initial) / len(rejected_initial),
                3,
            )
            if rejected_initial
            else None,
            "rejections_by_gate": gate_rejections,
            "rollback_integrity_failures": sum(
                not bool(attempt.get("rollback_integrity")) for attempt in all_attempts
            ),
        },
        "reliability": {
            "configuration_failures": proposal_errors.count("configuration_failure"),
            "provider_failures": proposal_errors.count("provider_failure"),
            "malformed_responses": proposal_errors.count("malformed_response"),
            "invalid_patches": proposal_errors.count("invalid_patch"),
        },
    }
    if "agentic" in arms_present:
        result["paired_agentic_vs_off"] = _paired_summary(
            records, "correct_landed", arm_a="agentic", arm_b="off"
        )
    if "agentic+harness" in arms_present:
        result["paired_agentic_harness_vs_off"] = _paired_summary(
            records, "correct_landed", arm_a="agentic+harness", arm_b="off"
        )
        result["paired_agentic_harness_vs_agentic"] = _paired_summary(
            records, "correct_landed", arm_a="agentic+harness", arm_b="agentic"
        )
    return result


def run(
    backend: Backend,
    cases: tuple[CaseAdapter, ...],
    trials: int,
    max_retries: int,
    pricing: Pricing | None = None,
    agentic_backend: "AgenticBackend | None" = None,
    agentic_mcp_backend: "AgenticHarnessBackend | None" = None,
) -> dict:
    pricing = pricing or Pricing()
    run_id = uuid.uuid4().hex
    records: list[dict] = []
    for trial in range(trials):
        for case in cases:
            records.extend(_run_pair(case, backend, trial, max_retries, pricing, agentic_backend, agentic_mcp_backend))
    aggregate_result = aggregate(records)
    reliability = cast(dict[str, int], aggregate_result["reliability"])
    infrastructure_failures = (
        reliability["configuration_failures"] + reliability["provider_failures"]
    )
    return {
        "status": "invalid-infrastructure" if infrastructure_failures else "valid",
        "meta": {
            "schema_version": 2,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": backend.name,
            "model": backend.name,
            "provider": getattr(backend, "provider", "test"),
            "temperature": 0,
            "git_revision": _git_revision(),
            "release": os.environ.get("SENTRY_RELEASE") or _git_revision(),
            "methodology": "independent full-system proposals",
            "initial_user_prompt": USER_PROMPT,
            "cases": [case.name for case in cases],
            "trials": trials,
            "max_retries": max_retries,
            "initial_model_calls_per_arm": 1,
            "pricing_per_mtok": asdict(pricing),
        },
        "records": records,
        "aggregate": aggregate_result,
    }


def _load_env(name: str) -> str | None:
    if value := os.environ.get(name):
        return value
    path = REPO_ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return None


def _git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


class HttpBackend:
    def __init__(self, provider: str, model: str, base_url: str, timeout: int = 300) -> None:
        self.provider = provider
        self.model = model
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, prompt: str) -> Completion:
        if self.provider == "anthropic":
            key = _load_env("ANTHROPIC_API_KEY")
            if not key:
                return Completion(
                    "",
                    Usage(),
                    0.0,
                    "ANTHROPIC_API_KEY is not configured",
                    "configuration_failure",
                )
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            }
            body = {
                "model": self.model,
                "max_tokens": 8192,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            url = self.base_url + "/chat/completions"
            headers = {"Content-Type": "application/json"}
            body = {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        started = time.perf_counter()
        try:
            request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
            if self.provider == "anthropic":
                text = "".join(
                    block.get("text", "")
                    for block in data["content"]
                    if block.get("type") == "text"
                )
                raw_usage = data.get("usage", {})
                usage = Usage(
                    input_tokens=int(raw_usage.get("input_tokens", 0)),
                    output_tokens=int(raw_usage.get("output_tokens", 0)),
                    cache_read_tokens=int(raw_usage.get("cache_read_input_tokens", 0)),
                    cache_write_tokens=int(raw_usage.get("cache_creation_input_tokens", 0)),
                )
            else:
                text = data["choices"][0]["message"]["content"]
                raw_usage = data.get("usage", {})
                cached = int(raw_usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
                usage = Usage(
                    input_tokens=max(int(raw_usage.get("prompt_tokens", 0)) - cached, 0),
                    output_tokens=int(raw_usage.get("completion_tokens", 0)),
                    cache_read_tokens=cached,
                )
            return Completion(text, usage, round(time.perf_counter() - started, 3))
        except (
            urllib.error.URLError,
            TimeoutError,
            KeyError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            capture_exception(
                exc,
                component="benchmark",
                phase="provider_request",
                tags={"model": self.model, "provider": self.provider},
            )
            return Completion(
                "",
                Usage(),
                round(time.perf_counter() - started, 3),
                str(exc),
                "provider_failure",
            )


_AGENTIC_TOOLS: list[dict] = [
    {
        "name": "list_files",
        "description": "List Python source files in the repository (excludes __pycache__ and .venv).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write complete new content to a source file. Rejected for paths under tests/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
                "content": {"type": "string", "description": "Complete new file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a shell command in the repository root. Output is capped at 2 000 chars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        },
    },
]

_AGENTIC_SYSTEM = (
    "You are an autonomous Python refactoring agent. "
    "Use tools to explore the repository, apply behavior-preserving structural refactors, "
    "and verify your work by running tests. Do not edit or create any files under tests/. "
    "Stop calling tools once the refactoring is complete and tests pass."
)

_AGENTIC_MCP_TOOLS: list[dict] = [
    {
        "name": "list_files",
        "description": "List Python source files in the repository (excludes __pycache__ and .venv).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "analyze_file",
        "description": (
            "Run Refactorika structural analysis on a file or directory. "
            "Returns ranked opportunities: long functions, deep nesting, import issues, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_duplicates",
        "description": (
            "Find duplicate or near-duplicate functions in a file or directory "
            "using structural fingerprinting and semantic similarity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_dead_code",
        "description": (
            "Find unreachable symbols via call-graph reachability analysis. "
            "Returns dead functions/classes ranked by confidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "apply_and_verify",
        "description": (
            "Submit a mutation for verification. Runs parse → ruff → pyright → pytest. "
            "Commits the file on success; rolls back atomically and returns diagnostics on failure. "
            "This is the ONLY way to modify source files — do not use write_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
                "new_content": {"type": "string", "description": "Complete new file content"},
                "refactor_kind": {
                    "type": "string",
                    "description": "Kind of refactor: e.g. flatten_nesting, extract_function, consolidate_duplicate, remove_dead_code",
                },
            },
            "required": ["path", "new_content", "refactor_kind"],
        },
    },
    {
        "name": "apply_and_verify_multi",
        "description": (
            "Submit a multi-file mutation for atomic verification. Snapshots all files, runs "
            "parse → ruff → pyright → pytest across all of them, commits all on success, or "
            "restores all originals on failure. Required for renames, moves, and duplicate "
            "consolidation that touch ≥2 files at once."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "object",
                    "description": "Map of {relative_path: complete_new_content} for every file to touch",
                    "additionalProperties": {"type": "string"},
                },
                "refactor_kind": {
                    "type": "string",
                    "description": "Kind of refactor: e.g. rename, move_symbol, consolidate_duplicate",
                },
            },
            "required": ["edits", "refactor_kind"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a read-only shell command (e.g. grep, cat). Output capped at 2 000 chars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        },
    },
]

_AGENTIC_MCP_SYSTEM = (
    "You are an autonomous Python refactoring agent with access to Refactorika's analysis tools. "
    "Workflow: (1) list_files; (2) analyze_file on each source file to find the highest-value "
    "opportunity — do NOT also call find_duplicates or find_dead_code unless analyze_file "
    "explicitly surfaces a duplicate or dead-code issue; (3) read the file; (4) "
    "submit the refactored content immediately via apply_and_verify (single file) or "
    "apply_and_verify_multi (multiple files) — both run parse, lint, type-check, and tests "
    "internally and roll back atomically with diagnostics on failure; (5) repair from the "
    "diagnostics and retry. "
    "You MUST use apply_and_verify or apply_and_verify_multi for all mutations — you cannot "
    "write files directly. "
    "Do NOT use run_bash to run tests or check the environment — apply_and_verify handles all "
    "verification. Do not touch files under tests/. Stop when apply_and_verify reports committed."
)


class AgenticBackend:
    """Tool-use agentic arm: explores and edits files directly via a multi-turn loop."""

    def __init__(
        self,
        model: str,
        api_key: str,
        max_iterations: int = 20,
        bash_timeout: int = 30,
    ) -> None:
        self.model = model
        self.name = f"{model}+tools"
        self._api_key = api_key
        self.max_iterations = max_iterations
        self.bash_timeout = bash_timeout

    def _api_call(self, messages: list[dict]) -> tuple[list[dict], Usage, str | None]:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0,
            "system": _AGENTIC_SYSTEM,
            "tools": _AGENTIC_TOOLS,
            "messages": messages,
        }
        try:
            req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            raw = data.get("usage", {})
            usage = Usage(
                input_tokens=int(raw.get("input_tokens", 0)),
                output_tokens=int(raw.get("output_tokens", 0)),
                cache_read_tokens=int(raw.get("cache_read_input_tokens", 0)),
                cache_write_tokens=int(raw.get("cache_creation_input_tokens", 0)),
            )
            return data["content"], usage, None
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return [], Usage(), str(exc)

    def _execute(self, repo: Path, name: str, inputs: dict) -> str:
        if name == "list_files":
            files = sorted(
                p.relative_to(repo).as_posix()
                for p in repo.rglob("*.py")
                if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
            )
            return "\n".join(files) or "(no Python files)"

        if name == "read_file":
            path = inputs.get("path", "")
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                return "error: path escapes repository"
            return target.read_text() if target.is_file() else f"error: {path} not found"

        if name == "write_file":
            path = inputs.get("path", "")
            content = inputs.get("content", "")
            if path.startswith("tests/"):
                return "error: writes to tests/ are not permitted"
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                return "error: path escapes repository"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            return f"wrote {path}"

        if name == "run_bash":
            command = inputs.get("command", "")
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=self.bash_timeout,
                )
                out = (proc.stdout + proc.stderr).strip()
            except subprocess.TimeoutExpired:
                return f"exit 124\ntimeout after {self.bash_timeout}s"
            if len(out) > 2000:
                out = out[:2000] + "\n...[truncated]"
            return f"exit {proc.returncode}\n{out}" if out else f"exit {proc.returncode}"

        return f"error: unknown tool {name!r}"

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], Usage, float, str | None, int]:
        before = {
            p.relative_to(repo).as_posix(): p.read_text()
            for p in sorted(repo.rglob("*.py"))
            if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
        }
        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        usage = Usage()
        started = time.perf_counter()
        error: str | None = None
        model_calls = 0

        for _ in range(self.max_iterations):
            content, turn_usage, call_error = self._api_call(messages)
            model_calls += 1
            usage.add(turn_usage)
            if call_error:
                error = call_error
                break
            messages.append({"role": "assistant", "content": content})
            tool_calls = [b for b in content if b.get("type") == "tool_use"]
            if not tool_calls:
                break
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": self._execute(repo, call["name"], call.get("input", {})),
                }
                for call in tool_calls
            ]
            messages.append({"role": "user", "content": results})

        seconds = round(time.perf_counter() - started, 3)
        after = {
            p.relative_to(repo).as_posix(): p.read_text()
            for p in sorted(repo.rglob("*.py"))
            if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
        }
        edits = {path: text for path, text in after.items() if before.get(path) != text}
        return edits, usage, seconds, error, model_calls


class AgenticHarnessBackend:
    """Agentic arm with Refactorika MCP tools: analysis + apply_and_verify gate stack."""

    def __init__(
        self,
        model: str,
        api_key: str,
        max_iterations: int = 20,
        bash_timeout: int = 30,
    ) -> None:
        self.model = model
        self.name = f"{model}+harness"
        self._api_key = api_key
        self.max_iterations = max_iterations
        self.bash_timeout = bash_timeout

    def _api_call(self, messages: list[dict]) -> tuple[list[dict], Usage, str | None]:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0,
            "system": _AGENTIC_MCP_SYSTEM,
            "tools": _AGENTIC_MCP_TOOLS,
            "messages": messages,
        }
        try:
            req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            raw = data.get("usage", {})
            usage = Usage(
                input_tokens=int(raw.get("input_tokens", 0)),
                output_tokens=int(raw.get("output_tokens", 0)),
                cache_read_tokens=int(raw.get("cache_read_input_tokens", 0)),
                cache_write_tokens=int(raw.get("cache_creation_input_tokens", 0)),
            )
            return data["content"], usage, None
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return [], Usage(), str(exc)

    def _execute(self, repo: Path, storage: Storage, name: str, inputs: dict, gate_log: list[dict]) -> str:
        if name == "list_files":
            files = sorted(
                p.relative_to(repo).as_posix()
                for p in repo.rglob("*.py")
                if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
            )
            return "\n".join(files) or "(no Python files)"

        if name == "read_file":
            path = inputs.get("path", "")
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                return "error: path escapes repository"
            return target.read_text() if target.is_file() else f"error: {path} not found"

        if name == "analyze_file":
            path = inputs.get("path", "")
            target = repo / path
            if not target.exists():
                return f"error: {path} not found"
            try:
                result = _rf_analyze_file(str(target), storage)
                return json.dumps(
                    result.to_dict() if hasattr(result, "to_dict") else vars(result),
                    default=str,
                )
            except Exception as exc:
                return f"error: {exc}"

        if name == "find_duplicates":
            path = inputs.get("path", "")
            target = repo / path
            if not target.exists():
                return f"error: {path} not found"
            try:
                vi = VectorIndex(storage)
                result = _rf_find_duplicates(str(target), storage, vi)
                return json.dumps(result, default=str)
            except Exception as exc:
                return f"error: {exc}"

        if name == "find_dead_code":
            path = inputs.get("path", "")
            target = repo / path
            if not target.exists():
                return f"error: {path} not found"
            try:
                result = _rf_find_dead_code(str(target), storage)
                return json.dumps(result, default=str)
            except Exception as exc:
                return f"error: {exc}"

        if name == "apply_and_verify":
            path = inputs.get("path", "")
            new_content = inputs.get("new_content", "")
            refactor_kind = inputs.get("refactor_kind", "refactor")
            if path.startswith("tests/"):
                return "error: cannot apply_and_verify on test files"
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                return "error: path escapes repository"
            try:
                record = verify_edits(
                    repo,
                    {path: new_content},
                    test_command=[sys.executable, "-m", "pytest", "-q"],
                    required_gates=REQUIRED_GATES,
                )
                gate_log.append({
                    "tool": name,
                    "path": path,
                    "status": record.status,
                    "gate_details": record.gate_details,
                    "checks": asdict(record.checks),
                })
                if record.status == "committed":
                    return f"committed: {path} ({refactor_kind})"
                return f"rolled-back: {json.dumps(record.gate_details, sort_keys=True)}"
            except ValueError as exc:
                return f"error: {exc}"

        if name == "apply_and_verify_multi":
            edits = inputs.get("edits", {})
            refactor_kind = inputs.get("refactor_kind", "refactor")
            if not isinstance(edits, dict) or not edits:
                return "error: edits must be a non-empty {path: content} object"
            if any(p.startswith("tests/") for p in edits):
                return "error: cannot apply_and_verify_multi on test files"
            for path in edits:
                target = (repo / path).resolve()
                try:
                    target.relative_to(repo.resolve())
                except ValueError:
                    return f"error: path escapes repository: {path}"
            try:
                record = verify_edits(
                    repo,
                    edits,
                    test_command=[sys.executable, "-m", "pytest", "-q"],
                    required_gates=REQUIRED_GATES,
                )
                gate_log.append({
                    "tool": name,
                    "path": list(edits.keys()),
                    "status": record.status,
                    "gate_details": record.gate_details,
                    "checks": asdict(record.checks),
                })
                if record.status == "committed":
                    return f"committed: {list(edits.keys())} ({refactor_kind})"
                return f"rolled-back: {json.dumps(record.gate_details, sort_keys=True)}"
            except ValueError as exc:
                return f"error: {exc}"

        if name == "run_bash":
            command = inputs.get("command", "")
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=self.bash_timeout,
                )
                out = (proc.stdout + proc.stderr).strip()
            except subprocess.TimeoutExpired:
                return f"exit 124\ntimeout after {self.bash_timeout}s"
            if len(out) > 2000:
                out = out[:2000] + "\n...[truncated]"
            return f"exit {proc.returncode}\n{out}" if out else f"exit {proc.returncode}"

        return f"error: unknown tool {name!r}"

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], Usage, float, str | None, int, list[dict]]:
        storage = Storage(redis_url=None, json_path=repo / ".refactorika" / "bench-state.json")

        before = {
            p.relative_to(repo).as_posix(): p.read_text()
            for p in sorted(repo.rglob("*.py"))
            if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
        }
        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        usage = Usage()
        started = time.perf_counter()
        error: str | None = None
        model_calls = 0
        gate_log: list[dict] = []

        for _ in range(self.max_iterations):
            content, turn_usage, call_error = self._api_call(messages)
            model_calls += 1
            usage.add(turn_usage)
            if call_error:
                error = call_error
                break
            messages.append({"role": "assistant", "content": content})
            tool_calls = [b for b in content if b.get("type") == "tool_use"]
            if not tool_calls:
                break
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": self._execute(repo, storage, call["name"], call.get("input", {}), gate_log),
                }
                for call in tool_calls
            ]
            messages.append({"role": "user", "content": results})

        seconds = round(time.perf_counter() - started, 3)
        after = {
            p.relative_to(repo).as_posix(): p.read_text()
            for p in sorted(repo.rglob("*.py"))
            if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
        }
        edits = {path: text for path, text in after.items() if before.get(path) != text}
        return edits, usage, seconds, error, model_calls, gate_log


def main() -> int:
    init_sentry("benchmark")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("anthropic", "openai"), default="anthropic")
    parser.add_argument("--model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--input-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--output-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--cache-read-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--cache-write-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--case", action="append", choices=[case.name for case in CASES])
    parser.add_argument("--calibrate-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--regression-threshold", type=float, default=0.10)
    parser.add_argument("--agentic", action="store_true", help="add agentic tool-use arm (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--agentic-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--agentic-max-iter", type=int, default=20)
    parser.add_argument("--agentic-mcp", action="store_true",
                        help="add agentic+mcp arm (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--agentic-mcp-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--agentic-mcp-max-iter", type=int, default=20)
    args = parser.parse_args()
    selected = tuple(case for case in CASES if not args.case or case.name in args.case)
    agentic_backend: AgenticBackend | None = None
    if args.agentic:
        key = _load_env("ANTHROPIC_API_KEY")
        if not key:
            print("error: --agentic requires ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        agentic_backend = AgenticBackend(args.agentic_model, key, args.agentic_max_iter)
    agentic_mcp_backend: AgenticHarnessBackend | None = None
    if args.agentic_mcp:
        key = _load_env("ANTHROPIC_API_KEY")
        if not key:
            print("error: --agentic-mcp requires ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        agentic_mcp_backend = AgenticHarnessBackend(
            args.agentic_mcp_model, key, args.agentic_mcp_max_iter
        )
    result = (
        {"status": "valid", "calibration": calibrate(selected)}
        if args.calibrate_only
        else run(
            HttpBackend(args.provider, args.model, args.base_url),
            selected,
            args.trials,
            args.max_retries,
            Pricing(
                args.input_cost_per_mtok,
                args.output_cost_per_mtok,
                args.cache_read_cost_per_mtok,
                args.cache_write_cost_per_mtok,
            ),
            agentic_backend,
            agentic_mcp_backend,
        )
    )
    if args.calibrate_only and not result["calibration"]["valid"]:
        result["status"] = "void"
    destination = args.output or REPO_ROOT / "eval" / "results" / "full-system-latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_text(json.dumps(result, indent=2) + "\n")
    except OSError as exc:
        capture_exception(exc, component="benchmark", phase="artifact_write")
        raise
    if args.baseline and not args.calibrate_only:
        try:
            baseline = json.loads(args.baseline.read_text())
            capture_benchmark_regression(result, baseline, threshold=args.regression_threshold)
        except (OSError, json.JSONDecodeError) as exc:
            capture_exception(exc, component="benchmark", phase="baseline_read")
    if "aggregate" in result:
        print(json.dumps(result["aggregate"], indent=2))
    print(f"status: {result['status']} | result: {destination}")
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
