"""RefactorBench adapter — run Refactorika's engine on real multi-file refactoring tasks.

RefactorBench (microsoft/RefactorBench) is 100 natural-language refactoring tasks across nine
real OSS repos (Django, FastAPI, Celery, Scrapy, …), each verified by an AST unit test. This
adapter, per task:

  1. classifies the instruction against Refactorika's fixed transform menu and *declines*
     out-of-scope tasks explicitly (no hallucinated edit);
  2. for in-scope tasks, sets up an isolated copy of the repo, maps the instruction to a
     TransformSpec, applies it through the engine (rope rename, reference-correct, parse-gated);
  3. runs the task's own AST unit test and records subtests-passed/total and task pass/fail.

We report THREE numbers, never one: in-scope pass rate, in-scope subtask-completion, and the
count of out-of-scope tasks. The in-scope set for v1 is single-symbol renames (Refactorika's
rename engine); module/file renames, moves, signature changes, encapsulation, etc. are declined.

Isolation: a fresh filesystem copy per task (the tests are pure AST/file checks needing no repo
install, so this gives the same isolation as the dockerized setup the benchmark recommends,
without the overhead). Verification: the task's AST test is the authoritative check; the engine
parse-gates each edit. The repo's own (huge) test suite is not run per task — at benchmark scale
that is impractical, and RefactorBench's AST tests are its intended verification.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Make `refactorika` importable when run from the eval/ venv or the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from refactorika.core.gates import parse_gate  # noqa: E402
from refactorika.core.schema import RefactorDecision  # noqa: E402
from refactorika.transforms.rename import rename_at  # noqa: E402

DEFAULT_RB_DIR = Path(__file__).resolve().parent / "external" / "refactorbench"

# "Rename [the] X [class] [in F] to Y" — single-symbol rename.
_RENAME_RE = re.compile(
    r"rename\s+(?:the\s+)?([A-Za-z_]\w*)(?:\s+class)?(?:\s+in\s+[\w./]+)?\s+to\s+([A-Za-z_]\w*)\b",
    re.IGNORECASE,
)
# Out-of-scope intents Refactorika has no reference-correct engine for in v1.
_OUT_OF_SCOPE_HINTS = [
    ("move ", "move"),
    ("add a log", "add_parameter"),
    ("add a logging", "add_parameter"),
    ("add a boolean", "add_parameter"),
    ("combine ", "combine"),
    ("encapsulate ", "encapsulate"),
    ("create a new", "create"),
    ("parameterize ", "parameterize"),
    ("resolve the", "import_fix"),
]


@dataclass
class Task:
    name: str
    repo: str  # e.g. "fastapi_refactor"
    instruction: str
    task_path: Path
    test_path: Path
    level: str


@dataclass
class Scope:
    in_scope: bool
    kind: str
    params: dict
    reason: str


@dataclass
class TaskResult:
    task: Task
    scope: Scope
    status: str  # declined | target_not_found | parse_failed | applied | error
    passed: int
    total: int
    seconds: float = 0.0

    @property
    def task_pass(self) -> bool:
        return self.scope.in_scope and self.total > 0 and self.passed == self.total

    def to_dict(self) -> dict:
        return {
            "task": self.task.name,
            "repo": self.task.repo.replace("_refactor", ""),
            "level": self.task.level,
            "in_scope": self.scope.in_scope,
            "kind": self.scope.kind,
            "reason": self.scope.reason,
            "status": self.status,
            "subtests_passed": self.passed,
            "subtests_total": self.total,
            "task_pass": self.task_pass,
            "seconds": round(self.seconds, 2),
            "instruction": self.task.instruction,
        }


# --------------------------------------------------------------------------- loading
def load_tasks(rb_dir: Path = DEFAULT_RB_DIR, level: str = "base") -> list[Task]:
    """Parse scripts/<level>_mapping.py (test_path -> task_path) into Task objects."""
    mapping_file = rb_dir / "scripts" / f"{level}_mapping.py"
    ns: dict = {}
    exec(mapping_file.read_text(), ns)  # noqa: S102 — trusted benchmark data
    scripts_dir = rb_dir / "scripts"
    tasks: list[Task] = []
    for test_rel, task_rel in ns["file_mapping"].items():
        test_path = (scripts_dir / test_rel).resolve()
        task_path = (scripts_dir / task_rel).resolve()
        if not test_path.exists() or not task_path.exists():
            continue
        repo = test_path.parent.name
        name = task_path.stem.replace("-task", "")
        tasks.append(Task(name, repo, task_path.read_text().strip(), task_path, test_path, level))
    return tasks


# ----------------------------------------------------------------------- classifying
def classify(instruction: str) -> Scope:
    """Map an instruction to an in-scope TransformSpec, or declare it out of scope."""
    low = instruction.lower()
    if "rename" in low:
        if re.search(r"\b[\w]+\.py\b", instruction):
            return Scope(False, "module_rename", {}, "module/file rename (out of scope in v1)")
        m = _RENAME_RE.search(instruction)
        if m:
            multi = " and " in low and low.count(" to ") > 1
            reason = "multi-target rename (attempting first)" if multi else "single-symbol rename"
            return Scope(True, "rename", {"old": m.group(1), "new": m.group(2)}, reason)
        return Scope(False, "rename_unparsed", {}, "rename phrasing not a single symbol")
    for hint, label in _OUT_OF_SCOPE_HINTS:
        if hint in low:
            return Scope(False, label, {}, f"{label} has no reference-correct engine in v1")
    return Scope(False, "other", {}, "no matching in-scope transform")


# ------------------------------------------------------------------------- execution
def _count_subtests(test_path: Path) -> int:
    return len(re.findall(r"def\s+test_\w+", test_path.read_text()))


def _locate_symbol(work: Path, name: str) -> Optional[tuple[str, int]]:
    """Find the definition of *name* (def/class or module/class-level assignment).

    Returns (abspath, char offset of the name). Assignments are included so symbol renames of
    constants (e.g. EX_STATE_FAILURE) are handled, not just functions/classes.
    """
    esc = re.escape(name)
    def_pat = re.compile(rf"^[ \t]*(?:async\s+)?(?:def|class)\s+({esc})\b", re.MULTILINE)
    assign_pat = re.compile(rf"^[ \t]*({esc})\s*(?::[^=\n]+)?=(?!=)", re.MULTILINE)
    best: Optional[tuple[str, int]] = None
    for py in sorted(work.rglob("*.py")):
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = def_pat.search(src)
        if m:
            return str(py), m.start(1)  # prefer a def/class definition
        if best is None:
            am = assign_pat.search(src)
            if am:
                best = (str(py), am.start(1))
    return best


def _setup_workdir(task: Task, rb_dir: Path) -> Path:
    """Copy the top-level dirs the test references (../<top>/) into an isolated work tree."""
    tops = set(re.findall(r"\.\./([A-Za-z_]\w*)/", task.test_path.read_text()))
    repo_dir = rb_dir / "repositories" / task.repo
    work = Path(tempfile.mkdtemp(prefix="rb_"))
    for top in tops:
        src = repo_dir / top
        if src.is_dir():
            shutil.copytree(src, work / top, ignore=shutil.ignore_patterns("__pycache__"))
    return work


def _run_ast_test(work: Path, task: Task) -> tuple[int, int]:
    """Run the task's AST unit test from a sibling dir; return (passed, total)."""
    tdir = work / "_rbtest"
    tdir.mkdir(exist_ok=True)
    shutil.copy(task.test_path, tdir / "rbtest.py")
    out = subprocess.run(
        [sys.executable, "-m", "unittest", "rbtest", "-v"],
        cwd=str(tdir), capture_output=True, text=True,
    )
    text = out.stderr + "\n" + out.stdout
    ran = re.search(r"Ran (\d+) test", text)
    total = int(ran.group(1)) if ran else _count_subtests(task.test_path)
    fails = errs = 0
    m = re.search(r"FAILED \(([^)]*)\)", text)
    if m:
        fb = re.search(r"failures=(\d+)", m.group(1))
        eb = re.search(r"errors=(\d+)", m.group(1))
        fails = int(fb.group(1)) if fb else 0
        errs = int(eb.group(1)) if eb else 0
    return max(0, total - fails - errs), total


def run_task(task: Task, rb_dir: Path, memory=None) -> TaskResult:
    """Classify, (if in scope) apply the rename through the engine, run the AST test."""
    t0 = time.time()
    scope = classify(task.instruction)
    total = _count_subtests(task.test_path)
    if not scope.in_scope:
        return TaskResult(task, scope, "declined", 0, total, time.time() - t0)

    work = _setup_workdir(task, rb_dir)
    try:
        loc = _locate_symbol(work, scope.params["old"])
        if loc is None:
            return TaskResult(task, scope, "target_not_found", 0, total, time.time() - t0)
        file, offset = loc
        new_name = scope.params["new"]

        if memory is not None:  # ablation: recall is informational for renames (name is given)
            memory.recall(task.instruction, pattern=f"rename:{scope.params['old']}")

        edits = rename_at(str(work), file, offset, new_name)
        for _p, content in edits.items():  # verified spine: parse-gate before writing
            ok, _ = parse_gate(content)
            if ok is False:
                return TaskResult(task, scope, "parse_failed", 0, total, time.time() - t0)
        for p, content in edits.items():
            Path(p).write_text(content, encoding="utf-8")

        if memory is not None:
            memory.record(RefactorDecision(
                pattern=f"rename:{scope.params['old']}", transform_kind="rename",
                target=file, choice={"new_name": new_name}), task.instruction)

        passed, total = _run_ast_test(work, task)
        return TaskResult(task, scope, "applied", passed, total, time.time() - t0)
    except Exception:
        return TaskResult(task, scope, "error", 0, total, time.time() - t0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------------- aggregate
@dataclass
class Summary:
    level: str
    memory_on: bool
    results: list[TaskResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        in_scope = [r for r in self.results if r.scope.in_scope]
        out_scope = [r for r in self.results if not r.scope.in_scope]
        attempted = [r for r in in_scope if r.status == "applied"]
        passes = sum(1 for r in in_scope if r.task_pass)
        sub_passed = sum(r.passed for r in in_scope)
        sub_total = sum(r.total for r in in_scope)
        return {
            "level": self.level,
            "memory_on": self.memory_on,
            "totals": {
                "all_tasks": len(self.results),
                "in_scope": len(in_scope),
                "out_of_scope": len(out_scope),
                "attempted": len(attempted),
            },
            "in_scope_pass_rate": round(passes / len(in_scope), 3) if in_scope else 0.0,
            "in_scope_subtask_completion": round(sub_passed / sub_total, 3) if sub_total else 0.0,
            "in_scope_passes": passes,
            "subtests": {"passed": sub_passed, "total": sub_total},
            "out_of_scope_count": len(out_scope),
            "results": [r.to_dict() for r in self.results],
        }


def run_eval(rb_dir: Path, level: str, *, only_in_scope: bool = False,
             smoke: bool = False, limit: Optional[int] = None, memory_on: bool = False) -> Summary:
    tasks = load_tasks(rb_dir, level)
    if only_in_scope or smoke:
        tasks = [t for t in tasks if classify(t.instruction).in_scope]
    if smoke:
        tasks = tasks[:5]
    if limit:
        tasks = tasks[:limit]

    memory = _make_memory() if memory_on else None
    summary = Summary(level=level, memory_on=memory_on)
    for t in tasks:
        r = run_task(t, rb_dir, memory=memory)
        summary.results.append(r)
        mark = "PASS" if r.task_pass else ("decl" if r.status == "declined" else "----")
        print(f"  [{mark}] {r.task.repo.replace('_refactor',''):8} {r.task.name:40} "
              f"{r.passed}/{r.total}  ({r.status})")
    return summary


def _make_memory():
    from refactorika.core.storage import Storage
    from refactorika.memory.decision_memory import DecisionMemory

    return DecisionMemory(Storage())


# ---------------------------------------------------------------------------- output
def write_results(summary: Summary, out_dir: Path, tag: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = summary.to_dict()
    (out_dir / f"{tag}.json").write_text(json.dumps(d, indent=2))
    (out_dir / f"{tag}.md").write_text(_markdown(d))


def _markdown(d: dict) -> str:
    lines = [
        f"# RefactorBench results — level={d['level']}, memory={'on' if d['memory_on'] else 'off'}",
        "",
        f"- **In-scope pass rate:** {d['in_scope_pass_rate']:.1%} "
        f"({d['in_scope_passes']}/{d['totals']['in_scope']} in-scope tasks)",
        f"- **In-scope subtask completion:** {d['in_scope_subtask_completion']:.1%} "
        f"({d['subtests']['passed']}/{d['subtests']['total']} subtests)",
        f"- **Out-of-scope (declined):** {d['out_of_scope_count']} of "
        f"{d['totals']['all_tasks']} tasks",
        "",
        "| repo | task | kind | in-scope | subtests | pass |",
        "|---|---|---|---|---|---|",
    ]
    for r in d["results"]:
        lines.append(
            f"| {r['repo']} | {r['task']} | {r['kind']} | {'yes' if r['in_scope'] else 'no'} "
            f"| {r['subtests_passed']}/{r['subtests_total']} | {'✓' if r['task_pass'] else ''} |"
        )
    return "\n".join(lines) + "\n"
