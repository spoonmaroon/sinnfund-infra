"""Teaching example: predict a return to the average of five closing prices."""

import math
from statistics import mean


def target_price(closes):
    # Replace these inputs/model with your own research before real use.
    if len(closes) < 5 or any(
        isinstance(price, bool) or not isinstance(price, (int, float))
        or not math.isfinite(price) or price <= 0 for price in closes
    ):
        raise ValueError("Provide at least five positive, finite closing prices")
    return round(mean(closes[-5:]), 2)
