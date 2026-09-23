"""Where the board keeps what it knows.

Everything the board needs arrives from one of two places: a player dropping their call on
the website, or the hub posting the day's actual counts. Nothing here ever reads the live
database. That is deliberate: the website is meant to be hosted, and a hosted site must not
hold the key that reads production.

This module is the only thing that touches storage, so moving from local disk to hosted
storage later means rewriting these few functions and nothing else. The layout on disk:

    data/inbox/<target-date>/<Player>.csv   one file per player per day, their latest call
    data/actuals/<date>.json                the hub's bundle for a closed day
    data/live.json                          the hub's running count for today
    data/state.json                         the scores, written only by the scoring step
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BOARD_DATA", str(ROOT / "data")))
INBOX = DATA / "inbox"
ACTUALS = DATA / "actuals"
LIVE = DATA / "live.json"
STATE = DATA / "state.json"


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file and a rename, so a crash mid-write never leaves half a file.
    Two players dropping at the same moment each write their own file, and the scores are
    only ever written by the scoring step, so this is enough without a lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- calls
def save_call(day: dt.date, player: str, csv_text: str) -> Path:
    p = INBOX / day.isoformat() / f"{player}.csv"
    _atomic_write(p, csv_text)
    return p


def call_paths(day: dt.date) -> dict[str, Path]:
    folder = INBOX / day.isoformat()
    if not folder.exists():
        return {}
    return {p.stem: p for p in sorted(folder.glob("*.csv"))}


def call_days() -> list[str]:
    return sorted(p.name for p in INBOX.glob("2026-*") if p.is_dir()) if INBOX.exists() else []


# ---------------------------------------------------------------- the hub's data
def save_actuals(day: dt.date, bundle: dict) -> None:
    _atomic_write(ACTUALS / f"{day.isoformat()}.json", json.dumps(bundle, indent=2, sort_keys=True))


def load_actuals(day: dt.date) -> dict | None:
    p = ACTUALS / f"{day.isoformat()}.json"
    return json.loads(p.read_text()) if p.exists() else None


def latest_actuals() -> dict | None:
    files = sorted(ACTUALS.glob("*.json")) if ACTUALS.exists() else []
    return json.loads(files[-1].read_text()) if files else None


def save_live(bundle: dict) -> None:
    _atomic_write(LIVE, json.dumps(bundle, indent=2, sort_keys=True))


def load_live() -> dict | None:
    return json.loads(LIVE.read_text()) if LIVE.exists() else None


# ---------------------------------------------------------------- scores
def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"days": {}}


def save_state(s: dict) -> None:
    _atomic_write(STATE, json.dumps(s, indent=2, sort_keys=True))
