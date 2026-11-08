# ADR-0028: Show net liquidation value as positions plus Flex ending cash; keep return history off until NAV exists

- **Date:** 2026-11-08
- **Status:** Accepted — provisional until the Flex query carries NAV (INC-0007)

## Context

The dashboard summed position values only. Long/short books looked nearly empty (a short-interest book with about $638k of equity showed about $3k of net positions) and cash-only accounts were missing. The saved Flex query still omits daily NAV and TWR (INC-0007), but each statement carries `CashReportCurrency[currency=BASE_SUMMARY].endingCash` in USD. A P&L-only history curve had been drawn from 20 daily statements.

## Options considered

- Keep position value only.
- Approximate net liquidation value (NLV) as positions + ending cash.
- Wait for official NAV before showing any account totals.

## Decision

Show **NLV = positions (USD) + ending cash** per account and for the fund, label it as such, and use it for "% allocation" and for risk as % of NLV. Do not call it NAV. Turn the portfolio-history chart off and show "turns on once the IBKR Flex query includes daily NAV"; the API detects `EquitySummaryByReportDateInBase … total=` in the latest archive and re-enables itself.

## Rationale

Checked against the fund's 10/31 account sheet: all-cash and long/short accounts reconcile to within market moves (Quant matched exactly at $1,586). A P&L-only curve ignores cash and flows and invites misreading as a return.

## Consequences and limitations

NLV excludes accrued interest and dividends and is not broker-reconciled NAV. INC-0007's acceptance criteria still stand.

## Follow-up / revisit criteria

Add Report Date and Total (NAV in Base) and Starting/Ending Value, Deposits/Withdrawals and TWR (Change in NAV) to the saved query; then replace NLV with NAV and draw a TWR history.

Links: INC-0007, ADR-0018, `services/flex-web-live/README.md`.
