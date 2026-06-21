"""Full-system OFF-vs-ON benchmark for autonomous refactoring.

Both arms start from the exact user request ``refactor this codebase`` and from
separate copies of the same repository. OFF asks the model to form its own plan
before proposing edits. ON uses Refactorika's audit and dependency-ordered plan
to build the model prompt, then routes proposals through atomic verification and
gate-guided retries. Held-out tests are injected only by the final grader.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from refactorika.analysis.audit import build_plan
from refactorika.core.storage import Storage
from refactorika.harness import mark_escalated, verify_edits

from eval.full_system_cases import ALL_CASES, USER_PROMPT
from eval.full_system_cases.behavior import BehaviorCase
from eval.full_system_cases.multifile import MultiFileCase, structural_failures
from eval.full_system_cases.recovery import RecoveryCase

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_GATES = ("lint", "typecheck", "tests")


@dataclass(frozen=True)
class CaseAdapter:
    name: str
    source: BehaviorCase | MultiFileCase | RecoveryCase
    baseline_files: dict[str, str]
    hidden_tests: dict[str, str]
    user_prompt: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Completion:
    text: str
    usage: Usage
    seconds: float
    error: str | None = None


@dataclass
class Proposal:
    edits: dict[str, str]
    usage: Usage
    seconds: float
    prompt: str
    plan: str | None = None
    error: str | None = None


class Backend(Protocol):
    name: str

    def complete(self, prompt: str) -> Completion: ...


def adapt_case(case: object) -> CaseAdapter:
    """Normalize the three fixture families into one runner contract."""

    if isinstance(case, BehaviorCase):
        hidden = dict(case.hidden_tests)
    elif isinstance(case, (MultiFileCase, RecoveryCase)):
        hidden = {"tests/oracle/test_hidden.py": case.hidden_tests}
    else:
        raise TypeError(f"unsupported full-system case: {type(case).__name__}")
    return CaseAdapter(
        name=case.name,
        source=case,
        baseline_files=dict(case.baseline_files),
        hidden_tests=hidden,
        user_prompt=case.user_prompt,
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


def build_off_planning_prompt(case: CaseAdapter, repo: Path) -> str:
    """Let the non-harness agent decide what the generic request means."""

    return (
        "You are the planning stage of an autonomous refactoring agent.\n"
        f"User request (verbatim): {case.user_prompt}\n\n"
        "Inspect the repository snapshot, choose the highest-value behavior-preserving "
        "refactor, identify all affected call sites and compatibility constraints, and "
        "write a concise implementation plan. Hidden tests may exist.\n\n"
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
        return Proposal({}, completion.usage, completion.seconds, prompt, plan, completion.error)
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
        return Proposal({}, completion.usage, completion.seconds, prompt, plan, str(exc))


def propose_off(backend: Backend, case: CaseAdapter, repo: Path) -> Proposal:
    planning_prompt = build_off_planning_prompt(case, repo)
    planning = backend.complete(planning_prompt)
    if planning.error:
        return Proposal({}, planning.usage, planning.seconds, planning_prompt, error=planning.error)
    edit_prompt = build_edit_prompt(case, repo, planning.text)
    completion = backend.complete(edit_prompt)
    usage = Usage(planning.usage.input_tokens, planning.usage.output_tokens)
    usage.add(completion.usage)
    proposal = _decode_patch(completion, edit_prompt, planning.text)
    proposal.usage = usage
    proposal.seconds += planning.seconds
    return proposal


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


def _run_pair(case: CaseAdapter, backend: Backend, trial: int, max_retries: int) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix=f"full-{case.name}-") as tmp:
        off_repo = materialize(case, Path(tmp) / "off")
        on_repo = materialize(case, Path(tmp) / "on")

        off = propose_off(backend, case, off_repo)
        off_error = off.error or _write_patch(off_repo, off.edits)
        off_behavior, off_detail, off_structure = (
            (False, off_error, []) if off_error else oracle_grade(case, off_repo)
        )
        off_correct = not bool(off_error) and off_behavior and not off_structure

        harness_prompt = build_harness_prompt(case, on_repo)
        usage = Usage()
        seconds = 0.0
        final = None
        current: Proposal | None = None
        failure = None
        for attempt in range(max_retries + 1):
            current = propose_on(
                backend, case, on_repo, harness_prompt=harness_prompt, failure=failure
            )
            usage.add(current.usage)
            seconds += current.seconds
            if current.error:
                failure = current.error
            else:
                try:
                    final = verify_edits(
                        on_repo,
                        current.edits,
                        test_command=[sys.executable, "-m", "pytest", "-q"],
                        required_gates=REQUIRED_GATES,
                        retries=attempt,
                    )
                    if final.status == "committed":
                        break
                    failure = json.dumps(final.gate_details, sort_keys=True)
                except ValueError as exc:
                    failure = str(exc)
        committed = final is not None and final.status == "committed"
        if final is not None and not committed:
            mark_escalated(final)
        on_behavior, on_detail, on_structure = (
            oracle_grade(case, on_repo) if committed else (False, "not landed", [])
        )
        on_correct = committed and on_behavior and not on_structure

        common = {"case": case.name, "trial": trial, "initial_user_prompt": case.user_prompt}
        return [
            {
                **common,
                "arm": "off",
                "status": "shipped" if not off_error else "error",
                "landed": not bool(off_error),
                "correct_landed": off_correct,
                "regression_shipped": not bool(off_error) and not off_behavior,
                "incomplete_refactor_shipped": (
                    not bool(off_error) and off_behavior and bool(off_structure)
                ),
                "oracle_pass": off_behavior,
                "structural_failures": off_structure,
                "detail": off_detail,
                "tokens": off.usage.total,
                "seconds": round(off.seconds, 3),
                "plan": off.plan,
                "patch": off.edits,
            },
            {
                **common,
                "arm": "on",
                "status": "committed" if committed else "skipped-needs-human",
                "landed": committed,
                "correct_landed": on_correct,
                "regression_shipped": committed and not on_behavior,
                "incomplete_refactor_shipped": (committed and on_behavior and bool(on_structure)),
                "oracle_pass": on_behavior if committed else None,
                "structural_failures": on_structure,
                "detail": on_detail,
                "tokens": usage.total,
                "seconds": round(seconds, 3),
                "retries": final.retries if final else max_retries,
                "harness_prompt": harness_prompt,
                "patch": current.edits if current else {},
                "checks": asdict(final.checks) if final else None,
            },
        ]


def aggregate(records: list[dict]) -> dict[str, object]:
    arms: dict[str, dict[str, object]] = {}
    for arm in ("off", "on"):
        rows = [row for row in records if row["arm"] == arm]
        count = len(rows)
        arms[arm] = {
            "runs": count,
            "correct_landed": sum(row["correct_landed"] for row in rows),
            "correct_landed_rate": round(sum(row["correct_landed"] for row in rows) / count, 3)
            if count
            else 0.0,
            "regressions_shipped": sum(row["regression_shipped"] for row in rows),
            "incomplete_refactors_shipped": sum(row["incomplete_refactor_shipped"] for row in rows),
            "escalations": sum(row["status"] == "skipped-needs-human" for row in rows),
            "tokens": sum(row["tokens"] for row in rows),
            "seconds": round(sum(row["seconds"] for row in rows), 3),
        }
    return {"arms": arms}


def run(backend: Backend, cases: tuple[CaseAdapter, ...], trials: int, max_retries: int) -> dict:
    records: list[dict] = []
    for trial in range(trials):
        for case in cases:
            records.extend(_run_pair(case, backend, trial, max_retries))
    return {
        "status": "valid",
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": backend.name,
            "methodology": "independent full-system proposals",
            "initial_user_prompt": USER_PROMPT,
            "cases": [case.name for case in cases],
            "trials": trials,
            "max_retries": max_retries,
        },
        "records": records,
        "aggregate": aggregate(records),
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
                return Completion("", Usage(), 0.0, "ANTHROPIC_API_KEY is not configured")
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
                    int(raw_usage.get("input_tokens", 0)), int(raw_usage.get("output_tokens", 0))
                )
            else:
                text = data["choices"][0]["message"]["content"]
                raw_usage = data.get("usage", {})
                usage = Usage(
                    int(raw_usage.get("prompt_tokens", 0)),
                    int(raw_usage.get("completion_tokens", 0)),
                )
            return Completion(text, usage, round(time.perf_counter() - started, 3))
        except (
            urllib.error.URLError,
            TimeoutError,
            KeyError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            return Completion("", Usage(), round(time.perf_counter() - started, 3), str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("anthropic", "openai"), default="anthropic")
    parser.add_argument("--model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--case", action="append", choices=[case.name for case in CASES])
    parser.add_argument("--calibrate-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    selected = tuple(case for case in CASES if not args.case or case.name in args.case)
    result = (
        {"status": "valid", "calibration": calibrate(selected)}
        if args.calibrate_only
        else run(
            HttpBackend(args.provider, args.model, args.base_url),
            selected,
            args.trials,
            args.max_retries,
        )
    )
    if args.calibrate_only and not result["calibration"]["valid"]:
        result["status"] = "void"
    destination = args.output or REPO_ROOT / "eval" / "results" / "full-system-latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    if "aggregate" in result:
        print(json.dumps(result["aggregate"], indent=2))
    print(f"status: {result['status']} | result: {destination}")
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
