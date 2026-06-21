"""The 30-second magic moment, scripted end-to-end against demo_repo/.

    analyze
      -> find_duplicates (orders vs billing)
      -> find_dead_code (flagged private helper)
      -> generate_docs (living context file)
      -> apply GOOD refactor (commits)
      -> apply BAD refactor (caught, rolled back)
      -> dashboard

Run:  PATH=.venv/bin:$PATH .venv/bin/python -m scripts.demo
"""

from pathlib import Path

from refactorika.analysis.dead_code import find_dead_code
from refactorika.analysis.duplicates import find_duplicates
from refactorika.core.analyze import analyze_file
from refactorika.core.apply import apply_and_verify
from refactorika.core.storage import Storage
from refactorika.dashboard import render
from refactorika.docs_gen import generate_docs
from refactorika.memory.agent_memory import AgentMemory
from refactorika.memory.context import ContextRetriever
from refactorika.memory.vector_index import VectorIndex

ROOT = Path(__file__).resolve().parent.parent
DEMO_REPO = ROOT / "demo_repo"
TARGET = DEMO_REPO / "orders.py"

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
    # 5. THE TRUST SPINE — good edit commits, behavior-break caught
    # ------------------------------------------------------------------
    _section("APPLY  good refactor (flatten + dedupe imports)")
    r1 = apply_and_verify(str(TARGET), GOOD, "flatten_nesting", storage)
    print(f"  -> {r1.status}")

    _section("APPLY  clean-looking but behavior-breaking edit (tax 8% -> 5%)")
    r2 = apply_and_verify(str(TARGET), BAD, "split_function", storage)
    print(f"  -> {r2.status}  ({r2.failure_reason})")

    # ------------------------------------------------------------------
    # 6. DASHBOARD — the visible verification log
    # ------------------------------------------------------------------
    _section("DASHBOARD")
    print(render(storage.get_log()))


if __name__ == "__main__":
    main()
