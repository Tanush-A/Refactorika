import math
import math  # noqa: F811 — duplicate import (organization smell)
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
    """Total price with tier discount, coupon, and tax. Deeply nested on purpose."""
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
