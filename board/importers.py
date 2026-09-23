"""Turn a staffing workbook into a plain call CSV. Built for Brendan's pack, general enough for any.

His file is a full staffing pack: a Summary sheet with the call, plus "If dry" and "If worse"
scenario sheets, a redeploy plan, branch profiles, weather and calibration. Only the Summary
sheet is his actual call; the scenario sheets are bounds and must never be scored.

The Summary sheet groups branches under regional headers and carries a subtotal after each
block plus a grand total, so the branch rows have to be picked out rather than read straight
off. Everything that is not a branch is dropped by shape, not by row number, because the
number of branches in a region can change.

    python src/import_xlsx_call.py <file.xlsx> <target-date> [-o out.csv]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

SHEET = "Summary - Opsomming"
BRANCH_COL, CARS_COL = 0, 2
# a row that is a heading, a regional subtotal or the grand total, never a branch
NOT_A_BRANCH = re.compile(
    r"(total|totaal|branches\s*/\s*takke|alle takke|all branches|per persoon|per person"
    r"|branch\s*/\s*tak|teikenband|target band)", re.I)


def extract(path: Path, sheet: str = SHEET) -> pd.DataFrame:
    d = pd.read_excel(path, sheet_name=sheet, header=None)
    rows = []
    for i in range(len(d)):
        name, cars = d.iat[i, BRANCH_COL], d.iat[i, CARS_COL]
        if pd.isna(name) or pd.isna(cars):
            continue
        name = str(name).strip()
        if NOT_A_BRANCH.search(name):
            continue
        # a regional header is shouted; a branch name is not
        if name.isupper():
            continue
        try:
            n = float(cars)
        except (TypeError, ValueError):
            continue
        rows.append({"branch": name, "predicted_cars": int(round(n))})
    return pd.DataFrame(rows)


def xlsx_to_csv_text(path: Path, date: str) -> str:
    """Convert a workbook to the plain call CSV the board scores. Raises if there is no call
    sheet or the branch rows cannot be found, so a bad workbook is refused at the drop page."""
    df = extract(path)
    if df.empty:
        raise ValueError(f"no branch rows found on the sheet {SHEET!r}")
    if df.branch.duplicated().any():
        dup = ", ".join(sorted(df.branch[df.branch.duplicated()]))
        raise ValueError(f"the workbook lists these branches twice: {dup}")
    df.insert(0, "date", date)
    return df.to_csv(index=False)
