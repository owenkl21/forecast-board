"""Where the board keeps what it knows.

Everything the board needs arrives from one of two places: a player dropping their call on
the website, or the hub posting the day's actual counts. Nothing here ever reads the live
database. That is deliberate: the website is meant to be hosted, and a hosted site must not
hold the key that reads production.

This is the only module that touches storage. Two backends, chosen automatically:

  - **disk**, used locally, and whenever no Redis settings are present
  - **redis**, used on Vercel, where the disk is wiped between requests

Redis, not a blob store, because blob stores serve every file from a public web address.
That would break the seal: anyone holding the address could read a call before everyone is
in. A key-value store is private and a call is only a few kilobytes.

Keys, identical in meaning across both backends:

    call:<target-date>:<Player>   one per player per day, their latest call
    actuals:<date>                the hub's bundle for a closed day
    live                          the hub's running count for today
    state                         the scores, written only by the scoring step
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Vercel's Upstash integration names these either way depending on how it was connected.
_REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or ""
_REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
BACKEND = "redis" if (_REDIS_URL and _REDIS_TOKEN) else "disk"

# On Vercel the project folder is read-only; only /tmp can be written, and it does not last
# between requests. Locally, keep files in ./data.
_DEFAULT_DATA = "/tmp/board-data" if os.environ.get("VERCEL") else str(ROOT / "data")
DATA = Path(os.environ.get("BOARD_DATA") or _DEFAULT_DATA)
INBOX = DATA / "inbox"
ACTUALS = DATA / "actuals"
LIVE = DATA / "live.json"
STATE = DATA / "state.json"


# ================================================================== disk
def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file and a rename, so a crash mid-write never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ================================================================== redis
def _r(*cmd: str):
    """One Upstash REST command. Raises on any failure: a storage fault must be loud, never
    a quiet empty result that makes the board look like nobody has called."""
    body = json.dumps(list(cmd)).encode()
    req = urllib.request.Request(_REDIS_URL.rstrip("/"), data=body, method="POST",
                                 headers={"Authorization": f"Bearer {_REDIS_TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        out = json.loads(resp.read())
    if "error" in out:
        raise RuntimeError(f"redis {cmd[0]} failed: {out['error']}")
    return out.get("result")


def _keys(prefix: str) -> list[str]:
    """Every key under a prefix, via SCAN so it never blocks the store. The board holds a few
    dozen keys at most, so this is cheap."""
    out, cursor = [], "0"
    while True:
        cursor, batch = _r("SCAN", cursor, "MATCH", f"{prefix}*", "COUNT", "200")
        out += batch
        if str(cursor) == "0":
            return sorted(set(out))


# The scorer reads calls from files. On Redis, each request writes the day's calls to its own
# temp folder so the scoring code stays identical on both backends. Vercel allows writing to
# /tmp within a request, and nothing there needs to outlive it.
_TMP = Path(tempfile.gettempdir()) / "board-calls"


# ================================================================== the interface
def save_call(day: dt.date, player: str, csv_text: str) -> None:
    if BACKEND == "redis":
        _r("SET", f"call:{day.isoformat()}:{player}", csv_text)
    else:
        _atomic_write(INBOX / day.isoformat() / f"{player}.csv", csv_text)


def call_paths(day: dt.date) -> dict[str, Path]:
    if BACKEND == "disk":
        folder = INBOX / day.isoformat()
        return {p.stem: p for p in sorted(folder.glob("*.csv"))} if folder.exists() else {}
    prefix = f"call:{day.isoformat()}:"
    out = {}
    folder = _TMP / day.isoformat()
    for key in _keys(prefix):
        text = _r("GET", key)
        if text is None:
            continue
        player = key[len(prefix):]
        p = folder / f"{player}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        out[player] = p
    return out


def save_actuals(day: dt.date, bundle: dict) -> None:
    text = json.dumps(bundle, indent=2, sort_keys=True)
    if BACKEND == "redis":
        _r("SET", f"actuals:{day.isoformat()}", text)
    else:
        _atomic_write(ACTUALS / f"{day.isoformat()}.json", text)


def load_actuals(day: dt.date) -> dict | None:
    if BACKEND == "redis":
        t = _r("GET", f"actuals:{day.isoformat()}")
        return json.loads(t) if t else None
    p = ACTUALS / f"{day.isoformat()}.json"
    return json.loads(p.read_text()) if p.exists() else None


def actuals_days() -> list[str]:
    """Every date the hub has posted a closed day for, oldest first."""
    if BACKEND == "redis":
        return sorted(k.split(":", 1)[1] for k in _keys("actuals:"))
    return sorted(p.stem for p in ACTUALS.glob("*.json")) if ACTUALS.exists() else []


def latest_actuals() -> dict | None:
    days = actuals_days()
    return load_actuals(dt.date.fromisoformat(days[-1])) if days else None


def save_live(bundle: dict) -> None:
    text = json.dumps(bundle, indent=2, sort_keys=True)
    if BACKEND == "redis":
        _r("SET", "live", text)
    else:
        _atomic_write(LIVE, text)


def load_live() -> dict | None:
    if BACKEND == "redis":
        t = _r("GET", "live")
        return json.loads(t) if t else None
    return json.loads(LIVE.read_text()) if LIVE.exists() else None


def load_state() -> dict:
    if BACKEND == "redis":
        t = _r("GET", "state")
        return json.loads(t) if t else {"days": {}}
    return json.loads(STATE.read_text()) if STATE.exists() else {"days": {}}


def save_state(s: dict) -> None:
    text = json.dumps(s, indent=2, sort_keys=True)
    if BACKEND == "redis":
        _r("SET", "state", text)
    else:
        _atomic_write(STATE, text)
