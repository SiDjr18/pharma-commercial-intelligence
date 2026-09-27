"""Independent Python implementation of the M4 time logic (no SQL).

Bases (docs/SQL_ANALYTICS.md):
  MONTH : anchor month
  YTD   : Calendar YTD = January .. anchor month of the anchor's calendar year (NOT Indian FY)
  MAT   : 12 months ending at the anchor month
Comparison window = the same window shifted back 12 months.
A window is complete only if it starts on/after the first available month (data are gap-free).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

BASES = ("MONTH", "YTD", "MAT")
BASIS_LABELS = {"MONTH": "Month", "YTD": "Calendar YTD", "MAT": "MAT (moving annual total)"}


def month_index(d: dt.date) -> int:
    return d.year * 12 + (d.month - 1)


def from_index(i: int) -> dt.date:
    return dt.date(i // 12, i % 12 + 1, 1)


def add_months(d: dt.date, n: int) -> dt.date:
    return from_index(month_index(d) + n)


@dataclass(frozen=True)
class Window:
    anchor: dt.date
    basis: str
    basis_label: str
    cur_start: dt.date
    cur_end: dt.date
    prior_start: dt.date
    prior_end: dt.date
    n_months: int
    current_complete: bool
    prior_complete: bool


def current_start(anchor: dt.date, basis: str) -> dt.date:
    if basis == "MONTH":
        return anchor
    if basis == "YTD":
        return dt.date(anchor.year, 1, 1)
    if basis == "MAT":
        return add_months(anchor, -11)
    raise ValueError(f"basis must be one of {BASES}")


def comparison_shift(d: dt.date) -> dt.date:
    """Comparison window = same window 12 months earlier."""
    return add_months(d, -12)


def make_window(anchor: dt.date, basis: str, first_period: dt.date) -> Window:
    basis = basis.upper()
    anchor = anchor.replace(day=1)
    start = current_start(anchor, basis)
    p_start, p_end = comparison_shift(start), comparison_shift(anchor)
    return Window(anchor=anchor, basis=basis, basis_label=BASIS_LABELS[basis],
                  cur_start=start, cur_end=anchor, prior_start=p_start, prior_end=p_end,
                  n_months=month_index(anchor) - month_index(start) + 1,
                  current_complete=start >= first_period, prior_complete=p_start >= first_period)


def ytd_available(month: dt.date, first_period: dt.date) -> bool:
    """Calendar YTD at `month` needs January of that year to be in the data."""
    return dt.date(month.year, 1, 1) >= first_period
