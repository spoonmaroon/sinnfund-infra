# IBKR Flex historical portfolio returns

## Can the existing token/query choose dates and return a historical range?

### Takeaway
The token is authentication, not the report definition. The saved Activity Flex Query chooses accounts, sections, fields, format, and default period; the Web Service caller may override that period with `fd`/`td` or `p`, but each request is limited to 365 days. The current importer already overrides the period, although its CLI only exposes one day and sends that day as both `fd` and `td`.

### Cited Findings
- IBKR documents three `SendRequest` modes: use the saved query period, override with `fd=yyyymmdd&td=yyyymmdd`, or override with `p`; both overrides are limited to 365 days. The request still requires both the current token and saved Query ID. — [IBKR: Configure Flex Web Service](https://ibkrguides.com/orgportal/performanceandstatements/flex3.htm)
- Flex Queries are saved report templates that select the fields, period, order, and output format. IBKR says their data is available for the four previous calendar years plus the current year. — [IBKR: Flex Queries](https://www.ibkrguides.com/orgportal/performanceandstatements/flex.htm)
- The repository's `pull(token, query_id, day)` sends `fd=day` and `td=day`, so the deployed design asks for one statement day regardless of the saved query's default period. The CLI has `--date`, but no range arguments. — [Current `flex-ingest.py`](https://github.com/SinnQuant/sinnfund-infra/blob/main/services/flex-ingest/flex-ingest.py#L80-L86)
- The repository README currently says date parameters are omitted and the saved query controls the period; that statement conflicts with the implementation above. — [Current flex-ingest README](https://github.com/SinnQuant/sinnfund-infra/blob/main/services/flex-ingest/README.md)

### Inferences
- The existing token can request a different date range without being replaced, provided it is still valid, the Query ID is valid, and the requested dates fall inside IBKR's availability window. A new saved query is needed for missing fields, not for the date override itself.
- A single request can cover up to 365 calendar days, but a multi-day Activity Flex report should not be assumed to contain a daily NAV series. IBKR removed the Activity Statement/Flex Query NAV Time Series section in 2017, so a controlled 7-day report must be inspected before relying on row cardinality. — [IBKR statements release archive](https://ibkrguides.com/releasenotes/archive/statements-and-reports-archive.htm)
- `ALL` through Activity Flex is bounded by the four-prior-calendar-years-plus-current-year availability. If an account's inception predates that window, Flex Web Service alone cannot recreate true since-inception performance.

### Gaps
- IBKR's public guide does not say whether the 365-day limit counts both endpoints. Backfill code should make chunks conservatively smaller than the limit and verify the returned `fromDate`/`toDate`.
- The token's configured expiration and IP restriction cannot be inferred from archived reports. The latest successful archive proves only that it authenticated at that pull time.

## Which Flex sections and fields are needed for faithful returns?

### Takeaway
Use IBKR's own TWR from **Change in NAV** as the primary account-return field, with official beginning/ending NAV and cash-flow components for reconciliation. Add **Account Information** for base currency and **NAV Summary in Base** for dated totals. Cash-only or positions-only data is insufficient.

### Cited Findings
- **Change in NAV** supplies `From Date`, `To Date`, `Starting Value`, `Ending Value`, `Deposits/Withdrawals`, internal cash transfers, asset transfers, linking adjustments, FX translation, fees, commissions, P&L components, and `TWR`. IBKR defines that TWR as performance independent of contributions and withdrawals. — [IBKR: Change in NAV](https://ibkrguides.com/reportingreference/reportguide/changeinnav_fq.htm)
- **Net Asset Value Summary in Base** supplies `Report Date` and `Total` NAV, with cash, stocks, options, commodities, bonds, and accrual components. — [IBKR: NAV Summary in Base](https://ibkrguides.com/reportingreference/reportguide/net%20asset%20value%20(nav)%20summary%20in%20base.htm)
- **Account Information** supplies the account's base `Currency`, account ID/alias, type, and opening/funding dates. Only the identity and currency fields are needed here; address, email, birth date, and other personal fields should remain unselected. — [IBKR: Account Information](https://ibkrguides.com/reportingreference/reportguide/account%20information%20activity%20flex.htm)
- **Cash Report** supplies starting/ending cash and deposits, withdrawals, account transfers, internal transfers, fees, dividends, interest, trades, and FX translation, but it is a cash reconciliation rather than total portfolio NAV. — [IBKR: Cash Report](https://ibkrguides.com/reportingreference/reportguide/cash%20reportfq.htm)
- **Cash Transactions** supplies transaction date/time, amount, type, currency, and FX rate to base; **Statement of Funds** supplies a line-item cash ledger and activity codes such as deposit and withdrawal. These are useful for investigating mismatches but are not required for every normal page load. — [IBKR: Cash Transactions](https://ibkrguides.com/reportingreference/reportguide/cash%20transactionsfq.htm); [IBKR: Statement of Funds](https://ibkrguides.com/reportingreference/reportguide/statement%20of%20fundsfq.htm)
- **Month and Year to Date Performance Summary in Base** contains MTD/YTD MTM and realized P&L amounts by underlying; it does not document a portfolio return percentage. — [IBKR: MTD/YTD Performance Summary in Base](https://ibkrguides.com/reportingreference/reportguide/month%20and%20year%20to%20date%20performance%20summary%20in%20basefq.htm)
- **Mark-to-Market Performance Summary in Base** also describes a statement-period TWR and can be retained as a cross-check, but Change in NAV is the section that explicitly combines NAV, flows, dates, and TWR in one account-level record. — [IBKR: MTM Performance Summary in Base](https://www.ibkrguides.com/reportingreference/reportguide/mark-to-market%20performance%20summary%20in%20base%20fq.htm)

### Inferences
- Smallest useful returns query: Account Information (`Account ID`, `Account Alias`, `Currency`, `Date Opened/Funded`), Change in NAV (all date, NAV, flow, adjustment, and TWR fields), and NAV Summary in Base (`Report Date`, `Total`, with asset components if reconciliation is desired). Cash Report can remain for audit; transaction-level cash data can be a separate diagnostic query to keep normal files smaller.
- Do not calculate return as `(ending NAV / starting NAV) - 1` unless cash flows are proven zero. Do not use `CashReportCurrency.endingCash` as NAV, and do not use sum of positions as official NAV.
- The MTD/YTD performance section is useful for dollar P&L attribution, not the requested percentage-return buttons.

### Gaps
- The exact XML attribute names and row layout must be confirmed from one generated test report after the saved query is changed; IBKR's reference describes fields but does not provide a complete XML example for these sections.
- Whether the account bundle and consolidated fund statement can be emitted together under one Query ID is not documented. Two narrowly scoped Query IDs may be needed: account-by-account and All-in-One Summary.

## What can be calculated from the current archives?

### Takeaway
The current archives can support holdings, allocations, and cash/position views, but they still cannot support faithful 1D/1W/1M/3M/YTD/1YR/ALL investment returns. A recent saved-query change added the NAV-summary element, but its records contain only `accountId`; there is still no NAV total, report date, base currency, Change in NAV, or TWR.

### Cited Findings
- The repository's October 24 audit found `CashReport`/`CashReportCurrency`, but no `AccountInformation`, `EquitySummaryByReportDateInBase`, or `ChangeInNAV`; it explicitly warns that ending cash is not total NAV. — [INC-0007](https://github.com/SinnQuant/sinnfund-infra/blob/main/docs/incidents/0007-ibkr-flex-archives-missing-official-nav.md)
- The raw gzip is intentionally preserved before downstream parsing, so an absent source section cannot be repaired by changing only the website parser. — [ADR-0007](https://github.com/SinnQuant/sinnfund-infra/blob/main/docs/decisions/0007-archive-raw-flex-xml.md)
- IBKR's TWR method needs beginning market value, ending market value, and cash flow for each subperiod, then geometrically links subperiod returns. For multiple accounts, IBKR says to add market values and cash flows across components for each day before calculating and linking combined returns. — [IBKR PortfolioAnalyst return methodology](https://www.ibkrguides.com/portfolioanalyst/pa-white-papers-ie.pdf)

### Inferences
- Read-only production inventory on November 6, 2026 found daily archive folders from October 9 through November 5. All archives still lack `AccountInformation`, `ChangeInNAV`, and `MTMPerformanceSummaryInBase`. Archives from October 29 onward contain `EquitySummaryByReportDateInBase`, but each inspected record has only `accountId`, so the saved query selected the section without the value/date fields. No credentials or field values were read during this inventory.
- The current archive span is too short for 1M, 3M, YTD, 1YR, or ALL even if every required value existed. More importantly, the missing official NAV/TWR fields block a trustworthy 1D or 1W return as well.
- Positions and closing marks can produce indicative changes in marked holdings, but trades, deposits/withdrawals, internal transfers, fees, derivatives, FX, and missing valuation coverage would make that a different metric from IBKR account return. It should not be labeled performance.

### Gaps
- The recent partial NAV-summary query edit is not documented in the repository, so its intended field selection and rollout date are unknown.
- A broker-authorized statement or PortfolioAnalyst value has not yet been reconciled against a newly complete Flex return record.

## What is the safest one-time backfill and ongoing calculation design?

### Takeaway
Do not begin with thousands of daily requests. First create a versioned returns query and request the seven exact periods needed by the UI. Only do a one-business-day-at-a-time backfill if the product also needs a daily chart, animation, or arbitrary date ranges. Keep the current archives immutable and write backfill output under a separate versioned path.

### Cited Findings
- Flex Web Service limits requests to one per second and ten per minute per token. Error `1018` signals too many requests; `1019` means generation is still in progress. IBKR also documents temporary generation/heavy-load/retrieval errors (`1001`, `1009`, `1021`) and terminal token/query/IP errors (`1012`–`1015`). — [IBKR: Version 3 error codes](https://ibkrguides.com/orgportal/performanceandstatements/flex3error.htm)
- Programmatic access must send a User-Agent. Tokens default to six-hour validity, may be restricted to an IP address, and generating a new token invalidates the current token. — [IBKR: Configure Flex Web Service](https://ibkrguides.com/orgportal/performanceandstatements/flex3.htm)
- IBKR's run UI supports an **All-in-One Summary**, which consolidates multiple accounts as if they were one portfolio, and an **Account-by-Account Bundle**. — [IBKR: Run a Flex Query](https://www.ibkrguides.com/adminportal/performanceandstatements/runflex.htm)
- IBKR says TWR subperiods are geometrically linked; returns from multiple accounts must be based on summed daily market values and cash flows, not an average of account percentage returns. — [IBKR PortfolioAnalyst return methodology](https://www.ibkrguides.com/portfolioanalyst/pa-white-papers-ie.pdf)

### Inferences
- Recommended sequence:
  1. Clone the existing query as `returns-v1`; do not silently alter historical source semantics. Select only the fields listed above and XML output.
  2. Generate one single-day report and validate expected accounts, base currency, dated NAV total, Change-in-NAV start/end values, flows, and TWR.
  3. Generate one controlled 7-day report to discover whether it contains one period summary or usable daily rows.
  4. For the fixed buttons, request exact periods ending on the latest common business date: 1D, 1W, 1M, 3M, YTD, and 1YR. `ALL` should use sequential chunks no longer than 365 days and geometrically link their TWRs, only as far back as Flex availability permits.
  5. Cache the normalized results and recompute once after the daily archive lands. UI requests should never call IBKR directly.
  6. If a daily chart/animation is required, pull one business day at a time once, sequentially, skip already checksummed dates, and pause on `1018`; never run parallel requests that share a token.
  7. Preserve raw XML under a separate query-version/range path with checksum and retrieval time. Store normalized daily/account records separately with source provenance.
  8. Reconcile a sample of account and consolidated returns to Portal/PortfolioAnalyst before publishing.
- The documented ten-request-per-minute limit counts HTTP requests, not completed reports. Every report requires at least one `SendRequest` and one `GetStatement`, often more polls, so a safe backfill must target materially fewer than ten reports per minute.
- For fund-level performance, prefer a saved All-in-One Summary query. If the team calculates aggregation itself, sum same-day beginning NAV, ending NAV, and external cash flows across accounts, net internal transfers within the included fund, calculate one combined daily return, then geometrically link it. Never average account TWRs.
- Product definitions should be explicit: 1D is the latest business-day return; 1W should be either five trading sessions or seven calendar days; 1M/3M/1YR should anchor by calendar date and use the prior available business close; YTD starts at the previous year-end; ALL starts at the earliest trustworthy common record.

### Gaps
- IBKR does not document a bulk daily NAV time-series response for Activity Flex; the controlled 7-day probe is required before choosing between range requests and daily backfill.
- Closed, newly linked, or missing accounts can change the population over time. Acceptance rules for the fund's historical membership need an owner decision before `ALL` is published.

## Can Activity Flex supply benchmark returns?

### Takeaway
No benchmark section is documented for Activity Flex. PortfolioAnalyst supports benchmark comparison and CSV/PDF performance reports, but the Flex Web Service is documented for Activity and Trade Confirmation Flex Queries. Benchmark data therefore needs a separate read-only source or a PortfolioAnalyst export.

### Cited Findings
- IBKR's complete Activity Flex Query reference lists NAV, cash, performance, positions, trades, and other accounting sections, but no benchmark section. — [IBKR: Activity Flex Query Reference](https://ibkrguides.com/reportingreference/reportguide/activity%20flex%20query%20reference.htm)
- PortfolioAnalyst lets users add up to three benchmarks and view TWR or MWR for 7D, MTD, 1M, YTD, 1Y, or a custom range. — [IBKR: PortfolioAnalyst dashboard](https://www.ibkrguides.com/advisorportal/performanceandstatements/pa_viewingaccountperformance.htm)
- PortfolioAnalyst custom reports support custom/calendar/MTD/YTD/since-inception periods, TWR or MWR, and CSV or PDF output. — [IBKR: Create a PortfolioAnalyst custom report](https://www.ibkrguides.com/advisorportal/performanceandstatements/pa_customreports_create.htm)
- PortfolioAnalyst's Time Period Performance Statistics can emit TWR by day, month, or quarter, while MWR is not available daily. — [IBKR: Time Period Performance Statistics](https://www.ibkrguides.com/reportingreference/reportguide/timeperiodperformancestatistics.htm)

### Inferences
- The easiest authoritative validation is a one-time PortfolioAnalyst CSV with daily TWR and the chosen benchmark. It can validate the Flex-derived account history, but no official unattended PortfolioAnalyst Web Service endpoint was found in the cited documentation.
- For the production read-only UI, use an independently licensed benchmark total-return series (for example the exact benchmark identifier selected by the fund) and compound it over the same dates as portfolio TWR. A price-only index will misstate performance relative to a total-return benchmark.
- Store the benchmark identifier, currency, calendar, source, and whether it is price or total return. The screenshot's generic “vs Bench” label is not enough to reproduce the number.

### Gaps
- The intended benchmark is not identified in the request or screenshot.
- No official IBKR documentation was found for retrieving PortfolioAnalyst benchmark series through the Flex Web Service token.
