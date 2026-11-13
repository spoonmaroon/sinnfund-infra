# ADR-0034: Signals become a trade CSV that the class Implementation tool runs

- **Date:** 2026-11-13
- **Status:** Accepted

## Context

The professor stages the fund's trades with the class **Implementation** tool (`ibkr_trade_manager.py`, Python 3.11, `ib_insync`). It is not in this repository, and its account numbers must not be committed here. We need our signals to come out as a file that tool runs, and we need to know how it places orders.

## How the Implementation tool works (read from its source, 2026-11-13)

**Input.** One or more CSVs with exactly these columns (others are ignored):

```
account,symbol,target
DU0000001,AAPL,0.05
DU0000001,RSP,-0.25
```

- `target` is the wanted **signed weight of the account's NetLiquidation**: `0.05` = long 5% of NLV, `-0.05` = short 5%, `0` = flat. Shares = `target × NLV / price`, rounded to whole shares.
- **It is a full rebalance, not a list of trades.** It plans every symbol in the CSV *and every stock already held in that account*. A holding missing from the CSV gets a target of 0 and is **sold or covered**. So the CSV must list everything the account should hold after the run.
- One account per row; several accounts per file is fine. A repeated `account,symbol` row overwrites the earlier one (with a warning), it does not add.
- Symbols must be US stock tickers as IBKR names them. Each is priced as `Stock(symbol, "SMART", "USD")`, so LSEG RICs (`ZBH.N`), exchange-prefixed names (`TWSE:2344`) and IBKR's space-style classes (`BRK B` vs `BRK.B`) fail to qualify and are skipped. Symbols priced under `--min-price` (default $1) are skipped.
- The sum of `|target|` is the gross leverage. ±0.05 on 40 names is 2× gross.
- Every account in the CSV must be one the TWS login manages and must report NetLiquidation, or nothing is staged.

**Run.** `ibkr_trade_manager file.csv [file2.csv …] --port 7496` (7496 live TWS, 7497 paper; the example `.bat` files do this). It fetches positions, prices, 20-day volume and spreads from TWS, prints the plan, asks for a typed confirmation, then **stages every order with `transmit=False`**. Nothing reaches the market until a person presses Transmit in TWS. It then waits for fills and writes `fills_multi_*.csv`, `fills_detail_*.csv`, `algo_performance_*.csv`, `skipped_*.csv` and `timing_*.txt`. `--two-leg` stages sells first and buys after they fill.

**Order types.** The CSV cannot choose them. `execution.stage_orders()` builds every order the same way:

| Order | How it is built |
| --- | --- |
| Type | Market order with an IBKR **Adaptive** algo (priority Normal, Patient or Urgent), or a **MIDPRICE** order |
| Which one | Assigned at random within buckets of size-vs-volume and spread, round robin over the four (an A/B test of execution quality) |
| Time in force | `DAY` only |
| Outside regular hours | Off |
| Limit price | None |

So **limit orders and good-till-cancelled (GTC) orders are not supported**. Two ways to get them:

1. **In TWS, before transmitting.** Staged orders are editable: change type to LMT, set the price and TIF to GTC, then transmit. No code change; manual per order.
2. **In the tool.** Add optional columns (for example `order_type`, `limit_price`, `tif`) to `read_trade_sheets()` in `market_data.py`, carry them on the `Trade` dataclass in `models.py`, and in `stage_orders()` build `LimitOrder(side, qty, limit_price)` and set `order.tif = row tif` instead of the fixed `MarketOrder`/`"DAY"`. That is the professor's code, so the change goes to them.

**Tests.** The tool ships with none (only a sample `test.csv`). `services/prediction-api/tools/check_trade_csv.py` runs a CSV through the tool's own `read_trade_sheets`, `plan_trades`, `assign_buckets_and_algos` and `stage_orders`, with a fake IB that records orders instead of sending them. It prints each order (side, shares, type, TIF, algo), net and gross weight, and flags symbols that will not qualify. Prices and holdings are pretend (`--price`, `--held SYM=QTY`), so share counts are illustrative. A real end-to-end test needs TWS on a paper account (port 7497).

## Decision

1. `GET /prediction/signals/portfolio.csv` (public, no key) returns pending LIVE signals as `account,symbol,target`:
   - `account` is the server's `SIGNAL_LIVE_ACCOUNT`.
   - `symbol` is the plain US ticker (`ZBH.N` → `ZBH`, via `us_ticker`).
   - `target` is **equal weight**: each priced signal gets `±1/N`, long when its target price is above the current price, short when below, so gross is 100% of the account. Two signals on one stock are added together, because the tool would otherwise keep only the last row.
   - The current price is an **LSEG delayed price** from a short stream opened by the feed's `/snapshot` (the login has no REST snapshot entitlement), cached 60 s, so there is no Twelve Data 8-per-minute limit.
   - Signals that cannot be priced, or whose target equals the price, are left out and listed in the `X-Signals-Left-Out` response header.
   - It is read-only: nothing is marked exported.
   - `GET /prediction/signals/pending` (public) lists the pending signals with who sent them.
2. The dashboard gets a public, view-only **Signals** tab: every pending LIVE signal, who sent it, and the CSV download. Anyone can see the queue; only sending (sender key), exporting (exporter key) and clearing (admin key) are protected.
3. `POST /signals/export` (exporter key, ±0.05, Twelve Data, marks signals exported) is unchanged for now.

## Consequences

- The downloaded CSV only describes the signal book. Because the tool is a full rebalance, run it **only against an account that holds nothing but the signal book**, or merge the account's other holdings into the CSV first (the rebalancing work still to come). Run against an account with other positions, it closes them.
- Equal weight changes with every new signal: the 83rd signal resizes the other 82.
- The download depends on the LSEG feed being up. When LSEG cannot be reached it returns 503 rather than a CSV with guessed directions.
- Limit/GTC orders stay manual in TWS until the tool supports them.
