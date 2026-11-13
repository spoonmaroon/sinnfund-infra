"""Equal-weight signal targets (ADR-0034), shared by the website's preview CSV and tools/stage_signals.py.

Standard library only: stage_signals.py runs on the trader's machine.
"""


def signal_targets(signals, prices, gross=1.0):
    """Signed weight per symbol, and the signals left out.

    Each signal is +gross/N if its target_price is above the price, -gross/N if below, with N = ALL signals;
    signals on one stock are added. A signal with no price (or target equal to price) is left out and its
    share stays as cash, so missing prices never enlarge the others.
    """
    weights, left_out = {}, []
    for s in signals:
        price = prices.get(s["symbol"])
        if not price or s["target_price"] == price:
            left_out.append(s)
            continue
        side = 1 if s["target_price"] > price else -1
        weights[s["symbol"]] = weights.get(s["symbol"], 0) + side * gross / len(signals)
    return weights, left_out
