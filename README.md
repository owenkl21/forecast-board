# Forecast Championship

The competition board. Each player drops their own call; the site scores every closed day and
shows the standings, day by day results and every branch side by side.

## Two pages

- **`/`** the results board
- **`/submit`** drop your call: pick your name, enter your PIN, drag in a CSV or Excel workbook

## How it stays fair

- **Sealed until everyone is in.** No call for a day is shown until every player's has landed.
  There is no time cut-off: a call counts any time before the day it predicts, so revealing a
  partial set at any hour would let a late submitter read the board first.
- **Locked once the day starts.** A call can be replaced as often as you like until midnight
  before its day, never after.
- **Nothing leaks.** After dropping you see your own receipt only. No address on the site
  returns anyone's raw call.
- **PIN per player**, so nobody can overwrite someone else's file.

## Where the numbers come from

The site never reads the live database and contains no forecasting model. A separate machine
posts it three things, behind a shared token:

| post | when | what |
|---|---|---|
| `POST /api/hub/call` | 17:30, then hourly until midnight | the owner's call for tomorrow |
| `POST /api/hub/live` | hourly | today's running count per branch, and the day's shape |
| `POST /api/hub/actuals` | 19:05 | a closed day's counts per branch, branch sizes and weather |

Each is JSON with a `date` in `YYYY-MM-DD`. Bearer token in the `Authorization` header.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set real PINs and a long random hub token
./run_local.sh              # http://localhost:8000
```

Tests, with the site running:

```bash
.venv/bin/python tests/test_live.py
```

## Settings

Set these in `.env` locally and in the Vercel project settings. **Never commit them.**

- `BOARD_PINS` JSON of `{"Player": "pin"}`. Hand each person only their own.
- `BOARD_HUB_TOKEN` long and random; the same value goes on the posting machine.
- `BOARD_DATA` local folder for files. Ignored on Vercel.

## Adding a player

Add them to `config/players.json`. Someone who did not play round one gets
`"round1": null, "impute_round1": true` and carries the field average of the round-one scores,
so they can neither lead on one week alone nor be punished for arriving late. Then give them a
PIN in `BOARD_PINS`.

## Before deploying to Vercel

**One piece is not done yet: hosted storage.** Locally every file is kept on disk. Vercel's
disk is wiped between requests, so uploads would vanish. `board/store.py` is the only file
that touches storage and was written to be swapped; it needs a Vercel Blob backend, which has
to be built and tested against a real Vercel project and token. It is deliberately not shipped
untested.
