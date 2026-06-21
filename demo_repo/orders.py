# `json` below is unused on purpose — deterministic cleanup removes it.
import json
import math
from typing import Optional


def _round_money(value: float) -> float:
    """Round to cents. Only referenced by _legacy_discount — which is itself dead,
    so this becomes orphaned once _legacy_discount is removed (cascade)."""
    return math.floor(value * 100) / 100


def _legacy_discount(price: float) -> float:
    """Old discount logic, superseded by compute_total. Nothing calls it (dead)."""
    return _round_money(price * 0.75)


def _compute_shipping(weight: float) -> float:
    """Shipping calculator — reached via test_shipping below; must NOT be flagged dead."""
    if weight <= 1.0:
        return 3.99
    return 3.99 + (weight - 1.0) * 1.50


def compute_total(items: list[dict], customer_tier: str, coupon: Optional[str]) -> float:
    """Total price with tier discount, coupon, and tax. Deeply nested on purpose —
    a decomposition candidate for the --llm pass."""
    total = 0.0
    for item in items:
        if item["qty"] > 0:
            if item["price"] >= 0:
                line = item["price"] * item["qty"]
                if customer_tier == "gold":
                    if line > 100:
                        line = line * 0.85
                    else:
                        line = line * 0.90
                else:
                    if customer_tier == "silver":
                        line = line * 0.95
                total = total + line
    if coupon is not None:
        if coupon == "SAVE10":
            total = total * 0.90
        else:
            if coupon == "SAVE20":
                total = total * 0.80
    tax = total * 0.08
    return math.floor((total + tax) * 100) / 100
