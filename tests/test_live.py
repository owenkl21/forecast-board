"""End-to-end checks against a running local site. Run with the site up on :8000."""
import json, os, re, sys, urllib.request, urllib.error, uuid, datetime as dt
from pathlib import Path
env = {}
for ln in Path(".env").read_text().splitlines():
    k, _, v = ln.partition("="); env[k] = v.strip().strip("'")
PINS = json.loads(env["BOARD_PINS"]); TOKEN = env["BOARD_HUB_TOKEN"]
BASE = "http://127.0.0.1:8000"
SAST = dt.timezone(dt.timedelta(hours=2)); today = dt.datetime.now(SAST).date()
tomorrow = today + dt.timedelta(days=1)
CALL = Path("../forecast/comp/inbox/2026-09-22/Owen.csv").read_bytes()
passed = failed = 0

def req(method, path, data=None, headers=None):
    r = urllib.request.Request(BASE+path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()

def multipart(fields, fname, content):
    b = uuid.uuid4().hex; out = []
    for k, v in fields.items():
        out += [f"--{b}", f'Content-Disposition: form-data; name="{k}"', "", v]
    out += [f"--{b}", f'Content-Disposition: form-data; name="file"; filename="{fname}"',
            "Content-Type: text/csv", ""]
    body = ("\r\n".join(out) + "\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}

def check(name, cond, detail=""):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {name}")
    else: failed += 1; print(f"  FAIL  {name}  {detail}")

print("THE HAPPY PATH")
s, b = req("GET", "/health"); check("site answers", s == 200 and "Brendan" in b)
s, b = req("GET", "/"); check("results page renders", s == 200 and "Forecast" in b)
check("Monday and Tuesday are scored on it", "Mon 21" in b and "Tue 22" in b)
s, b = req("GET", "/submit"); check("drop page renders", s == 200 and "Drop your" in b)
body, h = multipart({"player":"Owen","pin":PINS["Owen"],"date":tomorrow.isoformat()}, "c.csv", CALL)
s, b = req("POST", "/submit", body, h)
check("a real call is accepted with a receipt", "is in" in b and "44 of 44" in b, b[-300:])
check("the file actually landed", Path(f"data/inbox/{tomorrow}/Owen.csv").exists())

print("\nTRYING TO BREAK OR GAME IT")
body, h = multipart({"player":"Tristan","pin":"0000","date":tomorrow.isoformat()}, "c.csv", CALL)
s, b = req("POST", "/submit", body, h)
check("a wrong PIN is refused", "does not match" in b)
check("and nothing was saved for Tristan", not Path(f"data/inbox/{tomorrow}/Tristan.csv").exists())
body, h = multipart({"player":"Owen","pin":PINS["Owen"],"date":today.isoformat()}, "c.csv", CALL)
s, b = req("POST", "/submit", body, h)
check("a call for a day already under way is locked", "locked" in b)
body, h = multipart({"player":"Nobody","pin":"1","date":tomorrow.isoformat()}, "c.csv", CALL)
s, b = req("POST", "/submit", body, h); check("a name not on the roster is refused", "not on the roster" in b)
body, h = multipart({"player":"Owen","pin":PINS["Owen"],"date":tomorrow.isoformat()}, "junk.csv", b"hello,world\n1,2\n")
s, b = req("POST", "/submit", body, h); check("a junk file is refused", "could not be read" in b or "wrong file" in b, b[-200:])
s, b = req("POST", "/api/hub/actuals", json.dumps({"date":"2026-09-23"}).encode(),
           {"Content-Type":"application/json","Authorization":"Bearer wrong"})
check("the hub endpoint refuses a bad token", s == 401)
s, b = req("POST", "/api/hub/call", json.dumps({"date":tomorrow.isoformat(),"csv":"x"}).encode(),
           {"Content-Type":"application/json"})
check("the hub endpoint refuses no token at all", s == 401)

print("\nTHE SEAL: one call in for tomorrow must reveal nothing")
s, b = req("GET", "/")
owen_total = f"{sum(int(r.split(',')[3]) for r in CALL.decode().strip().splitlines()[1:] if r.split(',')[2] not in ('De Drift','Newinbosch')):,}"
check("the board says it is sealed", "stay sealed" in b)
nxt = b[b.find("<h2>Next call"):b.find("<h2>Every branch")]
check("the Next call panel does not print the call's total", owen_total not in nxt, nxt[:200])
tabs = re.findall(r'<label for="mx\\d+">([^<]*)</label>', b)
check("and no tab exists for the sealed day", not any(tomorrow.strftime("%a %-d") in t for t in tabs), tabs)
for path in (f"/data/inbox/{tomorrow}/Owen.csv", f"/inbox/{tomorrow}/Owen.csv", "/api/hub/call",
             "/data/state.json", "/.env", "/docs", "/openapi.json"):
    s, _ = req("GET", path)
    check(f"nothing served at {path}", s in (404, 405), f"got {s}")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
