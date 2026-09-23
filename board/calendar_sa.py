"""South African public holidays and the trading calendar.

Holiday dates are computed, not hard-coded, so the model keeps working in future
years without anyone remembering to extend a list. Easter drives four of them.

Each holiday is kept as its OWN category rather than a single "is a holiday"
flag. Measured against a site x weekday x month baseline, the real effects span
76 percentage points -- Freedom Day trades +32% while Women's Day trades -44% --
so collapsing them into one multiplier is guaranteed to be wrong in both
directions at once.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

# Days on which most of the estate is shut. These are closures, not weak demand:
# on 26 December only 3-12 of 43 sites trade at all. A demand model must never be
# asked to predict a number for a day the gates are locked.
CLOSURE_DAYS = {(1, 1), (12, 25), (12, 26)}


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


@lru_cache(maxsize=None)
def holidays(y0: int = 2023, y1: int = 2030) -> dict[dt.date, str]:
    out: dict[dt.date, str] = {}
    for y in range(y0, y1 + 1):
        E = easter(y)
        for d, name in [
            (dt.date(y, 1, 1), "new_year"),
            (dt.date(y, 3, 21), "human_rights"),
            (E - dt.timedelta(days=2), "good_friday"),
            (E + dt.timedelta(days=1), "family_day"),
            (dt.date(y, 4, 27), "freedom_day"),
            (dt.date(y, 5, 1), "workers_day"),
            (dt.date(y, 6, 16), "youth_day"),
            (dt.date(y, 8, 9), "womens_day"),
            (dt.date(y, 9, 24), "heritage_day"),
            (dt.date(y, 12, 16), "reconciliation"),
            (dt.date(y, 12, 25), "christmas"),
            (dt.date(y, 12, 26), "goodwill"),
        ]:
            out[d] = name
            # Public Holidays Act: a holiday falling on a Sunday moves to Monday.
            if d.weekday() == 6:
                out[d + dt.timedelta(days=1)] = name + "_obs"
    out[dt.date(2024, 5, 29)] = "election_day"  # one-off national election
    return out


def holiday_name(d: dt.date) -> str:
    return holidays().get(d, "none")


def is_probable_closure(d: dt.date) -> bool:
    return (d.month, d.day) in CLOSURE_DAYS
