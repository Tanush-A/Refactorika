"""Refactorika benchmark framework.

Measures the three things the product promises, in one comparable schema:

  * Reliable   -- does the harness catch bad edits and pass good ones?
  * Enhances   -- did the refactor measurably improve structure?
  * Seamless   -- did it complete autonomously (retries / escalations / cost)?

Design goal: do NOT bake today's easy data source into the metric. Three slots
are pluggable so a deterministic run this week and a real-Claude-on-RefactorBench
run later produce the SAME result records:

  Proposer  -- where edits come from   (deterministic | synthetic | claude)
  Substrate -- what we edit + isolate  (demo_repo | refactorbench | any git repo)
  Grader    -- the independent oracle   (repo tests | gate-level)

The comparison is an ABLATION across tiers, not an on/off switch:

  RAW        -- accept the edit, no gates                 (the unharnessed model)
  LINT_TYPE  -- parse + lint + typecheck only             (ruff + pyright alone)
  FULL       -- the whole harness incl. the behavior gate (Refactorika)

The delta between LINT_TYPE and FULL is the silent-behavior-break value that
nothing else catches -- the whole reason the harness exists.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make `refactorika` importable

from refactorika.core.analyze import analyze_file  # noqa: E402
from refactorika.core.gates import (  # noqa: E402
    lint_gate,
    parse_gate,
    ruff_baseline,
    test_gate,
    typecheck_gate,
)

# --- vocabulary --------------------------------------------------------------

# Worst-first: a silent behavior break is the one linters/CI miss.
Severity = Literal["none", "lint", "type", "syntax", "silent_behavior"]
SEVERITY_ORDER: tuple[Severity, ...] = ("none", "lint", "type", "syntax", "silent_behavior")

Tier = Literal["raw", "lint_type", "full"]
# Ordered gate subset per tier. RAW runs nothing -> accepts blindly.
TIER_GATES: dict[Tier, tuple[str, ...]] = {
    "raw": (),
    "lint_type": ("parse", "lint", "typecheck"),
    "full": ("parse", "lint", "typecheck", "tests"),
}


# --- data records ------------------------------------------------------------


@dataclass
class EditProposal:
    """One proposed edit to one file, plus what we know about it up front."""

    task_id: str
    file_rel: str               # path relative to the substrate repo root
    new_content: str
    refactor_kind: str
    # For synthetic/control edits we KNOW the truth; for a real model these stay None.
    declared_label: Optional[Literal["good", "broken"]] = None
    declared_severity: Severity = "none"
    is_control: bool = False    # negative-control calibration edit


@dataclass
class TierOutcome:
    tier: Tier
    landed: bool                       # would this edit be committed?
    gate_checks: dict                  # gate -> True/False/None
    broken_landed: bool = False        # landed AND oracle says broken
    good_rejected: bool = False        # rolled back AND oracle says fine
    committed_unverified: bool = False # landed without behavioral proof


@dataclass
class TaskResult:
    task_id: str
    proposer: str
    substrate: str
    grader: str
    refactor_kind: str
    is_control: bool
    declared_label: Optional[str]
    declared_severity: Severity
    ground_truth_broken: bool          # the independent oracle's verdict
    oracle_severity: Severity
    tiers: dict                        # tier name -> TierOutcome (as dict)
    structure_delta: dict              # before/after structural metrics
    seconds: float
    control_ok: Optional[bool] = None  # for controls: did reality match declared?


# --- pluggable interfaces ----------------------------------------------------


class Substrate(ABC):
    """Provides tasks and isolated working copies to edit + grade."""

    name: str

    @abstractmethod
    def tasks(self) -> list[str]: ...

    @abstractmethod
    def materialize(self, task_id: str, dest: Path) -> Path:
        """Copy the task's repo into ``dest``; return the repo root path."""

    @abstractmethod
    def baseline_content(self, task_id: str, file_rel: str) -> str: ...


class Proposer(ABC):
    """Produces edits for a task. Swap deterministic <-> synthetic <-> claude."""

    name: str

    @abstractmethod
    def propose(self, substrate: Substrate, task_id: str) -> list[EditProposal]: ...


class Grader(ABC):
    """Independent oracle: is the landed code broken, and how badly?

    MUST be independent of the harness's own gates or the comparison is circular
    (see docs note). The repo's real test suite is the oracle here.
    """

    name: str

    @abstractmethod
    def grade(self, task_id: str, file_rel: str, content: str) -> tuple[bool, Severity]: ...


# --- concrete: demo_repo substrate ------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demo_repo"


class DemoRepoSubstrate(Substrate):
    """The self-contained demo_repo: orders.py + a runnable test_orders.py."""

    name = "demo_repo"

    def tasks(self) -> list[str]:
        return ["orders"]

    def materialize(self, task_id: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        for f in ("orders.py", "test_orders.py"):
            shutil.copy2(DEMO_DIR / f, dest / f)
        return dest

    def baseline_content(self, task_id: str, file_rel: str) -> str:
        return (DEMO_DIR / file_rel).read_text()


# --- concrete: synthetic proposer (labeled edits = built-in controls) --------

# A behavior-preserving refactor of demo_repo/orders.py (what a good model emits).
_GOOD = '''from typing import Optional

TIER_RATE = {"gold_bulk": 0.85, "gold": 0.90, "silver": 0.95}
COUPON_RATE = {"SAVE10": 0.90, "SAVE20": 0.80}


def _line_total(item: dict, customer_tier: str) -> float:
    if item["qty"] <= 0 or item["price"] < 0:
        return 0.0
    line = item["price"] * item["qty"]
    if customer_tier == "gold":
        return line * (TIER_RATE["gold_bulk"] if line > 100 else TIER_RATE["gold"])
    if customer_tier == "silver":
        return line * TIER_RATE["silver"]
    return line


def compute_total(items: list[dict], customer_tier: str, coupon: Optional[str]) -> float:
    """Total price with tier discount, coupon, and tax."""
    import math

    total = sum(_line_total(item, customer_tier) for item in items)
    total *= COUPON_RATE.get(coupon, 1.0) if coupon is not None else 1.0
    tax = total * 0.08
    return math.floor((total + tax) * 100) / 100
'''


class SyntheticProposer(Proposer):
    """Emits one good edit + one broken edit per failure class, all labeled.

    These double as negative controls: a known-good edit MUST pass the full
    harness and a known-broken edit MUST be caught. If not, the harness (or the
    grader) is itself broken -- the automated self-check.
    """

    name = "synthetic"

    def propose(self, substrate: Substrate, task_id: str) -> list[EditProposal]:
        good = _GOOD
        return [
            EditProposal(task_id, "orders.py", good, "flatten_nesting",
                         declared_label="good", declared_severity="none", is_control=True),
            EditProposal(task_id, "orders.py", good + "\ndef oops(:\n    pass\n",
                         "flatten_nesting", declared_label="broken",
                         declared_severity="syntax", is_control=True),
            EditProposal(task_id, "orders.py", "import os\nimport sys\nimport json\n" + good,
                         "flatten_nesting", declared_label="broken",
                         declared_severity="lint", is_control=True),
            EditProposal(task_id, "orders.py", good.replace("tax = total * 0.08", "tax = total * 0.05"),
                         "flatten_nesting", declared_label="broken",
                         declared_severity="silent_behavior", is_control=True),
        ]


class ClaudeProposer(Proposer):
    """Real-model proposer. Extension point for the agent-in-the-loop run."""

    name = "claude"

    def propose(self, substrate: Substrate, task_id: str) -> list[EditProposal]:
        raise NotImplementedError(
            "Wire the Anthropic API here: feed the task instruction + file, return the "
            "model's proposed file content as EditProposal(declared_label=None)."
        )


# --- concrete: independent oracle grader ------------------------------------


class RepoTestGrader(Grader):
    """Ground truth via parse + the repo's own tests, run in a fresh copy.

    Independent of the harness gates: this is the oracle, computed once per
    proposal regardless of tier, so RAW/LINT_TYPE tiers reveal the breaks they
    shipped without ever consulting the harness.
    """

    name = "repo_tests"

    def __init__(self, substrate: Substrate):
        self._substrate = substrate

    def grade(self, task_id: str, file_rel: str, content: str) -> tuple[bool, Severity]:
        ok, _ = parse_gate(content)
        if ok is False:
            return True, "syntax"
        pytest = shutil.which("pytest")
        if pytest is None:
            return False, "none"  # no behavioral oracle available
        with tempfile.TemporaryDirectory(prefix="refactorika-oracle-") as d:
            work = self._substrate.materialize(task_id, Path(d) / "r")
            (work / file_rel).write_text(content)
            out = subprocess.run([pytest, "-q", "--no-header"], cwd=str(work),
                                 capture_output=True, text=True)
        if out.returncode == 5:
            return False, "none"  # no tests cover it -> oracle can't judge behavior
        if out.returncode != 0:
            return True, "silent_behavior"
        return False, "none"


# --- engine -----------------------------------------------------------------


def _run_gate(gate: str, *, content: str, path: Path, baseline: int, repo_root: Path):
    if gate == "parse":
        return parse_gate(content)[0]
    if gate == "lint":
        return lint_gate(path, baseline)[0]
    if gate == "typecheck":
        return typecheck_gate(path)[0]
    if gate == "tests":
        return test_gate(repo_root)[0]
    raise ValueError(gate)


def _run_tier(substrate: Substrate, proposal: EditProposal, tier: Tier) -> TierOutcome:
    with tempfile.TemporaryDirectory(prefix=f"refactorika-{tier}-") as d:
        repo = substrate.materialize(proposal.task_id, Path(d) / "r")
        path = repo / proposal.file_rel
        baseline = ruff_baseline(path)            # baseline on the pre-edit file
        path.write_text(proposal.new_content)
        checks: dict = {}
        landed = True
        for gate in TIER_GATES[tier]:
            res = _run_gate(gate, content=proposal.new_content, path=path,
                            baseline=baseline, repo_root=repo)
            checks[gate] = res
            if res is False:
                landed = False
                break
    return TierOutcome(tier=tier, landed=landed, gate_checks=checks)


def _analyze_content(content: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="refactorika-analyze-") as d:
        p = Path(d) / "f.py"
        p.write_text(content)
        try:
            opps = analyze_file(str(p)).opportunities
        except Exception:  # noqa: BLE001
            return {}
    out: dict[str, int] = {}
    for o in opps:
        out[o.kind] = out.get(o.kind, 0) + 1
    out["total"] = len(opps)
    return out


def run_task(substrate: Substrate, proposer: Proposer, grader: Grader,
             task_id: str) -> list[TaskResult]:
    import time

    results: list[TaskResult] = []
    for proposal in proposer.propose(substrate, task_id):
        t0 = time.perf_counter()
        broken, oracle_sev = grader.grade(task_id, proposal.file_rel, proposal.new_content)
        is_bad = broken or proposal.declared_label == "broken"

        tiers: dict[str, TierOutcome] = {}
        for tier in ("raw", "lint_type", "full"):
            out = _run_tier(substrate, proposal, tier)  # type: ignore[arg-type]
            out.broken_landed = out.landed and is_bad
            out.good_rejected = (not out.landed) and (not is_bad)
            out.committed_unverified = out.landed and out.gate_checks.get("tests") is not True
            tiers[tier] = out

        control_ok: Optional[bool] = None
        if proposal.is_control:
            full = tiers["full"]
            control_ok = (not full.broken_landed) if proposal.declared_label == "broken" \
                else full.landed

        base = substrate.baseline_content(task_id, proposal.file_rel)
        results.append(TaskResult(
            task_id=task_id, proposer=proposer.name, substrate=substrate.name,
            grader=grader.name, refactor_kind=proposal.refactor_kind,
            is_control=proposal.is_control, declared_label=proposal.declared_label,
            declared_severity=proposal.declared_severity,
            ground_truth_broken=broken, oracle_severity=oracle_sev,
            tiers={k: asdict(v) for k, v in tiers.items()},
            structure_delta={"before": _analyze_content(base),
                             "after": _analyze_content(proposal.new_content)},
            seconds=round(time.perf_counter() - t0, 3), control_ok=control_ok,
        ))
    return results


def aggregate(results: list[TaskResult]) -> dict:
    """Roll task results up into the three pillars, per ablation tier."""
    real = [r for r in results]
    n_bad = sum(1 for r in real if r.ground_truth_broken or r.declared_label == "broken")
    n_good = len(real) - n_bad

    per_tier: dict[str, dict] = {}
    for tier in ("raw", "lint_type", "full"):
        caught = bl = gr = cu = 0
        by_sev: dict[str, int] = {}
        for r in real:
            o = r.tiers[tier]
            is_bad = r.ground_truth_broken or r.declared_label == "broken"
            if is_bad and not o["landed"]:
                caught += 1
            if o["broken_landed"]:
                bl += 1
                sev = r.declared_severity if r.is_control else r.oracle_severity
                by_sev[sev] = by_sev.get(sev, 0) + 1
            gr += int(o["good_rejected"])
            cu += int(o["committed_unverified"])
        per_tier[tier] = {
            "bad_edits": n_bad, "good_edits": n_good,
            "caught": caught, "broken_landed": bl,
            "broken_landed_by_severity": by_sev,
            "good_rejected": gr, "committed_unverified": cu,
            "catch_rate": round(caught / n_bad, 3) if n_bad else None,
            "false_rejection_rate": round(gr / n_good, 3) if n_good else None,
        }

    controls = [r for r in real if r.is_control]
    calibration = {
        "controls": len(controls),
        "passed": sum(1 for r in controls if r.control_ok),
        "failed": [r.task_id + ":" + str(r.declared_severity)
                   for r in controls if r.control_ok is False],
    }
    return {"per_tier": per_tier, "calibration": calibration}


def _print_report(agg: dict) -> None:
    print("\n=== Harness ablation (vs no harness) ===")
    print(f"  {'tier':<10} {'catch':>6} {'broken_landed':>14} {'false_rej':>10} {'unverified':>11}")
    for tier, m in agg["per_tier"].items():
        print(f"  {tier:<10} {str(m['catch_rate']):>6} {m['broken_landed']:>14} "
              f"{str(m['false_rejection_rate']):>10} {m['committed_unverified']:>11}")
    full = agg["per_tier"]["full"]
    print(f"  silent-behavior breaks still landing (full): "
          f"{full['broken_landed_by_severity'].get('silent_behavior', 0)}")
    cal = agg["calibration"]
    print(f"\n  controls: {cal['passed']}/{cal['controls']} passed", end="")
    print(f"  FAILED: {cal['failed']}" if cal["failed"] else "  (calibration OK)")


def run_demo_benchmark() -> dict:
    substrate = DemoRepoSubstrate()
    proposer = SyntheticProposer()
    grader = RepoTestGrader(substrate)
    results: list[TaskResult] = []
    for task in substrate.tasks():
        results.extend(run_task(substrate, proposer, grader, task))
    agg = aggregate(results)
    _print_report(agg)
    return {"results": [asdict(r) for r in results], "aggregate": agg}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    run_demo_benchmark()
