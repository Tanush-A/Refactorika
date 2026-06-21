"""Phase 2 — RefactorBench slice (real, unseen OSS repos).

Runs our local agent against RefactorBench tasks, harness OFF vs ON, and grades
with RefactorBench's own AST checker as the INDEPENDENT oracle. This is the
generalization test: it answers "does the harness keep broken edits off real
third-party code?" rather than re-grading our own demo repo.

Gate note: RefactorBench intentionally ships repos WITHOUT their dependencies, so
the type gate (pyright -> unresolved imports) and test gate (pytest -> can't
import the package) would spuriously fail on every edit. We therefore run the
dependency-free subset of the SAME real gates — `parse_gate` + `lint_gate` — and
keep the AST grader as the separate oracle. Every report line says exactly this.

Expectation: these are hard, multi-hop, often multi-file tasks; the paper's
frontier LM agents solved ~22% and humans ~87%. A local 7B editing a single file
will solve few or none — the headline here is the SAFETY delta (broken edits
shipped: no-harness vs harness), not the solve rate.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from refactorika.core.gates import lint_gate, parse_gate, ruff_baseline  # noqa: E402

from eval.proposers import LocalAgentProposer, Usage  # noqa: E402

DEFAULT_RB_DIR = _REPO_ROOT / "eval" / "external" / "refactorbench"

# Paper anchors (RefactorBench, base/handcrafted tasks).
ANCHOR_LM_AGENT = 0.22
ANCHOR_HUMAN = 0.87


def _log(msg: str) -> None:
    print(f"    {msg}", flush=True)


@dataclass
class RBTask:
    name: str
    repo: str  # e.g. "flask_refactor"
    instruction: str
    grader_path: Path
    primary_rel: str  # e.g. "src/flask/helpers.py"
    primary_loc: int


def discover_tasks(rb_dir: Path, repo: str = "flask_refactor") -> list[RBTask]:
    """Find tasks for a repo and rank by primary-file size (smallest first, so a
    single-file local agent has the best shot and generations stay fast)."""
    repo_root = rb_dir / "repositories" / repo
    base = rb_dir / "problems" / "base_problems" / repo
    tasks: list[RBTask] = []
    for grader in sorted((rb_dir / "tests" / repo).glob("*.py")):
        gtext = grader.read_text()
        m = re.search(r"file_path\s*=\s*'(\.\./src/[\w/]+\.py)'", gtext)
        if not m:
            srcs = re.findall(r"\.\./src/[\w/]+\.py", gtext)
            if not srcs:
                continue
            primary = srcs[0]
        else:
            primary = m.group(1)
        primary_rel = primary.replace("../", "")
        prim_abs = repo_root / primary_rel
        if not prim_abs.exists():
            continue
        # task instruction file: strip trailing -test / .py to match *-task.txt
        stem = grader.stem
        for suffix in ("-test", ".py"):
            stem = stem[: -len(suffix)] if stem.endswith(suffix) else stem
        task_txt = base / f"{stem}-task.txt"
        if not task_txt.exists():
            cand = list(base.glob(f"{stem}*task*.txt"))
            if not cand:
                continue
            task_txt = cand[0]
        tasks.append(RBTask(
            name=grader.stem, repo=repo, instruction=task_txt.read_text().strip(),
            grader_path=grader, primary_rel=primary_rel,
            primary_loc=len(prim_abs.read_text().splitlines()),
        ))
    tasks.sort(key=lambda t: t.primary_loc)
    return tasks


def _stage_repo(rb_dir: Path, repo: str) -> Path:
    work = Path(tempfile.mkdtemp(prefix="rb-"))
    copy = work / repo
    shutil.copytree(rb_dir / "repositories" / repo, copy)
    return copy


def _run_grader(copy: Path, grader_path: Path) -> bool:
    """Stage the grader in a subdir so its ../src and ../tests paths resolve, run
    it, return True iff all checks pass."""
    rundir = copy / "_rb"
    rundir.mkdir(exist_ok=True)
    shutil.copy(grader_path, rundir / "test.py")
    try:
        r = subprocess.run([sys.executable, "test.py"], cwd=str(rundir),
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _defined_names(source: str) -> set[str]:
    """Top-level + nested function/class names defined in a file."""
    import ast  # noqa: PLC0415
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _key_symbols(primary_text: str, instruction: str) -> set[str]:
    """The symbols actually being refactored: names DEFINED in the primary file
    that the instruction mentions (the 'old' name a renamer/editor targets). This
    is the high-signal grep term — far better than matching common English words."""
    defined = _defined_names(primary_text)
    return {n for n in defined if re.search(rf"\b{re.escape(n)}\b", instruction)}


def _relevant_files(copy: Path, primary_rel: str, instruction: str,
                    cap: int = 10) -> dict[str, str]:
    """The files an agent would actually touch: the primary file plus every src/
    or tests/ file that references the SYMBOL being refactored (the call sites a
    real agent would grep for). Ranked by reference density (most call sites
    first) so the files the task truly needs aren't crowded out by tiny unrelated
    files. Paths are repo-relative."""
    primary_text = (copy / primary_rel).read_text()
    syms = _key_symbols(primary_text, instruction)
    if not syms:  # fallback: identifiers from the instruction present in primary
        syms = {i for i in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", instruction)
                if re.search(rf"\b{re.escape(i)}\b", primary_text)}
    patterns = [re.compile(rf"\b{re.escape(s)}\b") for s in syms]
    candidates: list[tuple[int, int, str, str]] = []  # (-refs, loc, rel, text)
    for base in ("src", "tests"):
        root = copy / base
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            rel = str(py.relative_to(copy))
            if rel == primary_rel:
                continue
            text = py.read_text()
            refs = sum(len(p.findall(text)) for p in patterns)
            if refs:
                candidates.append((-refs, len(text.splitlines()), rel, text))
    candidates.sort()  # most references first, then smallest
    files: dict[str, str] = {primary_rel: primary_text}
    for _negrefs, _loc, rel, text in candidates:
        if len(files) >= cap:
            break
        files[rel] = text
    return files


def _gate_multi(copy: Path, edits: dict[str, str]) -> tuple[bool, str]:
    """Dependency-free subset of the real gate stack across all edited files:
    parse every new content, then lint each. Writes only after all parse clean;
    the caller restores originals on failure."""
    for rel, content in edits.items():
        ok, detail = parse_gate(content)
        if ok is False:
            return False, f"parse {rel}: {detail}"
    baselines = {rel: ruff_baseline(copy / rel) for rel in edits}
    for rel, content in edits.items():
        (copy / rel).write_text(content)
    for rel in edits:
        ok, detail = lint_gate(copy / rel, baselines[rel])
        if ok is False:
            return False, f"lint {rel}: {detail}"
    return True, f"parse+lint passed ({len(edits)} file(s))"


def _run_no_harness(task: RBTask, rb_dir: Path, proposer) -> dict:
    copy = _stage_repo(rb_dir, task.repo)
    files = _relevant_files(copy, task.primary_rel, task.instruction)
    _log(f"[{task.name}] no-harness: proposing across {len(files)} file(s) "
         f"({', '.join(Path(f).name for f in files)})...")
    mp = proposer.propose_multi_patch(task.instruction, files)
    usage = mp.usage
    if mp.error or not mp.edits:
        reason = mp.error or "no edits produced"
        _log(f"[{task.name}] no-harness: {reason} after {mp.seconds}s -> broken, not solved")
        shutil.rmtree(copy.parent, ignore_errors=True)
        return _rec(task, "no_harness", solved=False, broken_shipped=True,
                    retries=0, usage=usage, seconds=mp.seconds, escalated=False,
                    files_edited=0)
    # "broken shipped" = the raw edit fails the harness gate stack (parse OR lint).
    # _gate_multi writes the edits, so the grader then sees what was actually shipped.
    gate_ok, gate_detail = _gate_multi(copy, mp.edits)
    broken = not gate_ok
    solved = _run_grader(copy, task.grader_path)
    shutil.rmtree(copy.parent, ignore_errors=True)
    note = "gates-clean" if gate_ok else f"WOULD-BE-REJECTED: {gate_detail}"
    _log(f"[{task.name}] no-harness: shipped {len(mp.edits)} file(s) ({note}) "
         f"-> grader {'SOLVED' if solved else 'not solved'}  "
         f"({usage.total} tok, {round(mp.seconds,1)}s)")
    return _rec(task, "no_harness", solved=solved, broken_shipped=broken,
                retries=0, usage=usage, seconds=mp.seconds, escalated=False,
                files_edited=len(mp.edits))


def _run_harness(task: RBTask, rb_dir: Path, proposer, max_retries: int) -> dict:
    copy = _stage_repo(rb_dir, task.repo)
    usage = Usage()
    seconds = 0.0
    failure_reason: Optional[str] = None
    committed = False
    attempts = 0
    n_edited = 0
    for attempt in range(max_retries + 1):
        attempts += 1
        files = _relevant_files(copy, task.primary_rel, task.instruction)  # originals
        _log(f"[{task.name}] harness: attempt {attempt + 1}/{max_retries + 1} "
             f"proposing across {len(files)} file(s)...")
        mp = proposer.propose_multi_patch(task.instruction, files, failure_reason)
        usage.add(mp.usage)
        seconds += mp.seconds
        if mp.error or not mp.edits:
            failure_reason = mp.error or "no edits produced"
            _log(f"[{task.name}] harness: attempt {attempt + 1} {failure_reason}")
            continue
        originals = {rel: (copy / rel).read_text() for rel in mp.edits}
        ok, detail = _gate_multi(copy, mp.edits)
        if ok:
            committed = True
            n_edited = len(mp.edits)
            _log(f"[{task.name}] harness: attempt {attempt + 1} COMMITTED ({detail})")
            break
        for rel, content in originals.items():  # rollback all
            (copy / rel).write_text(content)
        failure_reason = detail
        _log(f"[{task.name}] harness: attempt {attempt + 1} rolled back ({detail})")
    solved = _run_grader(copy, task.grader_path) if committed else False
    shutil.rmtree(copy.parent, ignore_errors=True)
    _log(f"[{task.name}] harness: {'COMMITTED' if committed else 'ESCALATED needs-human'} "
         f"after {attempts} attempt(s) -> grader {'SOLVED' if solved else 'not solved'}  "
         f"({usage.total} tok, {round(seconds,1)}s)")
    return _rec(task, "harness", solved=solved, broken_shipped=False,
                retries=attempts - 1, usage=usage, seconds=seconds, escalated=not committed,
                files_edited=n_edited)


def _rec(task: RBTask, arm: str, *, solved: bool, broken_shipped: bool, retries: int,
         usage: Usage, seconds: float, escalated: bool, files_edited: int = 0) -> dict:
    return {
        "task": task.name, "repo": task.repo, "arm": arm,
        "primary": task.primary_rel, "primary_loc": task.primary_loc,
        "solved": solved, "broken_shipped": broken_shipped, "retries": retries,
        "tokens_total": usage.total, "seconds": round(seconds, 1), "escalated": escalated,
        "files_edited": files_edited,
    }


def _aggregate(records: list[dict], tasks: list[RBTask], model: str) -> dict:
    nh = [r for r in records if r["arm"] == "no_harness"]
    hr = [r for r in records if r["arm"] == "harness"]
    n = len(tasks)

    def solve_rate(rs):
        return round(sum(r["solved"] for r in rs) / len(rs), 3) if rs else None

    return {
        "status": "measured",
        "model": model,
        "repo": tasks[0].repo if tasks else None,
        "tasks": n,
        "gates_used": "parse + lint (type/test gates need repo deps — omitted)",
        "oracle": "RefactorBench AST grader (independent)",
        "solve_rate": {"no_harness": solve_rate(nh), "harness": solve_rate(hr)},
        "solved": {"no_harness": sum(r["solved"] for r in nh),
                   "harness": sum(r["solved"] for r in hr)},
        "broken_shipped": {"no_harness": sum(r["broken_shipped"] for r in nh),
                           "harness": sum(r["broken_shipped"] for r in hr)},
        "escalated": sum(r["escalated"] for r in hr),
        "anchors": {"lm_agent": ANCHOR_LM_AGENT, "human": ANCHOR_HUMAN},
        "tokens_total": sum(r["tokens_total"] for r in records),
        "records": records,
    }


def run_refactorbench(rb_dir: Path = DEFAULT_RB_DIR, repo: str = "flask_refactor",
                      proposer=None, task_limit: int = 3, max_retries: int = 2) -> dict:
    if not rb_dir.exists():
        return {"status": "pending", "reason": "RefactorBench not fetched"}
    if proposer is None:
        proposer = LocalAgentProposer()
    tasks = discover_tasks(rb_dir, repo)[:task_limit]
    if not tasks:
        return {"status": "pending", "reason": f"no tasks discovered for {repo}"}
    print(f"  RefactorBench slice: {len(tasks)} {repo} task(s), model={proposer.id} "
          f"max_retries={max_retries}  (multi-file edits)", flush=True)
    print(f"  gates: parse+lint (no repo deps)  oracle: AST grader  "
          f"anchors: LM {int(ANCHOR_LM_AGENT*100)}% / human {int(ANCHOR_HUMAN*100)}%", flush=True)
    records: list[dict] = []
    for i, task in enumerate(tasks, 1):
        print(f"  task {i}/{len(tasks)}: {task.name}", flush=True)
        records.append(_run_no_harness(task, rb_dir, proposer))
        records.append(_run_harness(task, rb_dir, proposer, max_retries))
    return _aggregate(records, tasks, proposer.id)
