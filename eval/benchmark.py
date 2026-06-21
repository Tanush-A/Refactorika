"""Refactorika benchmark harness (Phase 0 — synthetic proposer, no agent).

The argument the whole benchmark makes: *same proposer, harness OFF vs ON*. The
product's value is the delta between the no-harness arm (`raw`) and the harness
arm (`full`), judged by an independent oracle (repo tests), not by the harness.

Phase 0 pieces (all runnable now):
  - Proposer interface + SyntheticProposer (labeled candidate edits on demo_repo).
  - Tier runner: applies a gate *subset* per tier by calling `core/gates.py`
    directly (NOT `apply_and_verify`, which is all-or-nothing + commits).
  - Oracle grader: independent pytest run that defines ground-truth correctness.
  - Negative controls: edits with known outcomes; if a control's outcome is
    wrong, the harness/grader is itself broken (self-check).
  - Aggregation: pillars (reliability/enhancement) + swag (4a proxy, 4c health).

Agent-dependent sections (§3 autonomy/cost, 4a-real, 4b $, 4d RefactorBench,
§5 model matrix) are emitted as `pending` rather than fabricated — see
docs/12-benchmark-display-spec.md.
"""
from __future__ import annotations

import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Make the in-repo package importable from the eval venv.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from refactorika.core.gates import (  # noqa: E402
    lint_gate,
    parse_gate,
    ruff_baseline,
    test_gate,
    typecheck_gate,
)

from eval import metrics_health  # noqa: E402
from eval.substrates import SubstrateSpec, checkout  # noqa: E402

# --- Tiers -----------------------------------------------------------------
# Gate subsets, cheapest first. `raw` = no harness at all (apply blindly).
TIER_GATES: dict[str, list[str]] = {
    "raw": [],
    "lint_type": ["parse", "lint", "typecheck"],
    "full": ["parse", "lint", "typecheck", "tests"],
}
TIERS = list(TIER_GATES)

# Severity buckets for broken-but-landed edits (track TIER_GATES order).
SEVERITIES = ("syn", "lint", "type", "beh")


# --- Proposer interface ----------------------------------------------------
@dataclass
class Candidate:
    """A proposed edit. `defect` is None for a genuine improvement, else the
    severity of the planted break (which gate *should* catch it)."""

    name: str
    target: str  # path relative to the substrate root, e.g. "orders.py"
    kind: str  # refactor kind (schema.REFACTOR_KINDS)
    mutate: Callable[[str], str]  # original file content -> proposed content
    defect: Optional[str] = None  # None | "syn" | "lint" | "type" | "beh"


class Proposer(ABC):
    """Produces candidate edits for a substrate. A 'model' is just a proposer
    configured with a model_id (Phase 1+)."""

    id: str = "abstract"

    @abstractmethod
    def propose(self, substrate: SubstrateSpec) -> list[Candidate]:
        ...

    @abstractmethod
    def controls(self, substrate: SubstrateSpec) -> list["Control"]:
        ...


# --- Synthetic proposer (demo_repo) ---------------------------------------
# Behaviour-preserving improvements, returned as full file contents.
_ORDERS_FLATTENED = '''import math
from typing import Optional


def _legacy_discount(price: float) -> float:
    """Old discount logic — superseded by compute_total; nothing calls this."""
    return price * 0.75


def _compute_shipping(weight: float) -> float:
    """Shipping calculator — reached via test_shipping below; should not be flagged dead."""
    if weight <= 1.0:
        return 3.99
    return 3.99 + (weight - 1.0) * 1.50


def compute_total(items: list[dict], customer_tier: str, coupon: Optional[str]) -> float:
    """Total price with tier discount, coupon, and tax (flattened, dup import removed)."""
    total = 0.0
    for item in items:
        if item["qty"] <= 0:
            continue
        if item["price"] < 0:
            continue
        line = item["price"] * item["qty"]
        if customer_tier == "gold":
            line = line * 0.85 if line > 100 else line * 0.90
        elif customer_tier == "silver":
            line = line * 0.95
        total = total + line
    if coupon == "SAVE10":
        total = total * 0.90
    elif coupon == "SAVE20":
        total = total * 0.80
    tax = total * 0.08
    return math.floor((total + tax) * 100) / 100
'''

_BILLING_TIDIED = '''"""Billing helpers — intentionally has a near-duplicate of orders.compute_total logic."""

import math
from typing import Optional


def calculate_invoice_total(
    line_items: list[dict], membership: str, promo: Optional[str]
) -> float:
    """Compute invoice total with membership discount and promo code (flattened)."""
    subtotal = 0.0
    for entry in line_items:
        if entry["qty"] <= 0:
            continue
        if entry["price"] < 0:
            continue
        amount = entry["price"] * entry["qty"]
        if membership == "gold":
            amount = amount * 0.85 if amount > 100 else amount * 0.90
        elif membership == "silver":
            amount = amount * 0.95
        subtotal += amount
    if promo == "SAVE10":
        subtotal *= 0.90
    elif promo == "SAVE20":
        subtotal *= 0.80
    tax = subtotal * 0.08
    return math.floor((subtotal + tax) * 100) / 100
'''


def _require_change(fn: Callable[[str], str]) -> Callable[[str], str]:
    """Wrap a string-replace mutation so a no-op (missed anchor) fails loudly."""

    def wrapped(original: str) -> str:
        new = fn(original)
        if new == original:
            raise ValueError("mutation anchor not found — candidate is a no-op")
        return new

    return wrapped


# Planted-break mutations (string replacements against the original file).
def _syn(s: str) -> str:
    return s.replace('        if coupon == "SAVE10":', '        if coupon == "SAVE10"')


def _lint(s: str) -> str:  # unused imports -> new F401s (not removable by `ruff format`)
    return s.replace(
        "from typing import Optional\n",
        "from typing import Optional\nimport os\nimport sys\nimport json\n",
    )


def _type(s: str) -> str:  # str assigned to int-annotated name -> pyright error
    return s + '\n\n_TYPED: int = "not an int"\n'


def _beh_tax(s: str) -> str:
    return s.replace("    tax = total * 0.08", "    tax = total * 0.09")


def _beh_gold(s: str) -> str:
    return s.replace(
        "                        line = line * 0.85",
        "                        line = line * 0.80",
    )


def _beh_coupon(s: str) -> str:
    return s.replace("            total = total * 0.90", "            total = total * 0.95")


@dataclass
class Control:
    """A negative control: an edit whose tier outcome is known a priori."""

    name: str
    target: str
    mutate: Callable[[str], str]
    tier: str
    expect_caught: bool


class SyntheticProposer(Proposer):
    """Deterministic, labeled edits for demo_repo. The labels (`defect`) are the
    ground truth the harness is scored against in Phase 0."""

    id = "synthetic"

    def propose(self, substrate: SubstrateSpec) -> list[Candidate]:
        if substrate.name != "demo_repo":
            return []
        return [
            # Genuine improvements (should pass full and land).
            Candidate("orders_flatten", "orders.py", "flatten_nesting",
                      lambda _s: _ORDERS_FLATTENED),
            Candidate("billing_tidy", "billing.py", "flatten_nesting",
                      lambda _s: _BILLING_TIDIED),
            # Planted breaks, one per severity (beh x3 — the silent ones).
            Candidate("orders_syntax", "orders.py", "split_function",
                      _require_change(_syn), defect="syn"),
            Candidate("orders_unused_import", "orders.py", "reorder_imports",
                      _require_change(_lint), defect="lint"),
            Candidate("orders_type_error", "orders.py", "extract_helper",
                      _require_change(_type), defect="type"),
            Candidate("orders_wrong_tax", "orders.py", "extract_helper",
                      _require_change(_beh_tax), defect="beh"),
            Candidate("orders_wrong_gold", "orders.py", "flatten_nesting",
                      _require_change(_beh_gold), defect="beh"),
            Candidate("orders_wrong_coupon", "orders.py", "extract_helper",
                      _require_change(_beh_coupon), defect="beh"),
        ]

    def controls(self, substrate: SubstrateSpec) -> list[Control]:
        if substrate.name != "demo_repo":
            return []
        return [
            Control("ctrl_noop_passes", "orders.py", lambda s: s, "full", False),
            Control("ctrl_syntax_caught", "orders.py", _require_change(_syn), "full", True),
            Control("ctrl_behavior_caught", "orders.py", _require_change(_beh_tax), "full", True),
        ]


# --- Oracle grader ---------------------------------------------------------
class OracleGrader:
    """Independent correctness oracle = the repo's own test suite. Deliberately
    separate from the harness test gate so 'correct' is not defined by the thing
    under test (on demo_repo they coincide; on RefactorBench they won't)."""

    name = "repo-tests"

    @staticmethod
    def grade(repo_dir: Path) -> Optional[bool]:
        from refactorika.core.gates import _tool  # noqa: PLC0415

        pytest = _tool("pytest")
        if pytest is None:
            return None
        out = subprocess.run(
            [pytest, "-q", "--no-header"], cwd=str(repo_dir), capture_output=True, text=True
        )
        if out.returncode == 5:  # no tests collected
            return None
        return out.returncode == 0


# --- Tier runner -----------------------------------------------------------
@dataclass
class TierRecord:
    candidate: str
    target: str
    kind: str
    defect: Optional[str]
    tier: str
    trial: int
    caught: bool
    landed: bool
    test_ran: bool
    oracle_pass: Optional[bool]
    checks: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__


def run_tier(
    substrate: SubstrateSpec,
    candidate: Candidate,
    tier: str,
    trial: int,
    grader: OracleGrader,
) -> TierRecord:
    with checkout(substrate.path) as repo:
        target = repo / candidate.target
        original = target.read_text()
        # ruff_baseline reads the file on disk, so measure before mutating.
        baseline = ruff_baseline(target)
        new_content = candidate.mutate(original)
        caught, checks = _run_gates_with_baseline(repo, target, new_content, tier, baseline)
        landed = not caught
        test_ran = checks.get("tests") in (True, False)
        # Oracle only needs to settle genuine improvements (defect is None);
        # planted breaks are ground-truthed by their label.
        oracle_pass: Optional[bool] = None
        if landed and candidate.defect is None:
            oracle_pass = grader.grade(repo)
    return TierRecord(
        candidate=candidate.name,
        target=candidate.target,
        kind=candidate.kind,
        defect=candidate.defect,
        tier=tier,
        trial=trial,
        caught=caught,
        landed=landed,
        test_ran=test_ran,
        oracle_pass=oracle_pass,
        checks={k: v for k, v in checks.items()},
    )


def _run_gates_with_baseline(
    repo: Path, target: Path, new_content: str, tier: str, baseline: int
) -> tuple[bool, dict]:
    gates = TIER_GATES[tier]
    checks: dict[str, Optional[bool]] = {}
    target.write_text(new_content)
    for gate in gates:
        if gate == "parse":
            ok, _ = parse_gate(new_content)
        elif gate == "lint":
            ok, _ = lint_gate(target, baseline)
        elif gate == "typecheck":
            ok, _ = typecheck_gate(target)
        elif gate == "tests":
            ok, _ = test_gate(repo)
        else:  # pragma: no cover
            continue
        checks[gate] = ok
        if ok is False:
            return True, checks
    return False, checks


def run_control(substrate: SubstrateSpec, control: Control, grader: OracleGrader) -> dict:
    with checkout(substrate.path) as repo:
        target = repo / control.target
        original = target.read_text()
        baseline = ruff_baseline(target)
        new_content = control.mutate(original)
        caught, checks = _run_gates_with_baseline(
            repo, target, new_content, control.tier, baseline
        )
    ok = caught == control.expect_caught
    return {
        "name": control.name,
        "tier": control.tier,
        "expect_caught": control.expect_caught,
        "actual_caught": caught,
        "passed": ok,
        "checks": checks,
    }


# --- Enhancement (section 2) + swag (4a/4c) --------------------------------
def _compute_enhancement(substrate: SubstrateSpec, landed_goods: list[Candidate]) -> dict:
    """Apply the genuine improvements that landed, then diff code-health and the
    comprehension proxy against the original tree."""
    with checkout(substrate.path, git_init=False) as after:
        for cand in landed_goods:
            tgt = after / cand.target
            tgt.write_text(cand.mutate(tgt.read_text()))
        delta = metrics_health.health_delta(substrate.path, after)
    hb, ha = delta["health_before"], delta["health_after"]
    resolved = hb["opportunities"] - ha["opportunities"]
    pct = round(100 * resolved / hb["opportunities"], 1) if hb["opportunities"] else 0.0
    delta["opportunities_resolved_pct"] = pct
    return delta


# --- Aggregation -----------------------------------------------------------
def _tool_version(cmd: list[str]) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def _harness_sha() -> Optional[str]:
    out = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None


def _aggregate(
    records: list[TierRecord],
    controls: list[dict],
    enhancement: dict,
    comprehension: dict,
    substrate: SubstrateSpec,
    proposer_id: str,
    grader_name: str,
    trials: int,
    seed: int,
) -> dict:
    recs = [r for r in records]
    reliability: dict[str, dict] = {}
    for tier in TIERS:
        trecs = [r for r in recs if r.tier == tier]
        drecs = [r for r in trecs if r.defect]
        grecs = [r for r in trecs if r.defect is None]
        caught = sum(1 for r in drecs if r.caught)
        by_sev = {s: 0 for s in SEVERITIES}
        for r in drecs:
            if r.landed and r.defect in by_sev:
                by_sev[r.defect] += 1
        false_rej = sum(1 for r in grecs if r.caught)
        unverified = sum(1 for r in trecs if r.landed and not r.test_ran)
        reliability[tier] = {
            "catch_rate": round(caught / len(drecs), 3) if drecs else None,
            "broken_landed": sum(by_sev.values()),
            "broken_by_severity": by_sev,
            "false_rejections": false_rej,
            "false_rejection_rate": round(false_rej / len(grecs), 3) if grecs else 0.0,
            "committed_unverified": unverified,
            "defects": len(drecs),
            "good": len(grecs),
        }

    def good_landed(tier: str) -> int:
        return sum(
            1
            for r in recs
            if r.tier == tier and r.defect is None and r.landed and r.oracle_pass
        )

    headline = {
        "good_landed": {"raw": good_landed("raw"), "full": good_landed("full")},
        "broken_shipped": {
            "raw": reliability["raw"]["broken_landed"],
            "full": reliability["full"]["broken_landed"],
        },
        "silent_beh_shipped": {
            "raw": reliability["raw"]["broken_by_severity"]["beh"],
            "full": reliability["full"]["broken_by_severity"]["beh"],
        },
        "cost": {
            "good_rolled_back": reliability["full"]["false_rejections"],
            "false_rejection_rate_full": reliability["full"]["false_rejection_rate"],
            "retries_per_task": None,  # needs agent loop (Phase 1)
        },
    }

    hb, ha = enhancement["health_before"], enhancement["health_after"]
    pending_agent = {"status": "pending", "reason": "needs agent loop (Phase 1)"}
    return {
        "meta": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ"),
            "model": proposer_id,
            "harness_git_sha": _harness_sha(),
            "substrate": substrate.name,
            "grader": grader_name,
            "trials": trials,
            "seed": seed,
            "tool_versions": {
                "python": sys.version.split()[0],
                "ruff": _tool_version(["ruff", "--version"]),
                "pyright": _tool_version(["pyright", "--version"]),
                "pytest": _tool_version(["pytest", "--version"]),
                "radon": "present" if metrics_health._HAS_RADON else None,
            },
        },
        "headline": headline,
        "reliability": reliability,
        "enhancement": {
            "opportunities_before": hb["opportunities"],
            "opportunities_after": ha["opportunities"],
            "opportunities_resolved_pct": enhancement["opportunities_resolved_pct"],
            "max_nesting_before": hb["max_nesting"],
            "max_nesting_after": ha["max_nesting"],
            "longest_fn_before": hb["longest_fn"],
            "longest_fn_after": ha["longest_fn"],
            "files_improved": enhancement["files_improved"],
            "files_touched": enhancement["files_touched"],
        },
        "autonomy": pending_agent,
        "calibration": {
            "passed": sum(1 for c in controls if c["passed"]),
            "total": len(controls),
            "controls": controls,
        },
        "swag": {
            "code_health": {
                "loc_before": hb["loc"],
                "loc_after": ha["loc"],
                "avg_complexity_before": hb["avg_complexity"],
                "avg_complexity_after": ha["avg_complexity"],
                "max_nesting_before": hb["max_nesting"],
                "max_nesting_after": ha["max_nesting"],
                "longest_fn_before": hb["longest_fn"],
                "longest_fn_after": ha["longest_fn"],
                "context_files_before": hb["context_files"],
                "context_files_after": ha["context_files"],
                "complexity_tool": hb["complexity_tool"],
            },
            "comprehension_proxy": {
                "avg_before": enhancement["comprehension_before"],
                "avg_after": enhancement["comprehension_after"],
                "per_module": comprehension["per_module"],
            },
            "downstream_roi_real": pending_agent,
            "cost_dollars": pending_agent,
            "refactorbench": {
                "status": "pending",
                "reason": "needs per-repo deps + agent loop (Phase 2)",
            },
        },
        "model_matrix": {
            "status": "pending",
            "reason": "needs multiple model proposers (Phase 3)",
        },
        "tasks": [r.to_dict() for r in records],
    }


# --- Entry point -----------------------------------------------------------
def run_benchmark(
    substrate: SubstrateSpec,
    proposer: Optional[Proposer] = None,
    trials: int = 1,
    seed: int = 7,
) -> dict:
    proposer = proposer or SyntheticProposer()
    grader = OracleGrader()
    candidates = proposer.propose(substrate)
    if not candidates:
        raise ValueError(f"proposer {proposer.id} produced no candidates for {substrate.name}")

    records: list[TierRecord] = []
    for trial in range(trials):
        for cand in candidates:
            for tier in TIERS:
                records.append(run_tier(substrate, cand, tier, trial, grader))

    controls = [run_control(substrate, c, grader) for c in proposer.controls(substrate)]

    # Genuine improvements that landed under the full harness drive enhancement.
    landed_good_names = {
        r.candidate
        for r in records
        if r.tier == "full" and r.defect is None and r.landed and r.trial == 0
    }
    landed_goods = [c for c in candidates if c.defect is None and c.name in landed_good_names]
    enhancement = _compute_enhancement(substrate, landed_goods)
    comprehension = metrics_health.comprehension_tokens(substrate.path)

    return _aggregate(
        records,
        controls,
        enhancement,
        comprehension,
        substrate,
        proposer.id,
        grader.name,
        trials,
        seed,
    )
