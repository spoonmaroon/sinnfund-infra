# Prediction API Operations

FastAPI service for legacy predictions, authenticated research signals, and the separate paper-order
inbox.

## Source review map

- `app.py`: FastAPI entry point, legacy prediction/portfolio routes, and router registration.
- `signal_ledger.py`: sender/exporter/admin authentication, signal validation and ownership,
  create/edit/delete history, quote checks, and LIVE target CSV export.
- `paper_signals.py`: paper submitter/worker authentication and order-queue lifecycle.
- `tools/signal_keys.py`: generate, rotate, list, and revoke sender/exporter/admin keys.
- `tools/signal_client.py`: save an issued key and call the signal API.
- `tools/signal_list.py`: read all senders' latest signals as a table on the box.
- `tools/signal_stress.py`: bounded sustained create/delete test with cleanup and a JSON report.
- `tests/`: existing key-management and sender-client tests.

On November 4, 2026 (America/Chicago), the three service Python files above were
copied from the running JKoh service for review. Its dependency list matched this
checkout. On November 5, the deployed sender client was verified against the
previous committed version, then updated with the table/filter/export commands;
the new listing and stress-test helpers were also installed on JKoh. The existing
key-management tool was retained without a deployed-file comparison.
Private runtime configuration, credentials, key hashes, logs, and signal data were
not copied. This is source for review, not a configured local deployment.

### List signals from the terminal

From your computer, list signals owned by your saved sender key:

```bash
python3 services/prediction-api/tools/signal_client.py list --table --destination LIVE --status pending
```

Omit the filters to include other destinations/statuses. Omit `--table` for the
existing JSON output. To see everyone's pending LIVE signals on JKoh, use the
read-only ledger viewer (requires the SSH user's existing ledger read permission):

```bash
ssh sinnfund 'python3 /opt/prediction-api/tools/signal_list.py --destination LIVE'
```

The viewer defaults to pending. Use `--status all` to include deleted/exported
signals, or `--ledger /path/to/ledger.csv` to inspect a different ledger.

### Download the pending signals (any sender key)

`GET /signals/portfolio.csv` returns every sender's pending LIVE signals, one row each with weight 1/N
(`analyst,signal_id,symbol,target_price,sent_at,weight`; RICs shown as plain tickers). It is read-only:
no quotes are fetched and nothing is marked exported, so it is not limited by Twelve Data. Long or short is
left out because the export decides it from a fresh quote. Exporter-only keys are refused; use a sender or
admin key. See QUICKSTART step 4 for the command.

### Export a CSV on the box

An admin key can export using the same saved key used for sending:

```bash
python3 services/prediction-api/tools/signal_client.py export
```

An exporter-only key remains supported with `--key-file ~/.config/sinnfund/signal-exporter.key`.

This calls `POST /signals/export` and consumes **all pending LIVE signals across
all senders**. It does not accept a sender or signal selection. Duplicate tickers
or unavailable/stale quotes can block the export. The response reports the export
ID and row counts. The API saves the CSV on the box, not on the caller's computer:

```text
/opt/prediction-api/signal-data/exports/targets_<UTC time>_<export_id>.csv
```

The explicit key file overrides the saved sender/admin key for this command.
Exported signals cannot subsequently be deleted through the signal API.

Clear the pending LIVE queue without creating a CSV (admin only):

```bash
python3 services/prediction-api/tools/signal_client.py clear-live
```

Clear marks every pending LIVE signal as `cleared` with `cleared_by_admin` in the
audit ledger. It does not remove history or affect PAPER/final signals.

### Sustained test from your computer

```bash
python3 services/prediction-api/tools/signal_stress.py --seconds 60 --workers 4 --batch-size 100 --output signal-stress-results.json
```

Uses the sender client's saved credential and HTTPS base URL. Each worker creates
one batch, then deletes that batch before starting another. Test IDs have a unique
run prefix; cleanup only selects that prefix. The script never exports or submits
PAPER orders. It stops new cycles on errors or responses exceeding five seconds,
then verifies cleanup. It prints progress every ten seconds and saves timings,
errors, and cleanup status as JSON. Limits: 300 seconds, ten workers, 100 signals
per request. In-flight requests and cleanup may extend past the requested duration.
These are closed-loop measurements, not a guarantee of any offered arrival rate.

Deleted test signals remain in the audit ledger and appear in unfiltered listings.
An interrupted process or network failure may require cleanup using the report's
run prefix; never delete other senders' records or rewrite audit history to clean up.

### Storage optimization and abuse checks (November 5, 2026)

The CSV remains authoritative, with the same schema and complete audit history.
The API caches parsed signal state while holding the existing cross-process lock;
device/inode, size, modification time and change time invalidate that cache after
another writer changes the file. New events update cached state incrementally.
Writes copy the existing CSV bytes into a temporary file, append only new events,
fsync, and atomically replace the old file. Failed writes cannot leave partial
CSV rows or uncommitted records in the cache. Legacy-column ledgers are normalized
on the next write. The tradeoff is that each write still copies the CSV; multiple
API processes also cause more cache reloads. This is not a database migration.

Validation errors now return JSON-safe 422 responses without echoing input values,
including when clients submit non-finite numbers. Legacy `/prediction` submissions
now require positive finite prices, positive integer timestamps, valid researcher
and ticker strings, and no extra fields or coerced numeric strings. Valid numeric
JSON submissions retain their existing behavior.

`tests/test_signal_ledger.py` covers history preservation, cache invalidation,
cross-process changes, concurrent writes, write failures and CSV export.
`tests/test_api_abuse.py` starts a disposable HTTP service with synthetic prices
and tests ownership, credential scopes, malformed input and atomic bulk rejection.
Run the full suite with the service's dependencies installed:

```bash
python3 -m unittest discover -s services/prediction-api/tests
```

**Production location:** `/opt/prediction-api`

**systemd unit:** `prediction-api.service`

**HTTP bind:** `127.0.0.1:8003`

**Draft/test prefix:** `https://sinnfund.jkoh.dev/prediction/`

AWS is the production target. Set `SIGNAL_HOST` to its operator-managed SSH alias and
`SIGNAL_API_BASE_URL` to its published HTTPS `/prediction` prefix; do not send production signals
to jkoh. Until the AWS HTTPS prefix is published, the examples below deliberately default to the
jkoh draft/test environment.

For the shortest path, use the [five-minute signal guide](QUICKSTART.md). It starts with one
interactive key command and then shows send, list, inspect, edit, delete, PAPER, and automation.

## 1. Key scopes and storage

The API uses a separate random bearer key for each human identity. A caller-supplied name is never
accepted as proof of identity.

| Scope | Server hash file | Allowed operations |
|---|---|---|
| Sender | `/opt/prediction-api/signal-data/senders.htpasswd` | Create, read, edit, and delete owned signals |
| Exporter | `/opt/prediction-api/signal-data/exporters.htpasswd` | Consume pending LIVE signals into a target CSV |
| Admin | `/opt/prediction-api/signal-data/admins.htpasswd` | All sender operations, LIVE export, and clearing the pending LIVE queue |

The username in the hash file becomes the signal's audit identity. Names must match
`[A-Za-z0-9_.-]{1,64}`. The API reads the files on every request, so rotation and revocation take
effect without restarting the service.

The existing implementation accepts standard `htpasswd` `{SHA}` entries. These hashes are not
appropriate for human passwords. They are used here only with independently generated 256-bit
random keys. Treat the hash files as sensitive, keep them out of Git, and use mode `0600` locally
and `0640` with group `trading-dev` on the server.

## 2. Issue or rotate a sender key

The recommended command is interactive and can create or rotate one to 100 keys at once. When
already logged into JKoh, run it directly against the local key directory:

```bash
cd /opt/prediction-api
python3 tools/signal_keys.py create
```

Alternatively, run the repository version from an operator Mac; it connects to JKoh over SSH:

```bash
python3 services/prediction-api/tools/signal_keys.py create
```

Choose `sender`, enter how many keys, then enter each person's name. The command downloads the
current key file, generates independent 256-bit keys, installs the complete file atomically, and
prints the plaintext keys once. Use `list` or `revoke` for those operations:

```bash
python3 services/prediction-api/tools/signal_keys.py list
python3 services/prediction-api/tools/signal_keys.py revoke
```

The manual commands below document the underlying recovery procedure. They require macOS's existing
`/usr/sbin/htpasswd` and `openssl`.

Only one operator may change a scope's hash file at a time. Immediately before every issue,
rotation, or revocation, copy the current live file; never reuse a previously downloaded copy.
This prevents one operator from accidentally removing keys issued by another operator.

```bash
umask 077
mkdir -p data/private
SENDER_FILE=data/private/signal-senders.htpasswd
SIGNAL_HOST=${SIGNAL_HOST:-sinnfund}

status=0
ssh "$SIGNAL_HOST" 'test -f /opt/prediction-api/signal-data/senders.htpasswd' || status=$?
if [ "$status" -eq 0 ]; then
  scp "$SIGNAL_HOST":/opt/prediction-api/signal-data/senders.htpasswd "$SENDER_FILE" || exit $?
elif [ "$status" -eq 1 ]; then
  : > "$SENDER_FILE"
else
  printf 'Remote key-file check failed; nothing changed.\n' >&2
  exit "$status"
fi
chmod 600 "$SENDER_FILE"
```

An SSH/network/authentication failure is not the same as an absent file. The status check above
stops on those failures so an empty local file cannot accidentally replace every active key.

Issue a new key, or rotate an existing name by running the same command with that name:

```bash
printf 'Sender name: '
read -r SIGNAL_SENDER
printf '%s\n' "$SIGNAL_SENDER" | grep -Eq '^[A-Za-z0-9_.-]{1,64}$' || {
  printf 'Invalid sender name\n' >&2
  return 1 2>/dev/null || exit 1
}

SIGNAL_API_KEY=$(openssl rand -hex 32)
printf '%s\n' "$SIGNAL_API_KEY" |
  /usr/sbin/htpasswd -is "$SENDER_FILE" "$SIGNAL_SENDER"

printf 'Copy this key now through a private channel: %s\n' "$SIGNAL_API_KEY"
```

Install the complete hash file atomically:

```bash
scp "$SENDER_FILE" \
  "$SIGNAL_HOST":/opt/prediction-api/signal-data/.senders.htpasswd.upload

ssh "$SIGNAL_HOST" '
  set -eu
  path=/opt/prediction-api/signal-data/.senders.htpasswd.upload
  chgrp trading-dev "$path"
  chmod 640 "$path"
  mv "$path" /opt/prediction-api/signal-data/senders.htpasswd
'
```

The plaintext key is shown once and cannot be recovered from the hash file. After securely handing
it to its owner, remove it from the current shell:

```bash
unset SIGNAL_API_KEY
```

## 3. Issue an exporter or admin key

The interactive tool accepts `sender`, `exporter`, or `admin`. Choose `admin` when one identity
should send and export using the same key. Exporter-only keys remain supported for automation.

| Sender value | Exporter value |
|---|---|
| `SIGNAL_SENDER` | `SIGNAL_EXPORTER` |
| `signal-senders.htpasswd` | `signal-exporters.htpasswd` |
| `senders.htpasswd` | `exporters.htpasswd` |
| `.senders.htpasswd.upload` | `.exporters.htpasswd.upload` |

Each key belongs in one scope file; admin permissions are recognized without duplicating the key.

## 4. Verify a sender key without writing data

Read the key without adding it to shell history, then request an intentionally nonexistent signal:

```bash
read -rs SIGNAL_API_KEY
export SIGNAL_API_KEY
printf '\n'
API=${SIGNAL_API_BASE_URL:-https://sinnfund.jkoh.dev/prediction}

code=$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $SIGNAL_API_KEY" \
  "$API/signals/key-check-does-not-exist")

test "$code" = 404 && printf 'Sender key accepted\n' || {
  printf 'Unexpected HTTP status: %s\n' "$code" >&2
  exit 1
}
```

`404` is expected: authentication succeeded, but that signal is not owned by the caller. `401`
means the key is invalid or revoked; `503` means the sender hash file is not configured.

## 5. Create, read, edit, and delete a signal

The signal API intentionally uses only GET and POST. Replace the shell values below with the
intended signal; do not submit examples to production.

```bash
API=${SIGNAL_API_BASE_URL:-https://sinnfund.jkoh.dev/prediction}
SIGNAL_ID=YOUR_UNIQUE_SIGNAL_ID
SIGNAL_DESTINATION=LIVE
SIGNAL_TICKER=YOUR_TICKER
SIGNAL_PRICE=123.45
SIGNAL_TIMESTAMP=$(date +%s)
```

Create:

```bash
printf '{"signal_id":"%s","destination":"%s","ticker":"%s","price":%s,"timestamp":%s}\n' \
  "$SIGNAL_ID" "$SIGNAL_DESTINATION" "$SIGNAL_TICKER" \
  "$SIGNAL_PRICE" "$SIGNAL_TIMESTAMP" |
curl --fail-with-body -sS \
  -H "Authorization: Bearer $SIGNAL_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- "$API/signals" |
python3 -m json.tool
```

List compact summaries for every signal owned by this sender:

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer $SIGNAL_API_KEY" \
  "$API/signals" |
python3 -m json.tool
```

Read the current revision and complete audit history:

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer $SIGNAL_API_KEY" \
  "$API/signals/$SIGNAL_ID" |
python3 -m json.tool
```

Edit using the revision returned by GET:

```bash
CURRENT_REVISION=1
NEW_SIGNAL_PRICE=125.00

printf '{"signal_id":"%s","revision":%s,"price":%s}\n' \
  "$SIGNAL_ID" "$CURRENT_REVISION" "$NEW_SIGNAL_PRICE" |
curl --fail-with-body -sS \
  -H "Authorization: Bearer $SIGNAL_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- "$API/signals/edit" |
python3 -m json.tool
```

Delete using the latest revision returned by GET:

```bash
CURRENT_REVISION=2

printf '{"signal_id":"%s","revision":%s}\n' "$SIGNAL_ID" "$CURRENT_REVISION" |
curl --fail-with-body -sS \
  -H "Authorization: Bearer $SIGNAL_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- "$API/signals/delete" |
python3 -m json.tool
```

Delete is audited, not physical erasure. A final GET returns `status: "deleted"` and the complete
create/edit/delete history. Deleted signals are no longer pending or exportable. Create, edit, and
delete also accept arrays of up to 100 unique signals.

## 6. Automate with the bundled client

`tools/signal_client.py` is a standard-library client for create, list, GET by ID, edit, and delete.
It does not automate export because export consumes every pending LIVE signal and needs a
separately approved schedule.

First, an operator must issue the key using section 2. `save-key` does **not** create, issue, rotate,
or grant permissions to a key; it only stores that already-issued plaintext key for this operating-
system user. The prompt is hidden, and the client writes it to
`~/.config/sinnfund/signal-sender.key` with mode `0600`:

```bash
python3 services/prediction-api/tools/signal_client.py save-key
```

Normal use needs no environment variables. The create timestamp defaults to the current time;
edit and delete fetch the current revision themselves:

```bash
python3 services/prediction-api/tools/signal_client.py \
  create YOUR_SIGNAL_ID LIVE YOUR_TICKER 123.45
python3 services/prediction-api/tools/signal_client.py list
python3 services/prediction-api/tools/signal_client.py get YOUR_SIGNAL_ID
python3 services/prediction-api/tools/signal_client.py \
  edit YOUR_SIGNAL_ID --price 125.00
python3 services/prediction-api/tools/signal_client.py delete YOUR_SIGNAL_ID
```

### PAPER example

PAPER requires an explicit future forecast timestamp and entry limit. The API fetches a fresh
current quote, derives LONG/SHORT from current versus target, queues one paper share, and then makes
the signal immutable:

```bash
python3 services/prediction-api/tools/signal_client.py \
  create YOUR_SIGNAL_ID PAPER YOUR_TICKER 123.45 FUTURE_UNIX_TIME --limit-price 120.00
```

If the sender owns exactly one signal that is still deletable, no ID lookup is needed:

```bash
python3 services/prediction-api/tools/signal_client.py delete
```

The no-ID delete stops without changing anything when zero or multiple signals are deletable.
`delete-only` remains as a compatible alias.

### Automation example

For a scheduled job, run `save-key` once as the same operating-system user that will run the
job. Then save this example outside the repository as `send_signal.sh`:

```bash
#!/bin/sh
set -eu

CLIENT=/path/to/sinnfund-infra/services/prediction-api/tools/signal_client.py
case "${2-}:$#" in
  LIVE:4)
    exec python3 "$CLIENT" create "$1" LIVE "$3" "$4"
    ;;
  PAPER:6)
    exec python3 "$CLIENT" create "$1" PAPER "$3" "$4" "$5" --limit-price "$6"
    ;;
  *)
    printf 'usage: %s ID LIVE TICKER TARGET\n' "$0" >&2
    printf '   or: %s ID PAPER TICKER TARGET FUTURE_UNIX_TIME LIMIT\n' "$0" >&2
    exit 2
    ;;
esac
```

Example automation call:

```bash
./send_signal.sh "model-$(date +%s)" LIVE EXAMPLE 123.45
./send_signal.sh "paper-$(date +%s)" PAPER EXAMPLE 123.45 "$(($(date +%s)+86400))" 120.00
```

Use a made-up/public instrument when testing. The caller remains responsible for generating a
unique ID. Pass an explicit timestamp after the target price only when the forecast time differs
from the send time. To use another private key location, set `SIGNAL_API_KEY_FILE`; ephemeral jobs
may instead set `SIGNAL_API_KEY`. Never set both, put a key in command arguments, or commit a key.
Do not automatically retry a request under a new ID: a lost response may still mean the original
signal was accepted. Retry the same ID, which is idempotent, then GET it. Automated edits and
deletes must use the current revision; the client fetches it when the revision argument is omitted.
A concurrent change still returns `409` rather than deleting a newer revision.

Run the offline client tests with:

```bash
python3 -m unittest discover services/prediction-api/tests
```

## 7. Export pending LIVE signals

Export is a privileged, state-changing operation. It compares every pending LIVE target with one
batched quote request, writes `account,symbol,target`, and consumes the pending set only after the
CSV write succeeds. Do not use export to test a credential.

Every export makes a new no-cache quote request. The response must have been fetched within 30
seconds and its market timestamp must be within the approved 5,400-second after-close window. A
missing/stale quote fails the whole export and consumes nothing.

Tickers can be plain (`ZBH`) or LSEG US RICs (`ZBH.N`, `ZBRA.OQ`, `MSFT.O`): the ledger keeps what
the sender wrote, but quotes and the export CSV use the plain ticker, and `ZBH` and `ZBH.N` count as
duplicates. `.A` (NYSE American) is not stripped because it collides with share classes like `BF.A`.

**Known limit (2026-11-11):** the Twelve Data plan allows 8 symbols per minute and 800 a day, and an
export quotes every pending symbol in one request that must be fresh within 30 seconds. An export
with more than 8 pending LIVE symbols is therefore refused ("Current prices are unavailable")
until the quote source changes; see the journal entry for 2026-11-11.

```bash
read -rs SIGNAL_EXPORTER_KEY
export SIGNAL_EXPORTER_KEY
printf '\n'

curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $SIGNAL_EXPORTER_KEY" \
  "${SIGNAL_API_BASE_URL:-https://sinnfund.jkoh.dev/prediction}/signals/export" |
python3 -m json.tool

unset SIGNAL_EXPORTER_KEY
```

Run this only at the approved export time. A schedule and download endpoint are not documented as
deployed. Exact target/current equality is retained as an audited `no_direction` rejection.

## 8. List, revoke, and recover keys

Coordinate with other key operators, then rerun the download block in section 2 immediately before
listing or revoking a sender. Use the exporter filenames from section 3 for an exporter.

List identities without displaying hashes:

```bash
cut -d: -f1 data/private/signal-senders.htpasswd
```

Revoke an identity locally, then repeat the atomic install command from section 2:

```bash
/usr/sbin/htpasswd -D data/private/signal-senders.htpasswd SENDER_NAME
```

Revocation takes effect as soon as the new hash file is renamed into place. Existing signals and
their audit owner remain unchanged. A lost key cannot be recovered; rotate the same name to a new
random key. A deleted username may be reissued later, but that identity will continue to match its
existing audit records.

## 9. Troubleshooting

| Symptom | Meaning / action |
|---|---|
| `No saved signal API key` | Obtain an issued sender key, then run `signal_client.py save-key` once as the current operating-system user |
| `Set only one of SIGNAL_API_KEY or SIGNAL_API_KEY_FILE` | Both overrides are set. Unset one, or unset both to use the saved default key |
| Older client says `Set exactly one of SIGNAL_API_KEY or SIGNAL_API_KEY_FILE` | Update the client and run `save-key`; the current client uses a private saved key by default |
| `401 Invalid signal API credential` | Wrong, revoked, or wrong-scope key; confirm the intended username exists in the correct local hash file and reinstall it |
| `404 signal not found` | Authentication succeeded, but the ID is absent or belongs to another sender |
| `409 signal revision changed` | GET the signal and retry only with the returned current revision |
| `409 only pending or no_direction signals can be edited/deleted` | The signal is already exported, deleted, or otherwise final |
| `503 ...htpasswd is not configured` | Install the corresponding sender/exporter hash file |
| `htpasswd: cannot modify file ... use -c` | After confirming the remote file truly does not exist, create the empty local file with `: > "$SENDER_FILE"`; do not use `-c` against an existing multi-user file |
| Key works locally but not after install | Verify remote group/mode and that the upload was atomically renamed to the exact active filename |

Check service state without exposing credentials:

```bash
ssh "${SIGNAL_HOST:-sinnfund}" 'systemctl is-active prediction-api.service'
curl -fsS "${SIGNAL_API_BASE_URL:-https://sinnfund.jkoh.dev/prediction}/openapi.json" >/dev/null
```

Never print server hash contents, bearer keys, runtime configuration, account identifiers, or
signal data into tickets, pull requests, logs, or chat.
