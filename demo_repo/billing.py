"""Billing helpers — a near-duplicate of orders.compute_total, *structurally identical*
(same control-flow shape, different names). The LLM decomposition pass decomposes both;
the second reuses the first's helper names via decision memory (the consistency beat)."""

import math
from typing import Optional


def calculate_invoice_total(
    line_items: list[dict], membership: str, promo: Optional[str]
) -> float:
    """Invoice total with membership discount, promo, and tax. Same shape as compute_total."""
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
                else:
                    if membership == "silver":
                        amount = amount * 0.95
                subtotal = subtotal + amount
    if promo is not None:
        if promo == "SAVE10":
            subtotal = subtotal * 0.90
        else:
            if promo == "SAVE20":
                subtotal = subtotal * 0.80
    tax = subtotal * 0.08
    return math.floor((subtotal + tax) * 100) / 100
