"""The 30-second magic moment, scripted end-to-end against demo_repo/.

    analyze -> apply GOOD refactor (commits) -> apply BAD refactor (caught, rolled back) -> dashboard

Run:  PATH=.venv/bin:$PATH .venv/bin/python -m scripts.demo
"""

from pathlib import Path

from refactorika.core.analyze import analyze_file
from refactorika.core.apply import apply_and_verify
from refactorika.core.storage import Storage
from refactorika.dashboard import render

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "demo_repo" / "orders.py"

# A behavior-preserving flatten + dedupe-imports (what Claude would propose). Type-clean, green.
GOOD = '''from typing import Optional

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

# A "clean-looking" edit that PASSES pyright but BREAKS behavior: tax 0.08 -> 0.05.
BAD = GOOD.replace("tax = total * 0.08", "tax = total * 0.05")


def main() -> None:
    storage = Storage()
    print(f"[storage backend: {storage.backend}]\n")

    print("ANALYZE", TARGET.name)
    for o in analyze_file(str(TARGET), storage).opportunities:
        print(f"  - {o.kind:16} {o.location:28} {o.detail}")

    print("\nAPPLY good refactor (flatten + dedupe imports)...")
    r1 = apply_and_verify(str(TARGET), GOOD, "flatten_nesting", storage)
    print(f"  -> {r1.status}")

    print("\nAPPLY clean-looking but behavior-breaking edit (tax 8% -> 5%)...")
    r2 = apply_and_verify(str(TARGET), BAD, "split_function", storage)
    print(f"  -> {r2.status}  ({r2.failure_reason})")

    print(render(storage.get_log()))


if __name__ == "__main__":
    main()
