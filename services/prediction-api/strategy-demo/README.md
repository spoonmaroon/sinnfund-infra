# Strategy → signal: demo kit

Run everything below from this folder. Requires Python 3.10+ and this repository;
no packages to install. Offline rehearsal needs no key. Actual create/get/edit/delete
requires an issued **sender key**, network access and a running test API.

```sh
cd /Users/enoch/sinnfund-infra/services/prediction-api/strategy-demo
python3 --version
```

If the checkout is somewhere else, change the path above.

## What you are demonstrating

`strategy.py` averages the last five synthetic closing prices: 98, 100, 102,
104 and 96. The resulting forecast is **100.00**, versus the sample last close of
96. `demo.py` turns that target into the API's signal format and uses the existing
sender client. Change `target_price()` and its inputs to plug in your strategy.

This is a teaching calculation, not a validated trading strategy or market data.
The API receives a ticker, target price and timestamp; you do not send Python
strategy code, a buy/sell instruction or position size.

We use a fictional ticker `DEMO` and the **LIVE ledger destination on the test
server**. Here LIVE means eligible for a later export, not an immediate broker
order. It is still a real server write. Coordinate with the test operator and
delete the demo afterward: a pending fictional ticker can block that server's
export. PAPER is a different flow that requests quotes and queues paper orders;
this kit does not use it. Do not point these synthetic examples at production.

## 1. Rehearse without a key

```sh
# Calculate the target and save a unique ID, target and fixed timestamp.
python3 demo.py prepare

# Show the exact create command; this does not contact the API.
python3 demo.py create

# These also preview without changing the server.
python3 demo.py edit --price 103
python3 demo.py delete

# Run the offline checks.
python3 -m unittest discover -s . -p 'test_*.py'
```

The saved `signal.json` will contain `destination: LIVE`, `ticker: DEMO`,
`price: 100`, a unique `demo-...` ID, and a Unix timestamp in seconds.
`prepare` refuses to overwrite it. Reuse that file for the entire demo.
For another demonstration, use `--file runs/another-demo.json` on every command
(create `runs` first). Never discard the file while a send outcome is uncertain.

## 2. Obtain and save your key once

Ask your test-service operator for an already-issued sender key. Saving a key
does not issue it or grant permissions. If you are the operator, the existing
interactive issuance command is `python3 ../tools/signal_keys.py create`;
it needs the operator's configured SSH access. See [key operations](../README.md#2-issue-or-rotate-a-sender-key).

```sh
# Clear overrides if you want to use the default saved key.
unset SIGNAL_API_KEY SIGNAL_API_KEY_FILE

# Paste the issued key at the hidden prompt, then press Enter.
python3 demo.py save-key

# Explicitly select the documented draft/test endpoint.
export SIGNAL_API_BASE_URL=https://sinnfund.jkoh.dev/prediction

# Read-only check: a JSON list (possibly []) means your key was accepted.
python3 demo.py list
```

The client saves the key to `~/.config/sinnfund/signal-sender.key` with private
`0600` permissions. Do this as the same OS user who will run the scripts or cron.
Never paste the key into code, a command argument, a recording, or Git.
For a separate key file, set `SIGNAL_API_KEY_FILE` to its absolute path and give
it `0600` permissions; do not also set `SIGNAL_API_KEY`.

## 3. Live demo: create → inspect → edit → inspect → delete

Run `prepare` from step 1 first. These commands act only on the ID in your file:

```sh
# Send the saved 100.00 target to POST /signals.
python3 demo.py create --send

# Read the accepted record and its audit history.
python3 demo.py get

# Change the target to 103.00. The client fetches the current revision first.
python3 demo.py edit --price 103 --send
python3 demo.py get

# Remove this signal from the pending set, preserving its audit history.
python3 demo.py delete --send
python3 demo.py get
```

Expect a pending record after creation, an updated price/revision after editing,
and `status: deleted` after deletion. The final history is the proof of the full
lifecycle. No export or broker execution is part of this demo.

Suggested narration: “My strategy runs on my computer. It calculates a target
price. I save that forecast as one signal, authenticate using my private key,
and send it to the central API. I can inspect it, revise it, and withdraw it.
The API retains the history.”

If a create times out, run `get` to check, or repeat **the same create with the
same saved file**. The API treats identical submissions as idempotent. After
editing, do not resend the original create payload: it will conflict with the
new content. Deleted IDs do not become new pending signals on retry; prepare a
new file for a new demo. A `409` during edit/delete means inspect again before
retrying. `401` means check your key; `503` needs the service operator. A failed
get/network request does not prove that a previous create failed.

## 4. Automate one daily run

`run_daily.sh` prepares one saved request per UTC date and reuses it on subsequent
runs that day. It works from any directory. By default it only previews:

```sh
sh run_daily.sh

# Explicitly send today's saved sample (only after arranging a test with the operator).
sh run_daily.sh --send

# Inspect and clean up that scheduled demo, using the same daily file.
python3 demo.py get --file "runs/$(date -u +%F).json"
python3 demo.py delete --file "runs/$(date -u +%F).json" --send
```

For a daily 9 AM weekday **preview**, open `crontab -e` and add the following,
replacing the Python and repository paths with yours (`command -v python3`
shows your Python path). This is an example; nothing installs a schedule for you.

```cron
# Cron uses the scheduler machine's timezone; daily files use UTC dates.
# Use absolute paths because cron has a minimal environment.
0 9 * * 1-5 PYTHON=/absolute/path/to/python3 /bin/sh /absolute/path/to/sinnfund-infra/services/prediction-api/strategy-demo/run_daily.sh >> /absolute/path/to/sinnfund-infra/services/prediction-api/strategy-demo/demo.log 2>&1
```

To schedule actual submissions, add `--send` immediately after `run_daily.sh`.
First replace the static teaching data with fresh validated strategy inputs,
choose the operator-approved endpoint/ticker, and agree on who consumes or
cleans up the signals. Otherwise each new day accumulates another pending sample.
Do not run multiple schedulers against the same demo directory. If two first runs
overlap, one may fail rather than overwrite the other's saved request.

The machine must be awake and online; cron does not catch up a missed run.
The script exits on errors and does not silently retry. Check `demo.log`.
Remove the cron line in `crontab -e` to stop automation. For cleanup after UTC
midnight, specify the original date's file, not today's.

## Files

| File | Purpose |
| --- | --- |
| `strategy.py` | Small strategy function to replace with your model |
| `demo.py` | Save key, prepare, preview, create, get, edit, delete, list |
| `run_daily.sh` | Commented automation entry point |
| `test_demo.py` | Offline strategy and lifecycle checks |
| `signal.json`, `runs/` | Generated requests, excluded from Git |

The scripts reuse `../tools/signal_client.py`; keep this folder inside the checkout.
