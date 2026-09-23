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

`test_live.py` drops a test call for tomorrow, so only ever run it against a local site on disk
storage, never against the hosted one.

## Settings

Set these in `.env` locally and in the Vercel project settings. **Never commit them.**

- `BOARD_PINS` JSON of `{"Player": "pin"}`. Hand each person only their own.
- `BOARD_HUB_TOKEN` long and random; the same value goes on the posting machine.
- `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` the hosted store. With both unset the
  site keeps its files on disk instead.
- `BOARD_DATA` the folder for those files, used only on disk storage.

## Adding a player

Add them to `config/players.json`. Someone who did not play round one gets
`"round1": null, "impute_round1": true` and carries the field average of the round-one scores,
so they can neither lead on one week alone nor be punished for arriving late. Then give them a
PIN in `BOARD_PINS`.

## Hosting on Vercel

- Framework preset **FastAPI**. Vercel finds the app in `app.py` and sends every address to it.
  There is no `vercel.json`: a rewrite there changes the path the app sees and every page
  answers "not found".
- Storage is an **Upstash Redis** database on the free tier, which cannot charge. Not Vercel
  Blob, because Blob serves every file from a public address and that would break the seal.
  `board/store.py` is the only file that touches storage.
- **Deployment Protection off**, or players hit a Vercel sign-in page instead of the board.
