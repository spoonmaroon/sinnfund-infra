# Close-route compatibility service

The close-only portfolio implementation has been retired. `web.py` is a small
read-only compatibility proxy from port `8001` to the merged portfolio API on
port `8002`. This keeps `/close/` working without maintaining a second parser
or a second interpretation of portfolio values.

Both public paths now expose the same response:

```text
/live/api/v1/positions
/close/api/v1/positions
```

Each updated position contains the original IBKR statement fields
(`close_mark_price`, `close_market_value`) and the LSEG delayed fields
(`delayed_mark_price`, `delayed_market_value`) plus their difference and quote
metadata. The compatibility response includes `X-Portfolio-Service: merged`.

The original close parser remains in this directory only as a command-line
archive inspection tool. It is no longer used to serve requests.
