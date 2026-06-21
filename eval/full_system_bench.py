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
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, cast

from refactorika.analysis.audit import build_plan
from refactorika.core.storage import Storage
from refactorika.harness import mark_escalated, verify_edits
from refactorika.observability import (
    capture_benchmark_regression,
    capture_exception,
    init_sentry,
)

from eval.agents.driver import SharedAgentDriver, ToolProvider
from eval.agents.harness_tools import HarnessDeveloperTools
from eval.agents.loop import AgentLoop, LoopBudgets
from eval.agents.metrics import collect_agent_metrics
from eval.agents.prompts import (
    build_edit_prompt as _build_edit_prompt,
)
from eval.agents.prompts import (
    build_harness_context_prompt as _build_harness_context_prompt,
)
from eval.agents.prompts import (
    build_off_prompt as _build_off_prompt,
)
from eval.agents.providers import Completion, HttpProvider, Usage
from eval.agents.schema import AgentResult, TerminationReason
from eval.agents.tools import DeveloperTools
from eval.full_system_cases import ALL_CASES, USER_PROMPT
from eval.full_system_cases.behavior import BehaviorCase
from eval.full_system_cases.multifile import MultiFileCase, structural_failures
from eval.full_system_cases.recovery import RecoveryCase
from eval.full_system_cases.stress import (
    StressCase,
)
from eval.full_system_cases.stress import (
    structural_failures as stress_failures,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_GATES = ("lint", "typecheck", "tests")
DEFAULT_REQUEST_TIMEOUT = 180
DEFAULT_TEST_TIMEOUT = 180
DEFAULT_AGENT_TIMEOUT = 900


@dataclass(frozen=True)
class CaseAdapter:
    name: str
    source: BehaviorCase | MultiFileCase | RecoveryCase | StressCase
    baseline_files: dict[str, str]
    hidden_tests: dict[str, str]
    user_prompt: str
    required_paths: frozenset[str]
    allowed_paths: frozenset[str]


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
class Proposal:
    edits: dict[str, str]
    usage: Usage
    seconds: float
    prompt: str
    plan: str | None = None
    error: str | None = None
    model_calls: int = 1
    error_class: str | None = None
    agent_result: AgentResult | None = None


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

    return _build_off_prompt(case.user_prompt, visible_snapshot(repo))


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
    return _build_harness_context_prompt(
        case.user_prompt,
        audit_plan=plan,
        architecture_notes=_architecture_notes(repo),
    )


def build_edit_prompt(
    case: CaseAdapter, repo: Path, plan: str, *, failure: str | None = None
) -> str:
    return _build_edit_prompt(
        case.user_prompt,
        visible_snapshot(repo),
        plan,
        failure=failure,
    )


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
        error_class=_agent_error_class(error),
        agent_result=getattr(backend, "last_result", None),
    )


def propose_agentic_mcp(
    backend: "AgenticHarnessBackend", case: CaseAdapter, repo: Path
) -> tuple[Proposal, list[dict]]:
    edits, usage, seconds, error, model_calls, gate_log = backend.run(repo, case.user_prompt)
    if error is None and any("gate_crash" in row.get("gate_details", {}) for row in gate_log):
        error = "gate_failure: verification subprocess crashed or timed out"
    if not error and not edits:
        error = "agent made no changes to source files"
    proposal = Proposal(
        edits=edits if not error else {},
        usage=usage,
        seconds=seconds,
        prompt=f"[agentic+harness:{backend.name}] {case.user_prompt}",
        error=error,
        model_calls=model_calls,
        error_class=_agent_error_class(error),
        agent_result=getattr(backend, "last_result", None),
    )
    return proposal, gate_log


def _agent_error_class(error: str | None) -> str | None:
    if error is None:
        return None
    if error.startswith("agent_timeout:"):
        return "timeout_failure"
    if error.startswith("iteration_limit_exceeded:"):
        return "iteration_limit"
    if error.startswith("gate_failure:"):
        return "gate_failure"
    if error.startswith("malformed_provider_response:"):
        return "malformed_response"
    if error.startswith("provider_timeout_or_failure:"):
        return "provider_failure"
    return "agent_failure"


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


def _run_hidden_tests(
    case: CaseAdapter, repo: Path, timeout: int = DEFAULT_TEST_TIMEOUT
) -> tuple[bool, str]:
    for relative, content in case.hidden_tests.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"oracle pytest exceeded {timeout}s") from exc
    finally:
        shutil.rmtree(repo / "tests" / "oracle", ignore_errors=True)
    output = (result.stdout + "\n" + result.stderr).strip().splitlines()
    detail = output[-1] if output else f"pytest exit {result.returncode}"
    return result.returncode == 0, detail


def _run_visible_tests(repo: Path, timeout: int = DEFAULT_TEST_TIMEOUT) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"visible pytest exceeded {timeout}s") from exc
    output = (result.stdout + "\n" + result.stderr).strip().splitlines()
    detail = output[-1] if output else f"pytest exit {result.returncode}"
    return result.returncode == 0, detail


def oracle_grade(case: CaseAdapter, repo: Path) -> tuple[bool, str, list[str]]:
    behavior_pass, detail = _run_hidden_tests(case, repo)
    structure = grade_structure(case, repo)
    return behavior_pass, detail, structure


def calibrate(
    cases: tuple[CaseAdapter, ...] = CASES,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    records = []
    for index, case in enumerate(cases, start=1):
        if progress:
            progress(f"calibration case={case.name} start index={index}/{len(cases)}")
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
            if progress:
                case_valid = (
                    visible
                    and structure_missing
                    and (hidden == (not isinstance(case.source, MultiFileCase)))
                )
                progress(
                    f"calibration case={case.name} complete "
                    f"valid={case_valid} index={index}/{len(cases)}"
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
    parallel_arms: bool = False,
    parallel_fallback_delay: float = 2.0,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix=f"full-{case.name}-") as tmp:
        pair_root = Path(tmp)
        off_repo = materialize(case, Path(tmp) / "off")
        on_repo = materialize(case, Path(tmp) / "on")
        agentic_repo = (
            materialize(case, Path(tmp) / "agentic") if agentic_backend is not None else None
        )
        agentic_mcp_repo = (
            materialize(case, Path(tmp) / "agentic_mcp")
            if agentic_mcp_backend is not None
            else None
        )
        common = {
            "case": case.name,
            "trial": trial,
            "initial_user_prompt": case.user_prompt,
            "case_metadata": getattr(case.source, "benchmark_metadata", {}),
        }
        fallback_arms: set[str] = set()
        arm_names = ["off", "on"]
        if agentic_backend is not None:
            arm_names.append("agentic")
        if agentic_mcp_backend is not None:
            arm_names.append("agentic+harness")
        mode = "parallel" if parallel_arms else "sequential"
        if progress:
            progress(
                f"trial={trial + 1} case={case.name} start mode={mode} "
                f"arms={','.join(arm_names)}"
            )

        on_started = time.perf_counter()
        audit_started = time.perf_counter()
        harness_prompt = build_harness_prompt(case, on_repo)
        audit_seconds = time.perf_counter() - audit_started

        off_started = time.perf_counter()
        if parallel_arms:
            workers = 2 + int(agentic_backend is not None) + int(agentic_mcp_backend is not None)
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="benchmark-arm"
            ) as pool:
                futures: dict[str, Future] = {
                    "off": pool.submit(propose_off, backend, case, off_repo),
                    "on": pool.submit(
                        propose_on,
                        backend,
                        case,
                        on_repo,
                        harness_prompt=harness_prompt,
                    ),
                }
                if agentic_backend is not None and agentic_repo is not None:
                    futures["agentic"] = pool.submit(
                        propose_agentic, agentic_backend, case, agentic_repo
                    )
                if agentic_mcp_backend is not None and agentic_mcp_repo is not None:
                    futures["agentic+harness"] = pool.submit(
                        propose_agentic_mcp, agentic_mcp_backend, case, agentic_mcp_repo
                    )
                parallel_results: dict[str, object] = {}
                future_arms = {future: arm for arm, future in futures.items()}
                for future in as_completed(future_arms):
                    arm = future_arms[future]
                    try:
                        parallel_results[arm] = future.result()
                        if progress:
                            progress(
                                f"trial={trial + 1} case={case.name} arm={arm} " "proposal-finished"
                            )
                    except Exception as exc:  # noqa: BLE001 - retry once outside the pool
                        capture_exception(
                            exc,
                            component="benchmark",
                            phase="parallel_arm",
                            tags={"arm": arm, "case": case.name, "trial": trial},
                        )
                        fallback_arms.add(arm)
                        if progress:
                            progress(
                                f"trial={trial + 1} case={case.name} arm={arm} "
                                f"parallel-error={type(exc).__name__}"
                            )

            off = (
                cast(Proposal, parallel_results.get("off")) if "off" not in fallback_arms else None
            )
            initial_on = (
                cast(Proposal, parallel_results.get("on")) if "on" not in fallback_arms else None
            )
            agentic = (
                cast(Proposal, parallel_results.get("agentic"))
                if agentic_backend is not None and "agentic" not in fallback_arms
                else None
            )
            agentic_mcp_result = (
                cast(tuple[Proposal, list[dict]], parallel_results.get("agentic+harness"))
                if agentic_mcp_backend is not None and "agentic+harness" not in fallback_arms
                else None
            )

            retryable = {
                "provider_failure",
                "timeout_failure",
                "agent_failure",
                "gate_failure",
            }
            for arm, proposal in (
                ("off", off),
                ("on", initial_on),
                ("agentic", agentic),
                (
                    "agentic+harness",
                    agentic_mcp_result[0] if agentic_mcp_result is not None else None,
                ),
            ):
                if proposal is not None and proposal.error_class in retryable:
                    fallback_arms.add(arm)

            if fallback_arms:
                if progress:
                    progress(
                        f"trial={trial + 1} case={case.name} sequential-fallback "
                        f"arms={','.join(sorted(fallback_arms))} "
                        f"delay={parallel_fallback_delay}s"
                    )
                if parallel_fallback_delay > 0:
                    time.sleep(parallel_fallback_delay)
            if "off" in fallback_arms:
                off = propose_off(backend, case, off_repo)
            if "on" in fallback_arms:
                initial_on = propose_on(backend, case, on_repo, harness_prompt=harness_prompt)
            if (
                "agentic" in fallback_arms
                and agentic_backend is not None
                and agentic_repo is not None
            ):
                shutil.rmtree(agentic_repo)
                agentic_repo = materialize(case, agentic_repo)
                agentic = propose_agentic(agentic_backend, case, agentic_repo)
            if (
                "agentic+harness" in fallback_arms
                and agentic_mcp_backend is not None
                and agentic_mcp_repo is not None
            ):
                shutil.rmtree(agentic_mcp_repo)
                agentic_mcp_repo = materialize(case, agentic_mcp_repo)
                agentic_mcp_result = propose_agentic_mcp(
                    agentic_mcp_backend, case, agentic_mcp_repo
                )
            assert off is not None and initial_on is not None
        else:
            if progress:
                progress(f"trial={trial + 1} case={case.name} arm=off proposal-start")
            off = propose_off(backend, case, off_repo)
            initial_on = None
            agentic = None
            agentic_mcp_result = None

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
        if parallel_arms:
            off_end_to_end = off.seconds + off_apply_seconds + off_grading_seconds

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
            if progress:
                progress(
                    f"trial={trial + 1} case={case.name} arm=on " f"proposal-attempt={attempt + 1}"
                )
            current = (
                initial_on
                if attempt == 0 and initial_on is not None
                else propose_on(
                    backend, case, on_repo, harness_prompt=harness_prompt, failure=failure
                )
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
                if current.error_class in {
                    "configuration_failure",
                    "provider_failure",
                    "timeout_failure",
                    "agent_failure",
                    "gate_failure",
                }:
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
                    attempt_error_class = "gate_failure" if failure_gate == "gate_crash" else None
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
                            "error_class": attempt_error_class,
                            "failure_gate": failure_gate,
                            "checks": asdict(final.checks),
                            "rollback_integrity": rollback_integrity,
                        }
                    )
                    if attempt == 0:
                        initial_checks = asdict(final.checks)
                    if final.status == "committed":
                        break
                    if attempt_error_class:
                        failure = final.failure_reason
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
        if parallel_arms:
            on_end_to_end = audit_seconds + model_seconds + gate_seconds + grading_seconds

        common["execution"] = {
            "parallel_arms": parallel_arms,
            "sequential_fallback": bool(fallback_arms),
            "fallback_arms": sorted(fallback_arms),
            "fallback_delay_seconds": parallel_fallback_delay if fallback_arms else 0.0,
        }
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
            assert agentic_repo is not None
            agentic_started = time.perf_counter()
            if agentic is None:
                if progress:
                    progress(f"trial={trial + 1} case={case.name} arm=agentic start")
                agentic = propose_agentic(agentic_backend, case, agentic_repo)
            agentic_end_to_end = time.perf_counter() - agentic_started
            if parallel_arms:
                agentic_end_to_end = agentic.seconds
            agentic_landed = not bool(agentic.error)
            agentic_behavior, agentic_detail, agentic_structure = (
                oracle_grade(case, agentic_repo)
                if agentic_landed
                else (False, agentic.error or "not landed", [])
            )
            agentic_outcome = _outcome(agentic_landed, agentic_behavior, agentic_structure)
            if agentic.agent_result is not None:
                agentic.agent_result.metadata["sequential_fallback"] = "agentic" in fallback_arms
            agentic_metrics = (
                collect_agent_metrics(agentic.agent_result).to_dict()
                if agentic.agent_result is not None
                else None
            )
            records.append(
                {
                    **common,
                    "arm": "agentic",
                    "status": "shipped" if agentic_landed else "error",
                    **agentic_outcome,
                    "oracle_pass": agentic_behavior if agentic_landed else None,
                    "structural_failures": agentic_structure,
                    "detail": agentic_detail,
                    "error_class": agentic.error_class,
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
                    "plan": (
                        asdict(agentic.agent_result.plan)
                        if agentic.agent_result is not None
                        and agentic.agent_result.plan is not None
                        else None
                    ),
                    "patch": agentic.edits,
                    "agent_metrics": agentic_metrics,
                    "tool_events": (
                        [asdict(event) for event in agentic.agent_result.tool_events]
                        if agentic.agent_result is not None
                        else []
                    ),
                    "termination_reason": (
                        agentic.agent_result.termination_reason.value
                        if agentic.agent_result is not None
                        else None
                    ),
                }
            )

        if agentic_mcp_backend is not None:
            assert agentic_mcp_repo is not None
            agentic_mcp_started = time.perf_counter()
            if agentic_mcp_result is None:
                if progress:
                    progress(f"trial={trial + 1} case={case.name} arm=agentic+harness start")
                agentic_mcp_result = propose_agentic_mcp(
                    agentic_mcp_backend, case, agentic_mcp_repo
                )
            agentic_mcp, agentic_mcp_gate_log = agentic_mcp_result
            agentic_mcp_end_to_end = time.perf_counter() - agentic_mcp_started
            if parallel_arms:
                agentic_mcp_end_to_end = agentic_mcp.seconds
            agentic_mcp_landed = not bool(agentic_mcp.error)
            agentic_mcp_behavior, agentic_mcp_detail, agentic_mcp_structure = (
                oracle_grade(case, agentic_mcp_repo)
                if agentic_mcp_landed
                else (False, agentic_mcp.error or "not landed", [])
            )
            agentic_mcp_outcome = _outcome(
                agentic_mcp_landed, agentic_mcp_behavior, agentic_mcp_structure
            )
            if agentic_mcp.agent_result is not None:
                agentic_mcp.agent_result.metadata["sequential_fallback"] = (
                    "agentic+harness" in fallback_arms
                )
            agentic_mcp_metrics = (
                collect_agent_metrics(agentic_mcp.agent_result).to_dict()
                if agentic_mcp.agent_result is not None
                else None
            )
            records.append(
                {
                    **common,
                    "arm": "agentic+harness",
                    "status": "shipped" if agentic_mcp_landed else "error",
                    **agentic_mcp_outcome,
                    "oracle_pass": agentic_mcp_behavior if agentic_mcp_landed else None,
                    "structural_failures": agentic_mcp_structure,
                    "detail": agentic_mcp_detail,
                    "error_class": agentic_mcp.error_class,
                    "tokens": agentic_mcp.usage.total,
                    "seconds": round(agentic_mcp.seconds, 3),
                    "initial": dict(agentic_mcp_outcome),
                    "usage": _usage_record(agentic_mcp.usage, agentic_mcp.model_calls, pricing),
                    "timing": {
                        "audit_seconds": 0.0,
                        "model_seconds": round(agentic_mcp.seconds, 3),
                        # Gate time is currently embedded in the model loop.
                        "gate_seconds": 0.0,
                        "application_seconds": 0.0,
                        "grading_seconds": 0.0,
                        "workflow_seconds": round(agentic_mcp.seconds, 3),
                        "end_to_end_seconds": round(agentic_mcp_end_to_end, 3),
                    },
                    "gate_log": agentic_mcp_gate_log,
                    "gate_calls": len(agentic_mcp_gate_log),
                    "gate_commits": sum(
                        1 for g in agentic_mcp_gate_log if g["status"] == "committed"
                    ),
                    "gate_rollbacks": sum(
                        1 for g in agentic_mcp_gate_log if g["status"] == "rolled-back"
                    ),
                    "change": _change_metrics(case, agentic_mcp.edits, agentic_mcp_structure),
                    "plan": (
                        asdict(agentic_mcp.agent_result.plan)
                        if agentic_mcp.agent_result is not None
                        and agentic_mcp.agent_result.plan is not None
                        else None
                    ),
                    "patch": agentic_mcp.edits,
                    "agent_metrics": agentic_mcp_metrics,
                    "tool_events": (
                        [asdict(event) for event in agentic_mcp.agent_result.tool_events]
                        if agentic_mcp.agent_result is not None
                        else []
                    ),
                    "termination_reason": (
                        agentic_mcp.agent_result.termination_reason.value
                        if agentic_mcp.agent_result is not None
                        else None
                    ),
                }
            )

        if progress:
            for record in records:
                progress(
                    f"trial={trial + 1} case={case.name} arm={record['arm']} "
                    f"complete status={record['status']} "
                    f"correct_landed={record['correct_landed']} "
                    f"calls={record['usage']['model_calls']} "
                    f"seconds={record['timing']['end_to_end_seconds']}"
                )
            progress(f"trial={trial + 1} case={case.name} complete")
        return records


def _clustered_delta_ci(
    records: list[dict],
    field: str,
    *,
    arm_a: str = "on",
    arm_b: str = "off",
    samples: int = 5000,
    seed: int = 7,
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
        f"{arm_a}_minus_{arm_b}_ci95_case_clustered": _clustered_delta_ci(
            records, field, arm_a=arm_a, arm_b=arm_b
        ),
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
            hashes = {row["change"]["patch_hash"] for row in rows if row["case"] == case}
            case_count = sum(row["case"] == case for row in rows)
            case_unique_rates.append(len(hashes) / case_count)
        termination_reasons: dict[str, int] = {}
        for row in rows:
            if reason := row.get("termination_reason"):
                termination_reasons[str(reason)] = termination_reasons.get(str(reason), 0) + 1
        model_calls = [int(row["usage"]["model_calls"]) for row in rows]
        audit_failures = sum(
            int((row.get("agent_metrics") or {}).get("completion_audit_failures") or 0)
            for row in rows
        )
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
            "median_model_calls": statistics.median(model_calls) if model_calls else 0,
            "termination_reasons": termination_reasons,
            "completion_audit_failures": audit_failures,
            "tool_events": sum(len(row.get("tool_events", [])) for row in rows),
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
            "unique_patch_rate": round(sum(case_unique_rates) / len(case_unique_rates), 3)
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
            "timeout_failures": proposal_errors.count("timeout_failure"),
            "agent_failures": proposal_errors.count("agent_failure"),
            "gate_failures": proposal_errors.count("gate_failure"),
            "iteration_limit_failures": proposal_errors.count("iteration_limit"),
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
    parallel_arms: bool = False,
    parallel_fallback_delay: float = 2.0,
    progress: Callable[[str], None] | None = None,
) -> dict:
    pricing = pricing or Pricing()
    run_id = uuid.uuid4().hex
    records: list[dict] = []
    for trial in range(trials):
        for case in cases:
            records.extend(
                _run_pair(
                    case,
                    backend,
                    trial,
                    max_retries,
                    pricing,
                    agentic_backend,
                    agentic_mcp_backend,
                    parallel_arms,
                    parallel_fallback_delay,
                    progress,
                )
            )
    aggregate_result = aggregate(records)
    reliability = cast(dict[str, int], aggregate_result["reliability"])
    infrastructure_failures = (
        reliability["configuration_failures"]
        + reliability["provider_failures"]
        + reliability["timeout_failures"]
        + reliability["agent_failures"]
        + reliability["gate_failures"]
        + reliability["iteration_limit_failures"]
    )
    return {
        "status": "invalid-infrastructure" if infrastructure_failures else "valid",
        "meta": {
            "schema_version": 3,
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
            "parallel_arms": parallel_arms,
            "parallel_fallback_delay": parallel_fallback_delay,
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


def _console_progress(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


class _SharedAgentBackend:
    """Compatibility adapter from the shared workflow to legacy benchmark records."""

    arm = "agentic"
    tools_class: type[DeveloperTools] = DeveloperTools
    _provider: ToolProvider
    max_iterations: int
    bash_timeout: int
    request_timeout: int
    agent_timeout: int
    gate_timeout: int

    def _run_shared(
        self,
        repo: Path,
        user_prompt: str,
    ) -> tuple[AgentResult, list[dict]]:
        tools = self.tools_class(
            repo,
            timeout=self.gate_timeout if self.arm == "agentic+harness" else self.bash_timeout,
        )
        driver = SharedAgentDriver(
            self._provider,
            tools,
            arm=self.arm,
            case="full-system",
            trial=0,
            user_prompt=user_prompt,
            timeout=float(self.request_timeout),
        )
        result = AgentLoop(
            driver,
            LoopBudgets(
                total_calls=self.max_iterations,
                timeout_seconds=float(self.agent_timeout),
            ),
        ).run()
        self.last_result = result
        gate_log = [
            {
                "tool": event.tool,
                "status": "committed" if event.status == "ok" else "rolled-back",
                "gate_details": (
                    {event.error_class: "shared harness tool rejected the patch"}
                    if event.error_class
                    else {}
                ),
                "seconds": event.seconds,
            }
            for event in result.tool_events
            if event.tool == "submit_patch"
        ]
        return result, gate_log

    @staticmethod
    def _legacy_usage(result: AgentResult) -> Usage:
        return Usage(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
        )

    @staticmethod
    def _legacy_error(result: AgentResult) -> str | None:
        reason = result.termination_reason
        if reason in {
            TerminationReason.COMPLETED,
            TerminationReason.COMPLETED_AFTER_REPAIR,
        }:
            return None
        detail = result.error or reason.value
        if reason is TerminationReason.ITERATION_LIMIT:
            return "iteration_limit_exceeded: " f"reached {result.model_calls} model calls"
        if reason is TerminationReason.AGENT_TIMEOUT:
            return f"agent_timeout: {detail}"
        if reason is TerminationReason.PROVIDER_FAILURE:
            return f"provider_timeout_or_failure: {detail}"
        if reason is TerminationReason.MALFORMED_RESPONSE:
            return f"malformed_provider_response: {detail}"
        if reason is TerminationReason.GATE_FAILURE:
            return f"gate_failure: {detail}"
        return f"{reason.value}: {detail}"


class AgenticBackend(_SharedAgentBackend):
    """Shared agent loop with direct, atomic developer-tool mutations."""

    def __init__(
        self,
        model: str,
        api_key: str,
        max_iterations: int = 20,
        bash_timeout: int = 30,
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
        agent_timeout: int = DEFAULT_AGENT_TIMEOUT,
    ) -> None:
        self.model = model
        self.name = f"{model}+tools"
        self._provider = HttpProvider(
            "anthropic",
            model,
            api_key=api_key,
            timeout=request_timeout,
        )
        self.max_iterations = max_iterations
        self.bash_timeout = bash_timeout
        self.request_timeout = request_timeout
        self.agent_timeout = agent_timeout
        self.gate_timeout = bash_timeout

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], Usage, float, str | None, int]:
        result, _ = self._run_shared(repo, user_prompt)
        return (
            result.edits,
            self._legacy_usage(result),
            result.seconds,
            self._legacy_error(result),
            result.model_calls,
        )


class AgenticHarnessBackend(_SharedAgentBackend):
    """Shared agent loop with Refactorika bootstrap and verified mutations."""

    arm = "agentic+harness"
    tools_class = HarnessDeveloperTools

    def __init__(
        self,
        model: str,
        api_key: str,
        max_iterations: int = 20,
        bash_timeout: int = 30,
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
        agent_timeout: int = DEFAULT_AGENT_TIMEOUT,
        gate_timeout: int = DEFAULT_TEST_TIMEOUT,
    ) -> None:
        self.model = model
        self.name = f"{model}+harness"
        self._provider = HttpProvider(
            "anthropic",
            model,
            api_key=api_key,
            timeout=request_timeout,
        )
        self.max_iterations = max_iterations
        self.bash_timeout = bash_timeout
        self.request_timeout = request_timeout
        self.agent_timeout = agent_timeout
        self.gate_timeout = gate_timeout

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], Usage, float, str | None, int, list[dict]]:
        result, gate_log = self._run_shared(repo, user_prompt)
        return (
            result.edits,
            self._legacy_usage(result),
            result.seconds,
            self._legacy_error(result),
            result.model_calls,
            gate_log,
        )


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
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT)
    parser.add_argument("--agent-timeout", type=int, default=DEFAULT_AGENT_TIMEOUT)
    parser.add_argument("--shell-timeout", type=int, default=30)
    parser.add_argument("--gate-timeout", type=int, default=DEFAULT_TEST_TIMEOUT)
    parser.add_argument(
        "--parallel-arms",
        action="store_true",
        help="run available arms concurrently and retry failed parallel calls sequentially",
    )
    parser.add_argument("--parallel-fallback-delay", type=float, default=2.0)
    parser.add_argument(
        "--quiet-progress",
        action="store_true",
        help="suppress per-case and per-arm progress messages on stderr",
    )
    parser.add_argument(
        "--agentic",
        action="store_true",
        help="add agentic tool-use arm (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument("--agentic-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--agentic-max-iter", type=int, default=20)
    parser.add_argument(
        "--agentic-mcp",
        action="store_true",
        help="add agentic+mcp arm (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument("--agentic-mcp-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--agentic-mcp-max-iter", type=int, default=20)
    args = parser.parse_args()
    progress = None if args.quiet_progress else _console_progress
    selected = tuple(case for case in CASES if not args.case or case.name in args.case)
    agentic_backend: AgenticBackend | None = None
    if args.agentic:
        key = _load_env("ANTHROPIC_API_KEY")
        if not key:
            print("error: --agentic requires ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        agentic_backend = AgenticBackend(
            args.agentic_model,
            key,
            args.agentic_max_iter,
            args.shell_timeout,
            args.request_timeout,
            args.agent_timeout,
        )
    agentic_mcp_backend: AgenticHarnessBackend | None = None
    if args.agentic_mcp:
        key = _load_env("ANTHROPIC_API_KEY")
        if not key:
            print("error: --agentic-mcp requires ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        agentic_mcp_backend = AgenticHarnessBackend(
            args.agentic_mcp_model,
            key,
            args.agentic_mcp_max_iter,
            args.shell_timeout,
            args.request_timeout,
            args.agent_timeout,
            args.gate_timeout,
        )
    destination = args.output or REPO_ROOT / "eval" / "results" / "full-system-latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = (
            {"status": "valid", "calibration": calibrate(selected, progress)}
            if args.calibrate_only
            else run(
                HttpProvider(
                    args.provider,
                    args.model,
                    api_key=_load_env("ANTHROPIC_API_KEY"),
                    base_url=args.base_url,
                    timeout=args.request_timeout,
                ),
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
                args.parallel_arms,
                args.parallel_fallback_delay,
                progress,
            )
        )
        if args.calibrate_only and not result["calibration"]["valid"]:
            result["status"] = "void"
    except Exception as exc:  # noqa: BLE001 - preserve a failure artifact for diagnosis
        capture_exception(exc, component="benchmark", phase="run")
        result = {
            "status": "invalid-infrastructure",
            "error": {
                "class": type(exc).__name__,
                "message": str(exc),
            },
            "meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": args.model,
                "provider": args.provider,
                "git_revision": _git_revision(),
            },
        }
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
