from orders import compute_total


def test_gold_bulk_discount() -> None:
    items = [{"price": 60.0, "qty": 3}]  # 180 -> gold>100 -> *0.85 = 153
    assert compute_total(items, "gold", None) == round(153 * 1.08, 2)


def test_silver_and_coupon() -> None:
    items = [{"price": 50.0, "qty": 1}]  # 50 -> silver *0.95 = 47.5 -> SAVE10 *0.9 = 42.75
    total = 42.75 * 1.08
    import math

    assert compute_total(items, "silver", "SAVE10") == math.floor(total * 100) / 100


def test_skips_nonpositive() -> None:
    items = [{"price": 10.0, "qty": 0}, {"price": 20.0, "qty": 2}]  # only second counts
    assert compute_total(items, "bronze", None) == round(40 * 1.08, 2)
