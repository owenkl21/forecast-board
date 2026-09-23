"""The competition website: a drop page for calls and the results board.

Players drop their own CSV (or workbook) behind a per-player PIN. The hub posts the owner's
call and the day's actual counts behind a shared token. Everything public is the sealed board;
no address returns anyone's raw call, so reading the site can never tell a late submitter what
the others have called.

Settings, never committed:
    BOARD_PINS       JSON of {"Player": "pin", ...}
    BOARD_HUB_TOKEN  the secret the hub sends as a bearer token
    BOARD_DATA       where to keep files (defaults to ./data)
"""
from __future__ import annotations

import datetime as dt
import hmac
import html
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import importers, scoring as S, store  # noqa: E402

app = FastAPI(title="Forecast Championship", docs_url=None, redoc_url=None, openapi_url=None)

MAX_BYTES = 2_000_000          # a real call is a few kilobytes; this refuses anything absurd
DAYS_AHEAD = 7                 # how far ahead a call may be dropped


# ------------------------------------------------------------------ settings
def _pins() -> dict[str, str]:
    raw = os.environ.get("BOARD_PINS", "")
    try:
        return {k: str(v) for k, v in json.loads(raw).items()} if raw else {}
    except json.JSONDecodeError:
        return {}


def _pin_ok(player: str, pin: str) -> bool:
    want = _pins().get(player)
    # constant-time, so the time a wrong guess takes says nothing about how close it was
    return bool(want) and hmac.compare_digest(want.encode(), (pin or "").strip().encode())


def _hub_ok(authorization: str | None) -> bool:
    want = os.environ.get("BOARD_HUB_TOKEN", "")
    got = (authorization or "").removeprefix("Bearer ").strip()
    return bool(want) and hmac.compare_digest(want.encode(), got.encode())


def _today() -> dt.date:
    return dt.datetime.now(S.SAST).date()


# ------------------------------------------------------------------ the board
@app.get("/", response_class=HTMLResponse)
def results() -> str:
    body = S.page().replace("</style>", _CTA_CSS + "</style>", 1)
    body = body.replace('<div class="meta">', _cta() + '<div class="meta">', 1)
    # the board was built to sit inside another page; served on its own, a phone has to be told
    # to fit it to the screen or it shows a shrunk desktop page
    return '<meta name="viewport" content="width=device-width,initial-scale=1">\n' + body


@app.get("/health")
def health() -> dict:
    return {"ok": True, "players": list(S.ROUND1), "today": _today().isoformat()}


# ------------------------------------------------------------------ dropping a call
@app.get("/submit", response_class=HTMLResponse)
def submit_form(msg: str = "", ok: str = "") -> str:
    return _drop_page(msg=msg, ok=ok)


@app.post("/submit", response_class=HTMLResponse)
async def submit(player: str = Form(...), pin: str = Form(...), date: str = Form(...),
                 file: UploadFile = File(...)) -> str:
    player = player.strip()
    if player not in S.ROUND1:
        return _drop_page(msg="That name is not on the roster.")
    if not _pin_ok(player, pin):
        return _drop_page(msg="That PIN does not match. Nothing was saved.", player=player)
    try:
        day = dt.date.fromisoformat(date)
    except ValueError:
        return _drop_page(msg="That date could not be read.", player=player)
    if S.locked(day):
        return _drop_page(msg=f"Calls for {day:%A %-d %B} are locked: that day has already started. "
                              f"A call can be changed right up until midnight before its day, never after.",
                          player=player)
    if day > _today() + dt.timedelta(days=DAYS_AHEAD):
        return _drop_page(msg=f"That is more than {DAYS_AHEAD} days ahead.", player=player)

    raw = await file.read()
    if not raw:
        return _drop_page(msg="The file was empty.", player=player)
    if len(raw) > MAX_BYTES:
        return _drop_page(msg="That file is far too big to be a call.", player=player)

    name = (file.filename or "").lower()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ("upload.xlsx" if name.endswith((".xlsx", ".xlsm")) else "upload.csv")
        tmp.write_bytes(raw)
        try:
            if tmp.suffix == ".xlsx":
                csv_text = importers.xlsx_to_csv_text(tmp, day.isoformat())
                tmp = Path(td) / "converted.csv"
                tmp.write_text(csv_text)
            else:
                csv_text = raw.decode("utf-8-sig")
            chk = S.check_call(tmp)
        except Exception as exc:  # noqa: BLE001 - a bad file is the player's to fix, plainly
            return _drop_page(msg=f"That file could not be read as a call: {html.escape(str(exc))}",
                              player=player)

    if chk["branches"] < chk["of"] // 2:
        return _drop_page(msg=f"Only {chk['branches']} of {chk['of']} branches were recognised, so "
                              f"this is probably the wrong file. Nothing was saved.", player=player)

    store.save_call(day, player, csv_text)
    return _drop_page(ok=_receipt(player, day, chk), player=player)


# ------------------------------------------------------------------ the hub
@app.post("/api/hub/call")
async def hub_call(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    if not _hub_ok(authorization):
        raise HTTPException(401, "bad hub token")
    p = await request.json()
    day = dt.date.fromisoformat(p["date"])
    player = p.get("player", "Owen")
    if player not in S.ROUND1:
        raise HTTPException(400, f"{player} is not on the roster")
    if S.locked(day):
        raise HTTPException(409, f"calls for {day} are locked")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "call.csv"
        tmp.write_text(p["csv"])
        chk = S.check_call(tmp)
    store.save_call(day, player, p["csv"])
    return JSONResponse({"saved": True, "player": player, **chk})


@app.post("/api/hub/actuals")
async def hub_actuals(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    if not _hub_ok(authorization):
        raise HTTPException(401, "bad hub token")
    p = await request.json()
    day = dt.date.fromisoformat(p["date"])
    store.save_actuals(day, {k: p.get(k) for k in ("date", "branch_actual", "sizes", "context")})
    return JSONResponse({"saved": True, "branches": len(p.get("branch_actual") or {})})


@app.post("/api/hub/live")
async def hub_live(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    if not _hub_ok(authorization):
        raise HTTPException(401, "bad hub token")
    p = await request.json()
    store.save_live({k: p.get(k) for k in ("date", "at", "branch_so_far", "curve")})
    return JSONResponse({"saved": True, "at": p.get("at")})


# ------------------------------------------------------------------ html
_CTA_CSS = """
.cta{display:inline-flex;align-items:center;gap:12px;margin:0 0 1.6rem;padding:15px 24px;
  background:var(--red);color:#fff;text-decoration:none;border-radius:12px;font-weight:800;
  font-size:1.05rem;letter-spacing:-.01em;box-shadow:0 8px 22px rgba(220,38,38,.38);
  transition:background .15s,transform .15s,box-shadow .15s}
.cta:hover{background:var(--red-lo);transform:translateY(-2px);box-shadow:0 12px 28px rgba(220,38,38,.48)}
.cta:active{transform:none}
.cta small{font-size:.8rem;font-weight:600;color:rgba(255,255,255,.82)}
.cta .arr{font-size:1.25rem;line-height:1;transition:transform .15s}
.cta:hover .arr{transform:translateX(3px)}
@media (max-width:560px){.cta{display:flex;justify-content:center;width:100%;box-sizing:border-box}}
"""


def _cta() -> str:
    """The way in to the drop page, at the top of the board where nobody can miss it."""
    tomorrow = _today() + dt.timedelta(days=1)
    return (f'<a class="cta" href="/submit">Drop your call <small>for {tomorrow:%A %-d %b}</small>'
            f'<span class="arr">&rarr;</span></a>\n  ')


def _receipt(player: str, day: dt.date, c: dict) -> str:
    """What the player gets back: their own file described, never anyone else's."""
    lines = [f"<b>{html.escape(player)}</b>, your call for <b>{day:%A %-d %B}</b> is in.",
             f"{c['branches']} of {c['of']} branches, <b>{c['total']:,} cars</b>."]
    if c["missing"]:
        lines.append(f'<span style="color:var(--red-lo)">Missing {len(c["missing"])}: '
                     f'{html.escape(", ".join(c["missing"]))}. A missing branch scores as a full '
                     f'miss. Drop a corrected file before midnight to fix it.</span>')
    if c["dropped"]:
        lines.append(f'<span class="dim">Ignored, not scored: {html.escape(", ".join(c["dropped"]))}.</span>')
    lines.append('<span class="dim">You can drop again any time before midnight; the latest '
                 'file counts. Nobody sees anyone&rsquo;s numbers until all calls are in.</span>')
    return "<br>".join(lines)


def _drop_page(msg: str = "", ok: str = "", player: str = "") -> str:
    tomorrow = _today() + dt.timedelta(days=1)
    opts = "".join(f'<option value="{html.escape(p)}"{" selected" if p == player else ""}>'
                   f'{html.escape(p)}</option>' for p in S.ROUND1)
    dates = "".join(
        f'<option value="{d.isoformat()}"{" selected" if d == tomorrow else ""}>{d:%A %-d %B}</option>'
        for d in (tomorrow + dt.timedelta(days=i) for i in range(DAYS_AHEAD)))
    note = (f'<div class="note ok">{ok}</div>' if ok else
            f'<div class="note bad">{html.escape(msg)}</div>' if msg else "")
    style = re.search(r"<style>.*?</style>", S.TEMPLATE, re.S).group(0)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Drop your call</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap">
{style}
<style>
form{{display:grid;gap:15px;background:var(--card);border:1px solid var(--line);border-radius:13px;
  padding:22px}}
label{{font-size:.7rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);
  display:grid;gap:7px}}
select,input[type=password]{{font:inherit;font-size:1rem;padding:11px 13px;border:1px solid var(--line);
  border-radius:9px;background:var(--bg);color:var(--text)}}
.drop{{border:2px dashed var(--line);border-radius:12px;padding:30px 18px;text-align:center;
  color:var(--text3);cursor:pointer;transition:border-color .15s,background .15s;
  text-transform:none;letter-spacing:0;font-size:.95rem;font-weight:500;display:block}}
.drop.over,.drop:hover{{border-color:var(--red);background:rgba(220,38,38,.04)}}
.drop b{{color:var(--text)}} .drop input{{display:none}}
button{{font:inherit;font-weight:800;font-size:1rem;padding:13px;border:0;border-radius:10px;
  background:var(--ink2);color:#fff;cursor:pointer}}
button:hover{{background:var(--red)}}
.note{{margin-bottom:16px;line-height:1.6}} .note.ok{{border-left-color:var(--green)}}
.note.bad{{border-left-color:var(--red)}}
</style></head><body>
<header class="hero"><div class="w">
  <div class="ribbon"><i></i><i></i><i></i></div>
  <h1>Drop your <span>call</span></h1>
  <p class="dek">Pick your name, enter your PIN and drop tomorrow&rsquo;s CSV or workbook. You can
  replace it as often as you like until midnight. <b>Nobody sees any numbers until every call is in.</b></p>
</div></header>
<div class="w">
{note}
<form method="post" enctype="multipart/form-data">
  <label>Your name<select name="player" required>{opts}</select></label>
  <label>PIN<input type="password" name="pin" inputmode="numeric" autocomplete="off" required></label>
  <label>For<select name="date">{dates}</select></label>
  <label class="drop" id="drop"><input type="file" name="file" id="file" accept=".csv,.xlsx,.xlsm" required>
    <span id="lab"><b>Drop your file here</b><br>or click to choose. CSV or Excel.</span></label>
  <button type="submit">Send my call</button>
</form>
<p class="cap"><a href="/" style="color:var(--text2)">&larr; Back to the results</a></p>
</div>
<script>
const d=document.getElementById('drop'),f=document.getElementById('file'),l=document.getElementById('lab');
const show=()=>{{if(f.files.length)l.innerHTML='<b>'+f.files[0].name+'</b><br>ready to send';}};
f.addEventListener('change',show);
['dragenter','dragover'].forEach(e=>d.addEventListener(e,x=>{{x.preventDefault();d.classList.add('over');}}));
['dragleave','drop'].forEach(e=>d.addEventListener(e,x=>{{x.preventDefault();d.classList.remove('over');}}));
d.addEventListener('drop',x=>{{f.files=x.dataTransfer.files;show();}});
</script>
</body></html>"""
