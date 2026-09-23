"""The competition board: scoring and rendering. Safe to publish.

This is the board half of what used to be one program. The other half, the forecast engine,
stays on the hub and never comes here. Everything in this package works only from:

  - the calls players drop on the website, and
  - the numbers the hub posts after each day closes and once an hour through the day.

It never reads the live database. The hub is the only thing that does, so a hosted copy of
this package holds no credential for production data and contains nothing of the engine.

Scoring is the competition's own: per-branch WAPE, every miss in cars added up, divided by the
cars actually washed, over the 44 scored branches. A branch that did not trade scores as
actual 0. Ground truth is the work-order count by check-in date, South African time.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd

from . import calendar_sa as CAL
from . import store

ROOT = store.ROOT
CONFIG = ROOT / "config"
PLAYERS_FILE = CONFIG / "players.json"


def _lines(p: Path) -> list[str]:
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


CARD = _lines(CONFIG / "card.txt")                     # the 46 branches in the competition
SCORED_EXCLUDE = set(_lines(CONFIG / "excluded.txt"))  # submitted but not scored

WEEK = (dt.date(2026, 9, 21), dt.date(2026, 9, 27))

ROUND1_WEIGHT, WEEK_WEIGHT = 0.35, 0.65
# Round 1 FINAL standings, all seven days 11-17 Sep, as published on the competition board
# (screenshot from Owen, 19 Sep). The champion column is the board's own 20% fixed + 80%
# daily blend. An earlier version of this file carried the SIX-day figures by mistake, which
# had William leading; over the full week Tristan won it.

def players() -> dict[str, dict]:
    """The roster, from config so a new player joins by being added to a file.

    A player who did not take part in round one has round1 = null. With impute_round1 they
    carry the FIELD AVERAGE of everyone who did play it, computed here so it can never drift
    out of step with the roster. Owen's ruling on 21 Sep, and the reasoning matters: scored
    on the week alone, a new player joining at round two would top the championship off a
    single week, which is not a real result. Treating them as an average round-one performer
    is the version of the rule you can say out loud. Without the flag they are scored on the
    week alone and the board marks them, rather than quietly ranking them against people
    carrying a round-one score."""
    d = json.loads(PLAYERS_FILE.read_text())
    ps = {k: dict(v) for k, v in d.items() if not k.startswith("_")}
    played = [v["round1"] for v in ps.values() if v.get("round1") is not None]
    if played:
        avg = round(sum(played) / len(played), 2)
        for v in ps.values():
            if v.get("round1") is None and v.get("impute_round1"):
                v["round1"], v["imputed"] = avg, True
    return ps


# Round 1 FINAL scores, all seven days 11-17 Sep, as published on the official board.
# ROUND1 keeps its old name and shape so nothing downstream had to change; a player with no
# round-one result maps to None here rather than being left out of the roster.

# Round 1 FINAL scores, all seven days 11-17 Sep, as published on the official board.
# ROUND1 keeps its old name and shape so nothing downstream had to change; a player with no
# round-one result maps to None here rather than being left out of the roster.
ROUND1 = {k: v.get("round1") for k, v in players().items()}

ROUND1_DETAIL = {k: tuple(v["detail"]) for k, v in players().items() if v.get("detail")}
# players whose round-one score is the imputed field average, not one they posted

# players whose round-one score is the imputed field average, not one they posted
IMPUTED = {k for k, v in players().items() if v.get("imputed")}

BRANCH_COLS = ("branch", "site", "name", "store")

CARS_COLS = ("predicted_cars", "cars", "prediction", "predicted", "forecast", "value")
# Words that mean "this is the number of cars" when the header is not an exact match, and
# words that rule a column out however car-ish it looks. William's first file arrived with
# `cars_forecast`, which matched nothing and would have scored him as a no-show for the day.

# Words that mean "this is the number of cars" when the header is not an exact match, and
# words that rule a column out however car-ish it looks. William's first file arrived with
# `cars_forecast`, which matched nothing and would have scored him as a no-show for the day.
CARS_HINTS = ("car", "forecast", "predict", "expected", "volume", "count")

NOT_CARS = ("rain", "mm", "pct", "%", "prob", "chance", "staff", "wait", "temp", "wind",
            "date", "day", "model", "note", "comment")

def read_call(path: Path) -> dict[str, float]:
    """Read one bot's file into {branch: cars}, tolerating layout differences."""
    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}
    bcol = next((cols[c] for c in BRANCH_COLS if c in cols), None)
    ccol = next((cols[c] for c in CARS_COLS if c in cols), None)
    if bcol is None:  # any text column whose name hints at a place
        bcol = next((v for k, v in cols.items()
                     if any(h in k for h in BRANCH_COLS)), None)
    if ccol is None:
        # no exact header match, so fall back to a hint, then to the only numeric column
        cand = [v for k, v in cols.items()
                if any(h in k for h in CARS_HINTS) and not any(n in k for n in NOT_CARS)]
        if not cand:
            cand = [c for c in df.columns
                    if c != bcol and pd.api.types.is_numeric_dtype(df[c])
                    and not any(n in str(c).lower() for n in NOT_CARS)]
        ccol = cand[0] if len(cand) == 1 else (cand[0] if cand else None)
        if ccol:
            print(f"    {path.name}: no standard cars column, using '{ccol}'", flush=True)
    if bcol is None or ccol is None:
        raise ValueError(f"{path.name}: need a branch column {BRANCH_COLS} and a cars column "
                         f"{CARS_COLS}; found {list(df.columns)}")
    df = df[[bcol, ccol]].dropna()
    df[bcol] = df[bcol].astype(str).str.strip()
    df = df[~df[bcol].str.upper().isin(
        {"TOTAL", "TOTALS", "GRAND TOTAL", "ALL SITES", "ALL BRANCHES", "SUM", "NETWORK"})]
    df[ccol] = pd.to_numeric(df[ccol], errors="coerce")
    return {b: float(v) for b, v in zip(df[bcol], df[ccol]) if pd.notna(v)}

# ------------------------------------------------------------------- live, in-day
SAST = dt.timezone(dt.timedelta(hours=2))

# How wrong the in-day projection is, measured over the last 70 days at every half hour from
# 07:00 to 17:30 (n=1388), bucketed by how much of the day was already in:
#     <15%  median 21.6%  p90 63.9%      50-65%  median 5.3%  p90 16.2%
#    15-25% median 14.4%  p90 39.1%      65-80%  median 3.5%  p90 11.3%
#    25-35% median  9.3%  p90 28.5%       >80%   median 0.5%  p90  4.0%
#    35-50% median  7.6%  p90 24.4%
# So the projection is worth printing from about half the day on, and not before. The early
# reading runs high (+17% mean under 15%), and a per-hour correction for that was fitted and
# rejected: on a first-half/second-half split it moved the out-of-sample bias from +1.8% to
# +5.7% and the median error from 3.6% to 4.5%. It is period-specific, not a real effect.

# How wrong the in-day projection is, measured over the last 70 days at every half hour from
# 07:00 to 17:30 (n=1388), bucketed by how much of the day was already in:
#     <15%  median 21.6%  p90 63.9%      50-65%  median 5.3%  p90 16.2%
#    15-25% median 14.4%  p90 39.1%      65-80%  median 3.5%  p90 11.3%
#    25-35% median  9.3%  p90 28.5%       >80%   median 0.5%  p90  4.0%
#    35-50% median  7.6%  p90 24.4%
# So the projection is worth printing from about half the day on, and not before. The early
# reading runs high (+17% mean under 15%), and a per-hour correction for that was fitted and
# rejected: on a first-half/second-half split it moved the out-of-sample bias from +1.8% to
# +5.7% and the median error from 3.6% to 4.5%. It is period-specific, not a real effect.
MIN_SHARE = 0.50

BAND = ((0.65, 16), (0.80, 11), (1.01, 4))   # share below x -> plus or minus y percent

def _band(share: float) -> int:
    return next(pct for lim, pct in BAND if share < lim)

def _share_at(curve: pd.Series, now: dt.datetime) -> float:
    """Share of the day done by this exact minute. Interpolated inside the current hour,
    because the cumulative share at hour H counts a whole hour that has not finished yet."""
    if not len(curve):
        return 0.0
    h = now.hour
    prev = float(curve.get(h - 1, 0.0)) if h > 0 else 0.0
    cur = float(curve.get(h, prev))
    return prev + (now.minute / 60.0) * max(cur - prev, 0.0)

BIG_SHARE = 0.60   # "big" = the fewest branches that together carry this much of an ordinary day

def _sub(call, act, bs: set[str]) -> dict:
    """WAPE and bias over one subset of branches."""
    err = sum(abs(call.get(b, 0.0) - act.get(b, 0.0)) for b in bs)
    tot = sum(act.get(b, 0.0) for b in bs)
    got = sum(call.get(b, 0.0) for b in bs)
    return {"wape": round(err / tot * 100, 2) if tot else None,
            "bias": round((got / tot - 1) * 100, 2) if tot else None,
            "actual": round(tot), "n": len(bs)}

def score(call: dict[str, float], act: dict[str, float], branches: set[str],
          sizes: dict[str, float] | None = None) -> dict:
    """Per-branch WAPE over the scored set. A branch absent from the call scores as 0 called
    (a full miss), which is what the board does, and is reported so it cannot hide.

    Also keeps the signed miss per branch, so the board can show where a bot's points are
    actually going rather than only that it lost them."""
    err = sum(abs(call.get(b, 0.0) - act.get(b, 0.0)) for b in branches)
    tot = sum(act.get(b, 0.0) for b in branches)
    called = sum(call.get(b, 0.0) for b in branches)
    missing = sorted(b for b in branches if b not in call)
    # Anything a bot sends that is not on the scored card is dropped, never scored. The
    # competition grades 44 branches; a bot adding De Drift, Newinbosch, a branch we do not
    # trade, or a typo'd name must not be able to move anyone's WAPE by doing so. Reported
    # so it is visible rather than silently swallowed.
    extra = sorted(set(call) - branches)
    out = {"wape": round(err / tot * 100, 2) if tot else None,
           "bias": round((called / tot - 1) * 100, 2) if tot else None,
           "called": round(called), "actual": round(tot), "missing_branches": missing,
           "extra_branches": extra,
           # the call itself, per branch. Everything else the board shows about a bot can be
           # derived from this and the day's actuals, so store the source, not the summaries.
           "branch_call": {b: round(call.get(b, 0.0)) for b in branches}}
    if sizes:
        out["big"] = _sub(call, act, big_set(branches, sizes))
        out["tail"] = _sub(call, act, branches - big_set(branches, sizes))
    return out

def big_set(branches: set[str], sizes: dict[str, float]) -> set[str]:
    """The fewest branches that together carry BIG_SHARE of an ordinary day.

    A flat cars-per-day threshold does not work here: a median taken over eight weeks is
    dragged down by quiet weekdays, so a cutoff that looks right against a Saturday picks
    almost nothing. Ranking by typical size and cutting at a share of the network is stable
    whatever the weekday, and it makes the panel's own caption true by construction."""
    ranked = sorted(branches, key=lambda b: -sizes.get(b, 0.0))
    total = sum(sizes.get(b, 0.0) for b in branches)
    if total <= 0:
        return set()
    big, run = set(), 0.0
    for b in ranked:
        big.add(b)
        run += sizes.get(b, 0.0)
        if run >= BIG_SHARE * total:
            break
    return big

def standings(state: dict) -> pd.DataFrame:
    """Championship = 35% round 1 + 65% the mean of this week's daily WAPE."""
    days = {d: v for d, v in state["days"].items()
            if WEEK[0].isoformat() <= d <= WEEK[1].isoformat()}
    rows = []
    for bot, r1 in ROUND1.items():
        ws = [v["bots"][bot]["wape"] for v in days.values()
              if bot in v["bots"] and v["bots"][bot].get("wape") is not None]
        bs = [v["bots"][bot]["bias"] for v in days.values()
              if bot in v["bots"] and v["bots"][bot].get("bias") is not None]
        misses = sum(1 for v in days.values() if bot in v.get("no_submission", []))
        week = sum(ws) / len(ws) if ws else None
        # A player with no round-one result is scored on this week alone. Blending them
        # against a round-one score they never posted would be inventing a number, and
        # dropping them off the board would be worse. They are ranked on the week and
        # flagged, so the column is honest about being a different quantity.
        if week is None:
            champ = None
        elif r1 is None:
            champ = week
        else:
            champ = ROUND1_WEIGHT * r1 + WEEK_WEIGHT * week
        rows.append({"bot": bot, "round1": r1, "week": round(week, 2) if week else None,
                     "days_scored": len(ws), "missed": misses,
                     "week_only": r1 is None, "imputed": bot in IMPUTED,
                     "bias": round(sum(bs) / len(bs), 1) if bs else None,
                     "champion": round(champ, 2) if champ else None})
    df = pd.DataFrame(rows)
    return df.sort_values("champion", na_position="last").reset_index(drop=True)

TEMPLATE = """<title>Forecast Championship</title>
<!-- A published page is static once loaded. The hub re-renders hourly and the link is
     republished a quarter past, but a tab left open all morning keeps showing whatever it
     loaded, which reads as "it never updates". Reload every 10 minutes so an open board is
     never more than one refresh behind the link, and never stale on somebody's second
     screen. Ten rather than sixty because people leave this up during the day. -->
<meta http-equiv="refresh" content="600">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap">
<style>
:root{
  --ink0:#141433; --ink1:#1B1B46; --ink2:#202050; --mute:#8585AB;
  --red:#DC2626; --red-lo:#EF4444; --green:#22C55E; --amber:#F59E0B;
  --bg:#F4F4F8; --card:#FFFFFF; --line:#E2E2EC; --text:#141433; --text2:#4A4A6E; --text3:#8585AB;
  --hero-bg:linear-gradient(135deg,#1B1B46 0%,#141433 55%,#20204F 100%);
  --row-lead:rgba(34,197,94,.10);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0A0A16; --card:#141433; --line:#262653; --text:#EDEDF6; --text2:#B4B4D0; --text3:#8585AB;
  --row-lead:rgba(34,197,94,.13);
}}
:root[data-theme="dark"]{
  --bg:#0A0A16; --card:#141433; --line:#262653; --text:#EDEDF6; --text2:#B4B4D0; --text3:#8585AB;
  --row-lead:rgba(34,197,94,.13);
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);margin:0;font-family:Archivo,system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;padding-block:0 72px;padding-inline:0}
.w{max-width:980px;margin:0 auto;padding-inline:20px}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}

/* ---- hero ---- */
.hero{background:var(--hero-bg);color:#fff;padding-block:44px 38px;margin-bottom:34px;
  position:relative;overflow:hidden}
.hero:after{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(900px 260px at 82% -30%,rgba(220,38,38,.28),transparent 70%)}
.hero .w{position:relative;z-index:1}
.ribbon{display:flex;gap:5px;margin-bottom:18px}
.ribbon i{display:block;height:5px;border-radius:3px;background:#fff;opacity:.9;
  animation:bar .7s cubic-bezier(.2,.8,.2,1) both}
.ribbon i:nth-child(1){width:34px;animation-delay:.05s}
.ribbon i:nth-child(2){width:18px;background:var(--red);opacity:1;animation-delay:.14s}
.ribbon i:nth-child(3){width:10px;animation-delay:.23s}
@keyframes bar{from{transform:scaleX(0);transform-origin:left}to{transform:scaleX(1)}}
h1{font-size:clamp(1.9rem,5.2vw,3rem);font-weight:800;letter-spacing:-.032em;line-height:1.02;
  margin:0 0 .45rem;text-wrap:balance}
h1 span{color:var(--red-lo)}
.dek{color:#C9C9E4;font-size:1rem;line-height:1.55;max-width:60ch;margin:0 0 1.5rem}
.dek b{color:#fff;font-weight:600}
.meta{display:flex;flex-wrap:wrap;gap:10px 22px;font-size:.76rem;letter-spacing:.02em;color:#A9A9CE}
.meta b{color:#EDEDF6;font-weight:600}
.live{display:inline-flex;align-items:center;gap:7px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);
  box-shadow:0 0 0 0 rgba(34,197,94,.7);animation:pulse 2.4s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}70%{box-shadow:0 0 0 9px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}

h2{font-size:.74rem;font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:var(--text3);
  margin:2.6rem 0 .85rem;display:flex;align-items:center;gap:11px}
h2:after{content:"";flex:1;height:1px;background:var(--line)}

/* ---- standings ---- */
.board{display:flex;flex-direction:column;gap:9px}
.card{background:var(--card);border:1px solid var(--line);border-radius:13px;
  display:grid;grid-template-columns:52px 1fr auto;align-items:center;gap:18px;
  padding:17px 20px;position:relative;overflow:hidden;
  animation:rise .55s cubic-bezier(.2,.8,.2,1) both}
@keyframes rise{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
.card.lead{border-color:rgba(34,197,94,.45);background:var(--row-lead)}
.card.lead:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--green)}
.pos{font-family:"IBM Plex Mono",monospace;font-size:1.5rem;font-weight:700;color:var(--text3);
  text-align:center;line-height:1}
.card.lead .pos{color:var(--green)}
.nm{font-size:1.12rem;font-weight:700;letter-spacing:-.012em;margin-bottom:7px}
.tag{font-size:.66rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  padding:.2em .5em;border-radius:4px;margin-left:.5em;vertical-align:2px;
  background:rgba(245,158,11,.15);color:var(--amber)}
.track{height:7px;border-radius:4px;background:var(--line);overflow:hidden;max-width:340px}
.fill{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--ink2),var(--red));
  animation:grow .9s .25s cubic-bezier(.2,.8,.2,1) both}
.card.lead .fill{background:linear-gradient(90deg,#15803D,var(--green))}
.tag.new{background:rgba(59,130,246,.15);color:#3B82F6}
@keyframes grow{from{width:0}}
.sub{font-size:.78rem;color:var(--text3);margin-top:7px}
.sub b{color:var(--text2);font-weight:600}
.score{text-align:right}
.score .v{font-family:"IBM Plex Mono",monospace;font-size:1.75rem;font-weight:700;letter-spacing:-.03em;
  line-height:1;display:block}
.score .v small{font-size:.58em;font-weight:600;opacity:.55;margin-left:1px}
.score .l{font-size:.64rem;font-weight:700;letter-spacing:.11em;text-transform:uppercase;
  color:var(--text3);margin-top:5px}
.pending{color:var(--text3);font-size:.9rem;font-weight:600;font-family:Archivo,sans-serif}

/* ---- grid ---- */
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:13px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{text-align:left;padding:13px 15px;border-bottom:1px solid var(--line);white-space:nowrap}
thead th{font-size:.66rem;letter-spacing:.09em;text-transform:uppercase;color:var(--text3);font-weight:700}
tbody tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
td.who{font-weight:700}
.win{color:var(--green);font-weight:700}
.dim{color:var(--text3)}
.bad{color:var(--red-lo)}
tr.tot td{background:rgba(133,133,171,.07);font-size:.82rem}
.cap{color:var(--text3);font-size:.78rem;line-height:1.6;margin:.9rem 0 0;max-width:62ch}

/* ---- live in-day panel ---- */
.livewrap{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:19px 21px;
  position:relative;overflow:hidden}
.livewrap:before{content:"";position:absolute;left:0;right:0;top:0;height:2px;
  background:linear-gradient(90deg,var(--ink2),var(--red),var(--ink2));background-size:200% 100%;
  animation:sweep 3.2s linear infinite}
@keyframes sweep{to{background-position:200% 0}}
.nums{display:flex;flex-wrap:wrap;gap:26px 44px;margin-bottom:4px}
.big{display:block;font-family:"IBM Plex Mono",monospace;font-size:2rem;font-weight:700;
  letter-spacing:-.03em;line-height:1}
.big small{font-size:.5em;font-weight:600;opacity:.5;margin-left:2px}
.lbl{font-size:.64rem;font-weight:700;letter-spacing:.11em;text-transform:uppercase;
  color:var(--text3);margin-top:6px;display:block}
.prog{height:6px;border-radius:4px;background:var(--line);overflow:hidden;margin:16px 0 7px}
.prog i{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,var(--ink2),var(--red));
  animation:grow 1s .2s cubic-bezier(.2,.8,.2,1) both}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:15px}
.chip{border:1px solid var(--line);border-radius:9px;padding:9px 13px;font-size:.82rem;
  display:flex;align-items:baseline;gap:9px;animation:rise .5s both}
.chip b{font-weight:700}
.chip .g{font-family:"IBM Plex Mono",monospace;font-weight:600}
.chip.near{border-color:rgba(34,197,94,.5)}
.chip.near .g{color:var(--green)}
.chip.far .g{color:var(--red-lo)}
.chip.none{color:var(--text3)}

/* ---- analytics panels ---- */
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:18px 20px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:11px}
.mini{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 17px;
  animation:rise .5s both}
.mini h3{font-size:.95rem;font-weight:700;margin:0 0 3px;letter-spacing:-.01em}
.mini .k{font-size:.7rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--text3);margin-bottom:11px;display:block}
.ln{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  padding:6px 0;border-bottom:1px dashed var(--line);font-size:.86rem}
.ln:last-child{border-bottom:none}
.ln span:last-child{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;
  font-weight:600;white-space:nowrap}
.up{color:var(--red-lo)} .dn{color:#3B82F6} .ok{color:var(--green)}
.badge{font-size:.6rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  padding:.15em .42em;border-radius:3px;margin-left:.4em;vertical-align:1px;
  background:rgba(133,133,171,.16);color:var(--text3)}
.badge.hol{background:rgba(245,158,11,.16);color:var(--amber)}
.badge.wet{background:rgba(59,130,246,.16);color:#3B82F6}
tr.fieldavg td{background:rgba(133,133,171,.05);font-size:.82rem;color:var(--text3)}
td.wincount{font-weight:700}
.spread{display:flex;align-items:center;gap:3px;height:26px;margin-top:9px}
.spread i{flex:1;border-radius:2px;background:var(--line)}
.needs{font-size:.78rem;color:var(--text3);margin-top:9px;line-height:1.55}
.needs b{color:var(--text2)}
.empty{color:var(--text3);font-size:.88rem;line-height:1.6;background:var(--card);
  border:1px dashed var(--line);border-radius:11px;padding:15px 17px;margin:0}

/* ---- every branch, every bot ---- */
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
.tabs label{font-size:.76rem;font-weight:700;letter-spacing:.03em;padding:7px 13px;
  border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text3);
  cursor:pointer;user-select:none;transition:background .15s,color .15s,border-color .15s}
.tabs label:hover{border-color:var(--mute);color:var(--text2)}
.mx{position:relative}
.mx > input{position:absolute;opacity:0;width:0;height:0;pointer-events:none}
.mx .sheet{display:none}
.mx table{font-size:.85rem}
.mx th,.mx td{padding:9px 13px}
.mx td.br{font-weight:600;position:sticky;left:0;background:var(--card);z-index:1}
.mx tbody tr:nth-child(even) td.br{background:var(--card)}
.mx .call{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;
  text-align:right;white-space:nowrap}
.mx .d{display:inline-block;min-width:3.1em;text-align:right;font-size:.78em;font-weight:600;
  margin-left:.45em;opacity:.85}
.mx .d.o{color:var(--red-lo)}      /* called more than turned up */
.mx .d.u{color:#3B82F6}            /* called fewer */
.mx .d.z{color:var(--green)}
.mx td.best{background:rgba(34,197,94,.10)}
.mx tr.act td{background:rgba(133,133,171,.07);font-weight:700}
.mx tfoot td{border-top:2px solid var(--line);font-weight:700;background:rgba(133,133,171,.05)}
.mx tfoot tr.sub td{font-weight:600;font-size:.8rem;background:transparent;border-top:none;
  color:var(--text3)}
@media (max-width:640px){ .mx th,.mx td{padding:8px 10px;font-size:.8rem} }
.note{color:var(--text2);font-size:.92rem;line-height:1.65;background:var(--card);
  border:1px solid var(--line);border-left:3px solid var(--red);border-radius:11px;padding:17px 19px;margin:0}
footer{margin-top:3rem;padding-top:1.3rem;border-top:1px solid var(--line);color:var(--text3);
  font-size:.8rem;line-height:1.65}
@media (max-width:640px){
  .card{grid-template-columns:38px 1fr;gap:12px;padding:15px}
  .score{grid-column:2;text-align:left;margin-top:4px}
  .score .v{font-size:1.4rem}
  .hero{padding-block:34px 30px}
}
@media (prefers-reduced-motion:reduce){
  *,*:before,*:after{animation:none!important;transition:none!important}
}
</style>

<header class="hero"><div class="w">
  <div class="ribbon"><i></i><i></i><i></i></div>
  <h1>Forecast <span>Championship</span></h1>
  <p class="dek">Four bots call tomorrow&rsquo;s cars at every branch. Every miss counts, over and under,
  so a right network total with wrong branches underneath scores nothing. <b>Lower wins.</b></p>
  <div class="meta">
    <span class="live"><span class="dot"></span><b>__STATUS__</b></span>
    <span><b>Round 2</b> Mon 21 &ndash; Sun 27 Sep</span>
    <span>Round 1 won by <b>Tristan</b> at 26.3%</span>
    <span><b>35%</b> round one &middot; <b>65%</b> this week</span>
    <span><b>44</b> branches scored</span>
  </div>
</div></header>

<div class="w">
<h2>Standings</h2>
<div class="board">__CARDS__</div>
<p class="cap">Bars show each bot against the rest of the field, not against zero. The whole
field currently sits inside a three-point band, so the gaps are small and real.</p>

__LIVE__

<h2>The race</h2>
__RACE__

<h2>Day by day</h2>
__GRID__

<h2>Where the points go</h2>
__LEAK__

<h2>Big branches against the tail</h2>
__SIZE__

<h2>Next call</h2>
__NEXT__

<h2>Every branch, every bot</h2>
__MATRIX__

<footer>__FOOTER__</footer>
</div>
"""

def live_panel(live: dict | None) -> str:
    """Today, in flight. Deliberately separated from the standings so nobody reads a part-day
    network total as a score."""
    if not live:
        return ""
    when = "Closed" if live["closed"] else f"As at {live['at']}"
    head = f'<h2>Today, {live["dow"].lower()} {dt.date.fromisoformat(live["date"]).strftime("%-d %b")}</h2>'
    p, band = live["projected"], live["band"]
    proj = (f'<span class="big">{p:,}</span>'
            f'<span class="lbl">projected close &plusmn;{band}%</span>' if p else
            '<span class="big" style="opacity:.35">&ndash;&ndash;</span>'
            '<span class="lbl">projection opens at half the day</span>')
    chips = []
    for i, (bot, called) in enumerate(sorted(live["calls"].items())):
        if p:
            gap = (called / p - 1) * 100
            cls = "near" if abs(gap) <= band else ("far" if abs(gap) >= 2 * band else "")
            tail = f'<span class="g">{gap:+.0f}%</span>'
        else:
            cls, tail = "", ""
        chips.append(f'<div class="chip {cls}" style="animation-delay:{.06 * i:.2f}s">'
                     f'<b>{bot}</b> called <span class="g">{called:,}</span>{tail}</div>')
    if not chips:
        chips = ['<div class="chip none">No calls in for today yet</div>']
    why = (f'The projection scales today&rsquo;s running count by the share of a '
           f'{live["dow"].lower()} normally checked in by now, over the last eight. It only '
           f'appears once half the day is in, because before that it runs high enough to '
           f'mislead. Percentages are each bot&rsquo;s network total against it, not the '
           f'per-branch score, which can only be settled at close.' if p else
           f'Only {live["share"]}% of a normal {live["dow"].lower()} is in. A projection this '
           f'early is out by a fifth often enough that it is not worth printing, so the board '
           f'waits for half the day.')
    return (f'{head}<div class="livewrap">'
            f'<div class="nums">'
            f'<div><span class="big">{live["so_far"]:,}</span><span class="lbl">cars so far</span></div>'
            f'<div>{proj}</div>'
            f'<div><span class="big">{live["share"]}<small>%</small></span>'
            f'<span class="lbl">of a normal {live["dow"].lower()} done</span></div>'
            f'</div>'
            f'<div class="prog"><i style="width:{min(live["share"], 100)}%"></i></div>'
            f'<p class="cap" style="margin:0">{when}. {why}</p>'
            f'<div class="chips">{"".join(chips)}</div></div>')

def _week_days(state: dict) -> list[str]:
    return sorted(d for d in state["days"] if WEEK[0].isoformat() <= d <= WEEK[1].isoformat())

def _scored_days(state: dict) -> list[str]:
    return [d for d in _week_days(state)
            if any(v.get("wape") is not None for v in state["days"][d]["bots"].values())]

def _empty(msg: str) -> str:
    return f'<p class="empty">{msg}</p>'

def race_panel(st: pd.DataFrame, state: dict) -> str:
    """Where the week lands if the rest of it runs like the days already in, and what each
    chasing bot would have to average over the days left to draw level."""
    scored = _scored_days(state)
    left = 7 - len(scored)
    if not scored:
        return _empty("The week runs Monday 21 to Sunday 27 September. Once the first day "
                      "closes, this shows each bot&rsquo;s projected finish, the gap to first, "
                      "and the average the chasing bots would need over the days left.")
    target = st.iloc[0]["champion"]
    cards = []
    for i, r in st.iterrows():
        if pd.isna(r["champion"]):
            body = (f'<div class="ln"><span>{"Round 1 only" if pd.notna(r["round1"]) else "New this week"}'
                    f'</span><span>{_r1(r)}</span></div>'
                    f'<div class="needs">No day scored yet this week.</div>')
        else:
            gap = r["champion"] - target
            gtxt = ("<span class='ok'>leading</span>" if i == 0
                    else f'<span class="up">{gap:+.2f} behind</span>')
            need = _need(r, target, left)
            if i == 0:
                nl = f"Ahead on {int(r['days_scored'])} scored day{'s' if r['days_scored'] != 1 else ''}."
            elif need is None:
                nl = "No days left to make it up."
            elif need <= 0:
                nl = ("<b>Out of reach</b> even with a perfect card on every remaining day.")
            elif need >= 100:
                nl = f"Needs only to keep submitting over the last {left} day{'s' if left != 1 else ''}."
            else:
                nl = (f"Needs to average <b>{need:.1f}%</b> over the last {left} "
                      f"day{'s' if left != 1 else ''} to draw level.")
            body = (f'<div class="ln"><span>Projected finish</span><span>{r["champion"]:.2f}%</span></div>'
                    f'<div class="ln"><span>This week so far</span>'
                    f'<span>{r["week"]:.2f}% over {int(r["days_scored"])}</span></div>'
                    f'<div class="ln"><span>Round 1 carried</span><span>{_r1(r)}</span></div>'
                    f'<div class="needs">{nl}{_miss_warning(r)}</div>')
        cards.append(f'<div class="mini" style="animation-delay:{.07 * i:.2f}s">'
                     f'<h3>{r["bot"]}</h3><span class="k">'
                     f'{gtxt if pd.notna(r["champion"]) else "not scored"}</span>{body}</div>')
    return (f'<div class="grid2">{"".join(cards)}</div>'
            f'<p class="cap">Projection assumes the rest of the week scores like the days already '
            f'in. With {len(scored)} of 7 days closed it moves a lot, so read the gap, not the '
            f'decimal. Round one carries 35% and cannot change.</p>')

def _r1(r: pd.Series) -> str:
    """A round-one score, or a dash for someone who was not in round one."""
    return f'{r["round1"]:.1f}%' if pd.notna(r["round1"]) else "&ndash;"

def _miss_warning(r: pd.Series) -> str:
    """A day with no file is currently dropped from the average rather than scored.

    That is deliberate: a file can be absent because Owen has not collected it yet, not
    because the bot failed to submit, and scoring an uncollected file as a total miss would
    put a bot on 100% for a day it may well have called. But a dropped day must not be
    invisible either, so the card says what the projection becomes if the board treats it
    the way it treats a missing branch, which is a full miss."""
    n_missed = int(r["missed"])
    if not n_missed or pd.isna(r["week"]):
        return ""
    n = int(r["days_scored"])
    harsh = ROUND1_WEIGHT * r["round1"] + WEEK_WEIGHT * (r["week"] * n + 100.0 * n_missed) / (n + n_missed)
    return (f' <b>{n_missed} day{"s" if n_missed != 1 else ""} with no file</b>, left out of the '
            f'average above. Scored as a full miss it would be {harsh:.1f}%.')

def _need(r: pd.Series, target: float, left: int) -> float | None:
    """The average WAPE over the remaining days that would land this bot on the leader's
    current projection. Negative means it cannot be done."""
    if left <= 0 or pd.isna(r["week"]):
        return None
    n = int(r["days_scored"])
    # a week-only player's championship IS their week average, so there is no round-one
    # portion to subtract out before solving for the days that remain
    need_week = (target if pd.isna(r["round1"])
                 else (target - ROUND1_WEIGHT * r["round1"]) / WEEK_WEIGHT)
    return (need_week * (n + left) - r["week"] * n) / left

def grid_panel(state: dict) -> str:
    """Day by day, with the winner marked, the field average so the day's own difficulty is
    visible, and a badge for rain or a public holiday."""
    days = _week_days(state)
    if not days:
        return _empty("Nothing scored yet. The week runs Monday 21 to Sunday 27 September, and "
                      "each day lands here the evening it closes, once the calls are in.")
    head, wins = [], {b: 0 for b in ROUND1}
    for d in days:
        c = state["days"][d].get("context") or {}
        b = ""
        if c.get("holiday"):
            b = f'<span class="badge hol" title="{c["holiday"]}">hol</span>'
        elif c.get("rain") and c["rain"] >= 1.0:
            b = f'<span class="badge wet" title="{c["rain"]}mm in trading hours">wet</span>'
        head.append(f'<th class="n">{dt.date.fromisoformat(d).strftime("%a %-d")}{b}</th>')
    for d in days:
        got = {b: v["wape"] for b, v in state["days"][d]["bots"].items() if v.get("wape") is not None}
        if got:
            wins[min(got, key=got.get)] = wins.get(min(got, key=got.get), 0) + 1
    body = []
    for bot in ROUND1:
        cells = []
        for d in days:
            v = state["days"][d]["bots"].get(bot, {})
            got = [x["wape"] for x in state["days"][d]["bots"].values() if x.get("wape") is not None]
            if v.get("wape") is not None:
                cells.append(f'<td class="n{" win" if got and v["wape"] == min(got) else ""}">'
                             f'{v["wape"]:.1f}%</td>')
            elif "error" in v:
                cells.append('<td class="n bad" title="the file could not be read">file?</td>')
            else:
                cells.append('<td class="n dim">&mdash;</td>')
        body.append(f'<tr><td class="who">{bot}</td>{"".join(cells)}'
                    f'<td class="n wincount">{wins.get(bot, 0) or "&mdash;"}</td></tr>')
    avg, marg = [], []
    for d in days:
        got = sorted(x["wape"] for x in state["days"][d]["bots"].values() if x.get("wape") is not None)
        avg.append(f'<td class="n">{sum(got) / len(got):.1f}%</td>' if got else '<td class="n">&mdash;</td>')
        marg.append(f'<td class="n">{got[1] - got[0]:.1f}</td>' if len(got) > 1 else '<td class="n">&mdash;</td>')
    tot = "".join(f'<td class="n dim">{state["days"][d]["actual"]:,}</td>' for d in days)
    return (f'<div class="scroll"><table><thead><tr><th>Bot</th>{"".join(head)}'
            f'<th class="n">Won</th></tr></thead><tbody>{"".join(body)}'
            f'<tr class="fieldavg"><td class="who dim">field average</td>{"".join(avg)}<td></td></tr>'
            f'<tr class="fieldavg"><td class="who dim">winning margin</td>{"".join(marg)}<td></td></tr>'
            f'<tr class="tot"><td class="who dim">cars washed</td>{tot}<td></td></tr>'
            f'</tbody></table></div>'
            f'<p class="cap">Green is the day&rsquo;s best call. The field average says how hard '
            f'the day was for everyone, so a bad row can be read as the day rather than the bot. '
            f'Winning margin is the gap between first and second that day.</p>')

def leak_panel(state: dict) -> str:
    """Per bot, the branches actually costing them the week, and which way they lean."""
    days = _scored_days(state)
    if not days:
        return _empty("Once a day is scored this breaks each bot&rsquo;s error down to the "
                      "branches carrying it, and says whether they lean over or under.")
    cards, i = [], 0
    for bot in ROUND1:
        agg, tot_abs, net, n = {}, 0.0, 0.0, 0
        for d in days:
            v = state["days"][d]["bots"].get(bot) or {}
            ba = state["days"][d].get("branch_actual") or {}
            for b, c in (v.get("branch_call") or {}).items():
                e = c - ba.get(b, 0)
                if not e:
                    continue
                agg[b] = agg.get(b, 0.0) + abs(e)
                net += e
                tot_abs += abs(e)
            if v.get("wape") is not None:
                n += 1
        if not agg:
            cards.append(f'<div class="mini"><h3>{bot}</h3>'
                         f'<span class="k">no scored day</span>'
                         f'<div class="needs">Nothing in yet.</div></div>')
            i += 1
            continue
        top = sorted(agg.items(), key=lambda kv: -kv[1])[:5]
        lns = "".join(
            f'<div class="ln"><span>{b}</span><span>{v:,.0f} cars '
            f'<span class="dim">{v / tot_abs * 100:.0f}%</span></span></div>' for b, v in top)
        lean = ("runs high" if net > tot_abs * 0.08 else
                "runs low" if net < -tot_abs * 0.08 else "balanced")
        cls = "up" if lean == "runs high" else "dn" if lean == "runs low" else "ok"
        cards.append(f'<div class="mini" style="animation-delay:{.07 * i:.2f}s"><h3>{bot}</h3>'
                     f'<span class="k">{n} day{"s" if n != 1 else ""} &middot; '
                     f'<span class="{cls}">{lean}</span></span>{lns}'
                     f'<div class="needs">Top five carry <b>{sum(v for _, v in top) / tot_abs * 100:.0f}%</b> '
                     f'of this bot&rsquo;s total miss.</div></div>')
        i += 1
    return (f'<div class="grid2">{"".join(cards)}</div>'
            f'<p class="cap">Cars missed at each branch, added up over the scored days, biggest '
            f'first. A bot that runs high calls more cars than turn up.</p>')

def size_panel(state: dict) -> str:
    """The sixteen branches that carry two thirds of the cars, against the long tail."""
    days = _scored_days(state)
    rows, nbig = [], 0
    for d in days:
        for v in state["days"][d]["bots"].values():
            nbig = max(nbig, (v.get("big") or {}).get("n") or 0)
    for bot in ROUND1:
        acc = {"big": [0.0, 0.0, 0.0], "tail": [0.0, 0.0, 0.0]}   # err, actual, called
        ok = False
        for d in days:
            v = state["days"][d]["bots"].get(bot) or {}
            for k in ("big", "tail"):
                s = v.get(k)
                if not s or s.get("wape") is None or not s.get("actual"):
                    continue
                a = float(s["actual"])
                acc[k][0] += s["wape"] / 100 * a
                acc[k][1] += a
                acc[k][2] += a * (1 + (s.get("bias") or 0.0) / 100)
                ok = True
        if not ok:
            continue
        cells = []
        for k in ("big", "tail"):
            e, a, c = acc[k]
            cells.append(f'<td class="n">{e / a * 100:.1f}%</td>'
                         f'<td class="n">{(c / a - 1) * 100:+.1f}%</td>' if a else
                         '<td class="n dim">&mdash;</td><td class="n dim">&mdash;</td>')
        rows.append(f'<tr><td class="who">{bot}</td>{"".join(cells)}</tr>')
    if not rows:
        return _empty("Once a day is scored this splits every bot&rsquo;s error into the "
                      "branches that carry the cars and the long tail behind them.")
    return (f'<div class="scroll"><table><thead><tr><th>Bot</th>'
            f'<th class="n">Big WAPE</th><th class="n">Big bias</th>'
            f'<th class="n">Tail WAPE</th><th class="n">Tail bias</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="cap">Big means the {nbig} busiest branches, the fewest that together '
            f'carry {BIG_SHARE:.0%} of an ordinary day. Size is measured on the eight weeks '
            f'before each day, never on the day itself, because bucketing by what actually '
            f'happened is hindsight and invents an effect that is not there. A bot can look '
            f'respectable overall and still be losing the week on these.</p>')

TARGET_HOUR = 19   # when Owen TRIES to have all four in. Not a cut-off: a call is valid any
                   # time before the day it predicts, so this hour must never unseal anything.

def _sheet(day: dt.date, calls: dict[str, dict], actual: dict[str, float] | None,
           branches: set[str]) -> str:
    """One day's full table: a row per branch, a column per bot, actuals and misses when the
    day has closed, and the totals that matter along the bottom."""
    bots = [b for b in ROUND1 if b in calls]
    order = sorted(branches, key=lambda b: -((actual or {}).get(b) if actual
                                             else max(calls[x].get(b, 0) for x in bots)))
    rows = []
    wins = dict.fromkeys(bots, 0)
    for b in order:
        cells, a = [], (actual or {}).get(b)
        if a is not None:
            errs = {x: abs(calls[x].get(b, 0) - a) for x in bots}
            closest = min(errs.values())
        for x in bots:
            c = round(calls[x].get(b, 0))
            if a is None:
                cells.append(f'<td class="call">{c:,}</td>')
                continue
            e = c - a
            cls = "z" if e == 0 else ("o" if e > 0 else "u")
            win = errs[x] == closest
            wins[x] += win
            cells.append(f'<td class="call{" best" if win else ""}">{c:,}'
                         f'<span class="d {cls}">{e:+d}</span></td>')
        act = f'<td class="call">{round(a):,}</td>' if a is not None else ""
        rows.append(f'<tr><td class="br">{b}</td>{"".join(cells)}{act}</tr>')

    head = "".join(f'<th class="call">{x}</th>' for x in bots)
    head += '<th class="call">Actual</th>' if actual else ""
    foot = []
    tot_a = sum((actual or {}).get(b, 0) for b in branches)
    tcells = "".join(f'<td class="call">{round(sum(calls[x].get(b, 0) for b in branches)):,}</td>'
                     for x in bots)
    foot.append(f'<tr><td class="br">Total cars</td>{tcells}'
                + (f'<td class="call">{round(tot_a):,}</td></tr>' if actual else "</tr>"))
    if actual and tot_a:
        w = "".join(f'<td class="call">'
                    f'{sum(abs(calls[x].get(b, 0) - actual.get(b, 0)) for b in branches) / tot_a * 100:.1f}%'
                    f'</td>' for x in bots)
        bi = "".join(f'<td class="call">'
                     f'{(sum(calls[x].get(b, 0) for b in branches) / tot_a - 1) * 100:+.1f}%'
                     f'</td>' for x in bots)
        wn = "".join(f'<td class="call">{wins[x]}</td>' for x in bots)
        foot.append(f'<tr><td class="br">WAPE</td>{w}<td></td></tr>')
        foot.append(f'<tr class="sub"><td class="br">Over or under</td>{bi}<td></td></tr>')
        foot.append(f'<tr class="sub"><td class="br">Branches called closest</td>{wn}<td></td></tr>')
    return (f'<div class="scroll"><table><thead><tr><th>Branch</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody><tfoot>{"".join(foot)}</tfoot></table></div>')

def matrix_panel(state: dict) -> str:
    """Every branch against every bot, one day at a time.

    Only ever shows a day where ALL FOUR calls are in. A day that has closed also carries the
    actuals and each bot's miss; a day still to come shows the calls alone. The seal is the
    same one as the Next call panel and for the same reason: a call counts any time before
    the day it predicts, so a partial table would let a late submitter read the field."""
    branches = scored_branches()
    sheets, labels = [], []
    # Week days already scored, PLUS today and tomorrow read straight off the inbox. Today
    # matters: a day is only written into state when it is scored at 19:05, so between
    # midnight and the evening the current day exists nowhere else. Leaving it out is why
    # Monday's table vanished at midnight, having been visible all Sunday evening as
    # "tomorrow".
    now = dt.datetime.now(SAST).date()
    days = _week_days(state) + [now.isoformat(), (now + dt.timedelta(days=1)).isoformat()]
    for d in sorted(set(days)):
        day = dt.date.fromisoformat(d)
        rec = state["days"].get(d) or {}
        actual = rec.get("branch_actual")
        calls = {b: v["branch_call"] for b, v in (rec.get("bots") or {}).items()
                 if v.get("branch_call")}
        if not calls:
            calls = _inbox_calls(day)
        if len(calls) < len(ROUND1):
            continue
        labels.append((d, day.strftime("%a %-d"), bool(actual)))
        sheets.append(_sheet(day, calls, actual, branches))
    if not sheets:
        return _empty(
            "This fills in the moment all four calls for a day are in: every branch, every "
            "bot, side by side. Once the day closes it also carries the actual count, each "
            "bot&rsquo;s miss at every branch, and the daily WAPE and over-under underneath. "
            "It stays hidden until the last call lands, so nobody can read it and then write "
            "their own.")
    # CSS-only tabs: the page is regenerated every hour, so there is nothing for script to do.
    # The radios must be SIBLINGS of .sheets for the ~ selector to reach it, so they sit
    # directly in .mx and the labels point at them by id from inside the tab bar.
    last = len(sheets) - 1
    radios, tabs, panes, css = [], [], [], []
    for i, (d, lab, closed) in enumerate(labels):
        radios.append(f'<input type="radio" name="mxday" id="mx{i}"'
                      f'{" checked" if i == last else ""}>')
        tabs.append(f'<label for="mx{i}">{lab}{"" if closed else " &middot; called"}</label>')
        panes.append(f'<div class="sheet" id="mxs{i}">{sheets[i]}</div>')
        css.append(f'#mx{i}:checked~.sheets #mxs{i}{{display:block}}'
                   f'#mx{i}:checked~.tabs label[for="mx{i}"]'
                   f'{{background:var(--ink2);border-color:var(--ink2);color:#fff}}')
    return (f'<div class="mx"><style>{"".join(css)}</style>{"".join(radios)}'
            f'<div class="tabs">{"".join(tabs)}</div>'
            f'<div class="sheets">{"".join(panes)}</div></div>'
            f'<p class="cap">The number is the call, the small figure beside it is the miss at '
            f'that branch: red called too many, blue too few, green exactly right. Green '
            f'shading marks whoever came closest at that branch. Days marked "called" have '
            f'not traded yet, so they show the calls only.</p>')

def render(st: pd.DataFrame, state: dict, live: dict | None = None) -> str:
    """The board the whole team reads. Self-contained, both themes, motion on load only."""
    days = sorted(d for d in state["days"] if WEEK[0].isoformat() <= d <= WEEK[1].isoformat())
    lead = st.iloc[0]["bot"] if st["champion"].notna().any() else None
    scored = st["champion"].notna().any()
    vals = [v for v in (st["champion"] if scored else st["round1"]) if pd.notna(v)]
    best, worst = (min(vals), max(vals)) if vals else (0.0, 1.0)
    span = max(worst - best, 0.01)

    def bar(v):
        """Lower is better, so a shorter bar is a better bot. Scaled across the field's own
        range, because every score sits inside a three-point band and a zero-based bar
        would make all four look identical."""
        return 18 + 82 * (v - best) / span

    cards = []
    for i, r in st.iterrows():
        has = pd.notna(r["champion"])
        ref = r["champion"] if has else r["round1"]
        pct = bar(ref) if pd.notna(ref) else 100.0
        score = (f'<span class="v">{r["champion"]:.2f}<small>%</small></span><span class="l">champion</span>'
                 if has else
                 (f'<span class="v pending">{r["round1"]:.1f}<small>%</small></span>'
                  f'<span class="l">round 1 only</span>' if pd.notna(r["round1"]) else
                  f'<span class="v pending">&ndash;&ndash;</span><span class="l">new, week only</span>'))
        tag = f'<span class="tag">{int(r["missed"])} missed</span>' if r["missed"] else ""
        if r.get("week_only"):
            tag += '<span class="tag new">week only</span>'
        elif r.get("imputed"):
            tag += '<span class="tag new">new this round</span>'
        d1 = ROUND1_DETAIL.get(r["bot"])
        bits = [(f'round 1 <b>{r["round1"]:.2f}%</b> <span class="dim">(field average, did not '
                 f'play round 1)</span>' if r.get("imputed") else
                 f'round 1 <b>{r["round1"]:.1f}%</b>' if pd.notna(r["round1"])
                 else 'no round 1, <b>scored on this week alone</b>')
                + (f' <span class="dim">(fixed {d1[0]:.1f} &middot; daily {d1[1]:.1f} &middot; bias {d1[2]:+.1f})</span>'
                   if d1 else '')]
        if pd.notna(r["week"]):
            bits.append(f'this week <b>{r["week"]:.2f}%</b> over {int(r["days_scored"])} days')
        if pd.notna(r["bias"]):
            bits.append(f'bias <b>{r["bias"]:+.1f}%</b>')
        cards.append(
            f'<div class="card{" lead" if r["bot"] == lead else ""}" style="animation-delay:{.08 * i + .1:.2f}s">'
            f'<div class="pos mono">{i + 1}</div>'
            f'<div><div class="nm">{r["bot"]}{tag}</div>'
            f'<div class="track"><div class="fill" style="width:{pct:.0f}%;'
            f'animation-delay:{.08 * i + .3:.2f}s"></div></div>'
            f'<div class="sub">{" &middot; ".join(bits)}</div></div>'
            f'<div class="score">{score}</div></div>')

    n_scored = len(_scored_days(state))
    status = f"{n_scored} of 7 days scored" if n_scored else "waiting for Monday"

    foot = ("Per-branch WAPE: every miss in cars added up, divided by the cars actually washed, over the "
            "44 scored branches (all except De Drift and Newinbosch). Positive bias means a bot called more "
            "cars than turned up. A bot with no file for a day is marked missed, never quietly dropped. "
            + (f"A player who joined at round two carries the field average of the four round-one "
               f"scores ({ROUND1[sorted(IMPUTED)[0]]:.2f}%) in place of a round one they did not play, so "
               f"they cannot lead the championship on one week alone and cannot be punished for "
               f"arriving either. " if IMPUTED else "") +
            "Ground truth is the work-order count by check-in date, South African time. Updated "
            f"{dt.datetime.now(dt.timezone(dt.timedelta(hours=2))).strftime('%-d %B at %H:%M')} SAST.")
    if live:
        status = f"{status} &middot; updated {live['at']}"
    return (TEMPLATE.replace("__CARDS__", "".join(cards))
            .replace("__LIVE__", live_panel(live))
            .replace("__RACE__", race_panel(st, state))
            .replace("__GRID__", grid_panel(state))
            .replace("__LEAK__", leak_panel(state))
            .replace("__SIZE__", size_panel(state))
            .replace("__NEXT__", next_panel())
            .replace("__MATRIX__", matrix_panel(state))
            .replace("__STATUS__", status).replace("__FOOTER__", foot))


# ------------------------------------------------------------------ the data layer
# Everything below reads from the store, which the website and the hub fill. Nothing reads the
# database. These replace the functions that, on the hub, used to query production directly.
def scored_branches() -> set[str]:
    return {b for b in CARD if b not in SCORED_EXCLUDE}


def actuals(day: dt.date) -> dict[str, float]:
    """The day's counts per branch, as posted by the hub once the day closed. Empty until then,
    which is exactly right: an unclosed day has no score."""
    b = store.load_actuals(day) or {}
    return {k: float(v) for k, v in (b.get("branch_actual") or {}).items()}


def branch_sizes(day: dt.date, weeks: int = 8) -> dict[str, float]:
    """Each branch's ordinary day, measured by the hub on the eight weeks BEFORE the day, never
    the day itself. Falls back to the most recent bundle so a day posted without sizes still
    gets its big-versus-tail split."""
    b = store.load_actuals(day) or store.latest_actuals() or {}
    return {k: float(v) for k, v in (b.get("sizes") or {}).items()}


def day_context(day: dt.date) -> dict:
    """What made a day easy or hard. The holiday comes from the calendar, which is public; the
    rain comes from the hub's bundle, observed where it had it and forecast where it did not."""
    out: dict = {"dow": day.strftime("%a"), "holiday": None, "rain": None, "rain_src": None}
    try:
        h = CAL.holiday_name(day)
        out["holiday"] = h if h and h != "none" else None
    except Exception:  # noqa: BLE001
        pass
    c = ((store.load_actuals(day) or {}).get("context") or {})
    if c.get("rain") is not None:
        out["rain"], out["rain_src"] = c["rain"], c.get("rain_src")
    return out


def _calls(day: dt.date) -> dict[str, dict[str, float]]:
    out = {}
    for player, p in store.call_paths(day).items():
        try:
            out[player] = read_call(p)
        except Exception:  # noqa: BLE001 - a bad file is reported at scoring, never fatal here
            pass
    return out


def _call_totals(day: dt.date) -> dict[str, int]:
    br = scored_branches()
    return {p: round(sum(c.get(b, 0.0) for b in br)) for p, c in _calls(day).items()}


def score_day(day: dt.date) -> dict | None:
    """Score one closed day. Returns None if the hub has not posted the day's counts yet."""
    if store.load_actuals(day) is None:
        return None
    act = actuals(day)
    branches = scored_branches()
    sizes = branch_sizes(day) or None
    out = {"date": day.isoformat(), "actual": round(sum(act.get(b, 0.0) for b in branches)),
           "bots": {}, "no_submission": [], "context": day_context(day),
           "branch_actual": {b: round(act.get(b, 0.0)) for b in branches}}
    for player, p in store.call_paths(day).items():
        try:
            out["bots"][player] = score(read_call(p), act, branches, sizes)
        except Exception as exc:  # noqa: BLE001 - a bad file must be visible, never silent
            out["bots"][player] = {"error": str(exc)}
    for bot in ROUND1:
        if bot not in out["bots"]:
            out["no_submission"].append(bot)
    unknown = sorted(set(out["bots"]) - set(ROUND1))
    if unknown:
        out["unknown_players"] = unknown
    return out


def build_state() -> dict:
    """Score every closed day from scratch, every time.

    Deliberately not cached. There are at most seven days and five files per day, so rescoring
    everything costs nothing, and it removes the entire class of bugs where a late file or a
    corrected count sits unscored behind a stale cache. The copy written to disk is for
    inspection only; nothing reads it back as truth."""
    days = {}
    for f in (sorted(store.ACTUALS.glob("*.json")) if store.ACTUALS.exists() else []):
        r = score_day(dt.date.fromisoformat(f.stem))
        if r is not None:
            days[f.stem] = r
    state = {"days": days}
    store.save_state(state)
    return state


# ------------------------------------------------------------------- live, in-day
def day_curve(day: dt.date) -> pd.Series:
    """The cumulative share of a normal day checked in by the end of each hour, as the hub
    measured it for this weekday."""
    cur = ((store.load_live() or {}).get("curve") or {})
    if not cur:
        return pd.Series(dtype=float)
    return pd.Series({int(k): float(v) for k, v in cur.items()}).sort_index()


def live_today() -> dict | None:
    """Today in flight, from the hub's latest hourly post. Returns None if there is no post for
    today, so a stale reading from yesterday can never pass itself off as the current one."""
    live = store.load_live()
    now = dt.datetime.now(SAST)
    day = now.date()
    if not live or live.get("date") != day.isoformat():
        return None
    # Measure the share at the moment the hub counted, not the moment the page was opened.
    # A page loaded forty minutes after the count would otherwise divide an old running total
    # by a later share of the day and project a close that is too low.
    try:
        hh, mm = (int(x) for x in str(live.get("at", "")).split(":"))
        seen = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        seen = now
    branches = scored_branches()
    so_far = sum(float((live.get("branch_so_far") or {}).get(b, 0)) for b in branches)
    share = _share_at(day_curve(day), seen)
    proj = round(so_far / share) if share >= MIN_SHARE and so_far else None
    return {"date": day.isoformat(), "at": seen.strftime("%H:%M"), "so_far": round(so_far),
            "share": round(share * 100), "projected": proj, "calls": _call_totals(day),
            "band": _band(share) if proj else None,
            "closed": seen.hour >= 18, "dow": day.strftime("%A")}


def next_panel() -> str:
    """Tomorrow's calls side by side, and only once every one of them is in.

    This board is shared with every player. A call counts any time before the day it
    predicts, so there is no moment before that day when revealing a partial set is safe: a
    player submitting at nine at night would otherwise read the board and then write their
    own number. The target hour is an aim, not a lock, so it deliberately unseals nothing.
    Full set or nothing."""
    now = dt.datetime.now(SAST)
    # Tomorrow once anyone has called it, otherwise today. Today's calls are already locked,
    # the day is running, so they are the useful thing to show until the next set arrives.
    tom = now.date() + dt.timedelta(days=1)
    day = tom if store.call_paths(tom) else now.date()
    calls = _call_totals(day)
    when = day.strftime("%A %-d %B")
    total = len(ROUND1)
    if len(calls) < total:
        n = len(calls)
        who = ", ".join(sorted(calls)) if calls else "nobody"
        return _empty(
            f"Calls for {when} stay sealed until all {total} are in. <b>{n} of {total}</b> so "
            f"far ({who}). A call counts any time before the day it predicts, so showing a "
            f"partial set at any hour would let whoever is still to submit read the board "
            f"first. The aim is all {total} by {TARGET_HOUR}:00, but the numbers appear when "
            f"the last one lands, not when the clock says so.")
    lo, hi = min(calls.values()), max(calls.values())
    span = max(hi - lo, 1)
    lns = "".join(
        f'<div class="ln"><span>{b}</span><span>{v:,}'
        + (f' <span class="dim">{(v / lo - 1) * 100:+.0f}%</span>' if lo else '')
        + '</span></div>' for b, v in sorted(calls.items(), key=lambda kv: kv[1]))
    tail = (f'<div class="needs">All {total} in. The field disagrees by <b>{hi - lo:,} cars</b>, '
            f'which is {span / lo * 100:.0f}% of the lowest call.</div>')
    return (f'<div class="panel"><span class="k">{when} &middot; network total, '
            f'{len(scored_branches())} scored branches</span>{lns}{tail}</div>')


def _inbox_calls(day: dt.date) -> dict[str, dict[str, float]]:
    """Every player's per-branch call for a day, straight from the store."""
    return _calls(day)


# ------------------------------------------------------------------ what the website calls
def page() -> str:
    """The results page, built fresh from whatever is in the store right now."""
    state = build_state()
    return render(standings(state), state, live_today())


def locked(day: dt.date) -> bool:
    """A call for a day can be changed right up until that day starts, and never after. This
    is what stops anyone dropping a file once the day is under way and the counts are moving."""
    return dt.datetime.now(SAST).date() >= day


def check_call(path: Path) -> dict:
    """Read a dropped file and say plainly what was in it, before it is accepted.

    Catches the wrong column, a missing branch or an unreadable file at the moment a player
    drops it, instead of discovering it the evening the day is scored."""
    c = read_call(path)
    br = scored_branches()
    got = {b: v for b, v in c.items() if b in br}
    return {"branches": len(got), "of": len(br), "total": round(sum(got.values())),
            "missing": sorted(b for b in br if b not in c),
            "dropped": sorted(set(c) - br)}
