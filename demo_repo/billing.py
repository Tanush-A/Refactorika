"""Billing helpers — intentionally has a near-duplicate of orders.compute_total logic."""

import math
from typing import Optional


def calculate_invoice_total(
    line_items: list[dict], membership: str, promo: Optional[str]
) -> float:
    """Compute invoice total with membership discount and promo code.

    Near-duplicate of orders.compute_total — same discount/coupon/tax logic
    with different variable names and structure. Planted for find_duplicates demo.
    """
    subtotal = 0.0
    for entry in line_items:
        if entry["qty"] > 0:
            if entry["price"] >= 0:
                amount = entry["price"] * entry["qty"]
                if membership == "gold":
                    if amount > 100:
                        amount = amount * 0.85
                    else:
                        amount = amount * 0.90
                elif membership == "silver":
                    amount = amount * 0.95
                subtotal += amount
    if promo == "SAVE10":
        subtotal *= 0.90
    elif promo == "SAVE20":
        subtotal *= 0.80
    tax = subtotal * 0.08
    return math.floor((subtotal + tax) * 100) / 100
