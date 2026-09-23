"""Round-trip the Redis backend against a real store. Run once Redis settings are exported.

Uses a date far in the future so it can never collide with a real call, and puts back the
live and state keys exactly as it found them, so running it against the production store
leaves nothing behind.

    KV_REST_API_URL=... KV_REST_API_TOKEN=... .venv/bin/python tests/test_store_redis.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from board import store  # noqa: E402

if store.BACKEND != "redis":
    sys.exit("Redis settings are not exported; nothing to test.")

TEST_DAY = dt.date(2099, 1, 1)
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


saved_live = store._r("GET", "live")
saved_state = store._r("GET", "state")
try:
    csv = "date,branch,predicted_cars\n2099-01-01,Tyger Valley,55\n2099-01-01,Grove,48\n"
    store.save_call(TEST_DAY, "Owen", csv)
    store.save_call(TEST_DAY, "Tristan", csv.replace("55", "60"))
    paths = store.call_paths(TEST_DAY)
    check("both calls come back", set(paths) == {"Owen", "Tristan"}, paths)
    check("and their contents are intact", paths["Owen"].read_text() == csv)

    store.save_call(TEST_DAY, "Owen", csv.replace("55", "57"))
    store.begin_request()
    check("a second drop replaces the first", "57" in store.call_paths(TEST_DAY)["Owen"].read_text())

    bundle = {"date": TEST_DAY.isoformat(), "branch_actual": {"Tyger Valley": 61}, "sizes": {}, "context": {}}
    store.save_actuals(TEST_DAY, bundle)
    check("a closed day comes back", store.load_actuals(TEST_DAY) == json.loads(json.dumps(bundle, sort_keys=True)))
    check("and is listed among closed days", TEST_DAY.isoformat() in store.actuals_days())
    check("an unknown day returns nothing", store.load_actuals(dt.date(2098, 1, 1)) is None)

    store.save_live({"date": "2099-01-01", "at": "10:00", "branch_so_far": {}, "curve": {}})
    check("the running count round-trips", store.load_live()["at"] == "10:00")
finally:
    store._r("DEL", f"calls:{TEST_DAY.isoformat()}")
    store._r("HDEL", "actuals", TEST_DAY.isoformat())
    for key, val in (("live", saved_live), ("state", saved_state)):
        store._r("SET", key, val) if val is not None else store._r("DEL", key)
    left = []
    if store._r("EXISTS", f"calls:{TEST_DAY.isoformat()}"):
        left.append(f"calls:{TEST_DAY.isoformat()}")
    if store._r("HEXISTS", "actuals", TEST_DAY.isoformat()):
        left.append("actuals field for the test day")
    check("every test key cleaned up", not left, left)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
