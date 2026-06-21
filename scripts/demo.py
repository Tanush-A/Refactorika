"""The scripted demo, end-to-end against demo_repo/.

    analyze · find_duplicates · find_dead_code · generate_docs   (advisory tour)
      -> CAMPAIGN (v3): audit_repo -> get_plan -> confirm_plan
                        -> execute each planned edit through the gate stack
                        -> behavior-breaking edit caught + rolled back
                        -> render_campaign with before -> after health

Run:  PATH=.venv/bin:$PATH .venv/bin/python -m scripts.demo
"""

from pathlib import Path

from refactorika.analysis.audit import audit_repo, build_plan
from refactorika.analysis.dead_code import find_dead_code
from refactorika.analysis.duplicates import find_duplicates
from refactorika.core.analyze import analyze_file
from refactorika.core.apply import apply_and_verify
from refactorika.core.storage import Storage
from refactorika.dashboard import render_campaign
from refactorika.docs_gen import generate_docs
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.context import ContextRetriever
from refactorika.memory.vector_index import VectorIndex

ROOT = Path(__file__).resolve().parent.parent
DEMO_REPO = ROOT / "demo_repo"
TARGET = DEMO_REPO / "orders.py"

# Behavior-preserving flatten of billing.calculate_invoice_total (planned task 1).
BILLING_GOOD = '''"""Billing helpers — invoice total with membership discount and promo code."""

import math
from typing import Optional

MEMBERSHIP_RATE = {"gold_bulk": 0.85, "gold": 0.90, "silver": 0.95}
PROMO_RATE = {"SAVE10": 0.90, "SAVE20": 0.80}


def _entry_amount(entry: dict, membership: str) -> float:
    if entry["qty"] <= 0 or entry["price"] < 0:
        return 0.0
    amount = entry["price"] * entry["qty"]
    if membership == "gold":
        return amount * (MEMBERSHIP_RATE["gold_bulk"] if amount > 100 else MEMBERSHIP_RATE["gold"])
    if membership == "silver":
        return amount * MEMBERSHIP_RATE["silver"]
    return amount


def calculate_invoice_total(
    line_items: list[dict], membership: str, promo: Optional[str]
) -> float:
    """Compute invoice total with membership discount and promo code."""
    subtotal = sum(_entry_amount(entry, membership) for entry in line_items)
    subtotal *= PROMO_RATE.get(promo, 1.0) if promo is not None else 1.0
    tax = subtotal * 0.08
    return math.floor((subtotal + tax) * 100) / 100
'''

# A behavior-preserving flatten + dedupe-imports (what Claude would propose). Type-clean, green.
# Preserves every existing symbol (incl. _legacy_discount + _compute_shipping that
# the demo's later dead-code / "reached via test" sections refer to).
GOOD = '''import math
from typing import Optional

TIER_RATE = {"gold_bulk": 0.85, "gold": 0.90, "silver": 0.95}
COUPON_RATE = {"SAVE10": 0.90, "SAVE20": 0.80}


def _legacy_discount(price: float) -> float:
    """Old discount logic — superseded by compute_total; nothing calls this."""
    return price * 0.75


def _compute_shipping(weight: float) -> float:
    """Shipping calculator — reached via test_shipping below; should not be flagged dead."""
    if weight <= 1.0:
        return 3.99
    return 3.99 + (weight - 1.0) * 1.50


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
    total = sum(_line_total(item, customer_tier) for item in items)
    total *= COUPON_RATE.get(coupon, 1.0) if coupon is not None else 1.0
    tax = total * 0.08
    return math.floor((total + tax) * 100) / 100
'''

# A "clean-looking" edit that PASSES pyright but BREAKS behavior: tax 0.08 -> 0.05.
BAD = GOOD.replace("tax = total * 0.08", "tax = total * 0.05")


def _section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> None:
    storage = Storage()
    vector_index = VectorIndex(storage)
    agent_memory = AgentMemory(storage)
    context_retriever = ContextRetriever(storage, agent_memory)

    print(f"[storage backend: {storage.backend}]")

    # ------------------------------------------------------------------
    # 1. ANALYZE — ranked structural-refactor opportunities
    # ------------------------------------------------------------------
    _section(f"ANALYZE  {TARGET.name}")
    for o in analyze_file(str(TARGET), storage).opportunities:
        print(f"  - {o.kind:16} {o.location:28} {o.detail}")

    # ------------------------------------------------------------------
    # 2. FIND_DUPLICATES — orders.compute_total vs billing.calculate_invoice_total
    # ------------------------------------------------------------------
    _section("FIND_DUPLICATES  demo_repo/")
    dup = find_duplicates(str(DEMO_REPO), storage, vector_index)
    pairs = dup.get("pairs", [])
    if pairs:
        top = pairs[0]
        a, b = top["a"], top["b"]
        target = top["consolidation_target"]
        print(f"  top pair [{top['match_type']}, rank {top.get('rank')}]")
        print(f"    {a['name']}  ({Path(a['file']).name}:{a['line']})")
        print(f"    {b['name']}  ({Path(b['file']).name}:{b['line']})")
        print(f"    similarity: {top.get('similarity')}")
        print(f"    keep -> {target['name']}  ({top.get('reason')})")
        print("    -> Claude would propose consolidate_duplicate via apply_and_verify_multi.")
    else:
        print("  no structural (exact-shape) duplicate pairs found")
        print("  (orders.compute_total and billing.calculate_invoice_total are a")
        print("   *semantic* near-duplicate: same discount/coupon/tax logic, different")
        print("   shape + variable names — caught only by the embedding tier below.)")
    if "semantic" in dup:
        print(f"  semantic tier: {dup['semantic']}")

    # ------------------------------------------------------------------
    # 3. FIND_DEAD_CODE — flagged private helper, high confidence
    # ------------------------------------------------------------------
    _section("FIND_DEAD_CODE  demo_repo/")
    dead = find_dead_code(str(DEMO_REPO), storage)
    print(f"  entry points: {', '.join(dead.get('entry_points', []))}")
    symbols = dead.get("dead_symbols", [])
    if symbols:
        for s in symbols:
            print(
                f"  - [{s['confidence']:6} rank {s['rank']}] {s['name']}  "
                f"({Path(s['file']).name}:{s['line']})"
            )
        print(f"    reason: {symbols[0]['reason']}")
        print("    -> Claude would propose remove_dead_code; pytest proves safe before commit.")
    else:
        print("  no dead symbols flagged")
    print(
        "  (_compute_shipping is reached via test_orders.test_shipping — "
        "correctly NOT flagged.)"
    )

    # ------------------------------------------------------------------
    # 4. GENERATE_DOCS — living context file + agent memory
    # ------------------------------------------------------------------
    _section(f"GENERATE_DOCS  {TARGET.name}")
    docs = generate_docs(str(TARGET), storage, agent_memory, context_retriever)
    print(f"  context file: {docs.get('context_file')}")
    print(f"  persisted to: {docs.get('persisted_to')}")
    print(f"  incremental:  {docs.get('incremental')}")
    module = docs.get("module", {})
    if module.get("purpose_hint"):
        print(f"  purpose hint: {module['purpose_hint']}")
    if module.get("exports"):
        names = ", ".join(e["name"] for e in module["exports"])
        print(f"  exports:      {names}")

    # ------------------------------------------------------------------
    # 5. CAMPAIGN (v3) — audit -> plan -> human confirm -> verified execution
    # ------------------------------------------------------------------
    _section("AUDIT_REPO  demo_repo/  (forest-level, before)")
    audit_before = audit_repo(str(DEMO_REPO), storage)
    print(f"  files scanned: {audit_before.files_scanned}   "
          f"opportunities: {audit_before.total_opportunities}   "
          f"headline: {audit_before.dominant_finding}")
    for e in audit_before.entries:
        print(f"  - {Path(e.file).name:14} score {e.score:4}  ({len(e.opportunities)} opps)")

    _section("GET_PLAN  (dependency-ordered, fewest-dependents-first)")
    plan = build_plan(str(DEMO_REPO), storage)
    for t in plan.tasks:
        print(f"  #{t.order}  {Path(t.file).name:14} {len(t.opportunities)} opps  "
              f"{len(t.dependents)} dependents {t.dependents or ''}")

    _section("CONFIRM_PLAN  (the human checkpoint)")
    plan.confirmed, plan.decision = True, "approve"   # what confirm_plan('approve') does
    storage.save_plan(plan.to_dict())
    print("  human approved -> CONFIRMED ✓   executing in plan order...")

    # Execute each planned edit through the verified spine.
    good_by_file = {"billing.py": BILLING_GOOD, "orders.py": GOOD}
    for t in plan.tasks:
        name = Path(t.file).name
        content = good_by_file.get(name)
        if content is None:
            print(f"  #{t.order} {name}: (Claude would propose an edit here)")
            continue
        r = apply_and_verify(t.file, content, "flatten_nesting", storage)
        print(f"  #{t.order} {name}: -> {r.status}")

    _section("TRUST SPINE  clean-looking but behavior-breaking edit (tax 8% -> 5%)")
    r_bad = apply_and_verify(str(TARGET), BAD, "split_function", storage)
    print(f"  -> {r_bad.status}  ({r_bad.failure_reason})")

    # ------------------------------------------------------------------
    # 6. THE VISIBLE STORY — audit -> plan -> gate log -> before/after health
    # ------------------------------------------------------------------
    audit_after = audit_repo(str(DEMO_REPO), storage)
    _section("CAMPAIGN DASHBOARD")
    print(render_campaign(
        audit_before.to_dict(), plan.to_dict(), storage.get_log(), audit_after.to_dict()
    ))


if __name__ == "__main__":
    main()
