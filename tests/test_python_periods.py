"""M5: independent Python period logic (no data needed) + parity with SQL period_basis."""
import datetime as dt

import pytest

from pci_analytics import periods

FIRST = dt.date(2021, 6, 1)
D = dt.date


@pytest.mark.parametrize("anchor,basis,start,n", [
    (D(2024, 5, 1), "MONTH", D(2024, 5, 1), 1),
    (D(2024, 5, 1), "YTD", D(2024, 1, 1), 5),
    (D(2024, 1, 1), "YTD", D(2024, 1, 1), 1),
    (D(2023, 12, 1), "YTD", D(2023, 1, 1), 12),
    (D(2024, 5, 1), "MAT", D(2023, 6, 1), 12),
    (D(2023, 2, 1), "MAT", D(2022, 3, 1), 12),   # MAT crossing a year boundary
])
def test_current_window(anchor, basis, start, n):
    w = periods.make_window(anchor, basis, FIRST)
    assert (w.cur_start, w.cur_end, w.n_months) == (start, anchor, n)


def test_comparison_is_same_window_one_year_earlier():
    w = periods.make_window(D(2024, 3, 1), "YTD", FIRST)
    assert (w.prior_start, w.prior_end) == (D(2023, 1, 1), D(2023, 3, 1))
    w = periods.make_window(D(2024, 5, 1), "MAT", FIRST)
    assert (w.prior_start, w.prior_end) == (D(2022, 6, 1), D(2023, 5, 1))


def test_ytd_is_calendar_not_indian_fy():
    for m in range(1, 13):
        w = periods.make_window(D(2023, m, 1), "YTD", FIRST)
        assert w.cur_start == D(2023, 1, 1) and w.basis_label == "Calendar YTD"
    assert periods.make_window(D(2023, 3, 1), "YTD", FIRST).n_months == 3   # not 12 (Apr-Mar)


@pytest.mark.parametrize("anchor,basis,cur_ok,prior_ok", [
    (D(2021, 6, 1), "MONTH", True, False),     # first available month
    (D(2022, 5, 1), "MONTH", True, False),
    (D(2022, 6, 1), "MONTH", True, True),      # first month with YoY
    (D(2021, 12, 1), "YTD", False, False),     # partial 2021 YTD is never computed
    (D(2022, 1, 1), "YTD", True, False),       # first valid YTD
    (D(2023, 1, 1), "YTD", True, True),        # first YTD with growth
    (D(2022, 4, 1), "MAT", False, False),
    (D(2022, 5, 1), "MAT", True, False),       # first valid MAT
    (D(2023, 5, 1), "MAT", True, True),        # first MAT with growth
])
def test_completeness(anchor, basis, cur_ok, prior_ok):
    w = periods.make_window(anchor, basis, FIRST)
    assert (w.current_complete, w.prior_complete) == (cur_ok, prior_ok)


def test_month_arithmetic():
    assert periods.add_months(D(2024, 1, 1), -1) == D(2023, 12, 1)
    assert periods.add_months(D(2023, 12, 1), 1) == D(2024, 1, 1)
    assert periods.add_months(D(2024, 5, 1), -11) == D(2023, 6, 1)
    assert periods.from_index(periods.month_index(D(2022, 7, 1))) == D(2022, 7, 1)


def test_anchor_normalised_and_basis_case_insensitive():
    w = periods.make_window(D(2024, 5, 17), "mat", FIRST)
    assert w.anchor == D(2024, 5, 1) and w.basis == "MAT"
    with pytest.raises(ValueError):
        periods.make_window(D(2024, 5, 1), "FY", FIRST)


def test_ytd_available():
    assert not periods.ytd_available(D(2021, 12, 1), FIRST)
    assert periods.ytd_available(D(2022, 1, 1), FIRST)


def test_parity_with_sql_period_basis(con, py_engine):
    """All 36 anchors x 3 bases: every window field identical to SQL (exact)."""
    rows = con.execute("SELECT anchor, basis, basis_label, cur_start, cur_end, prior_start, prior_end, n_months, "
                       "current_complete, prior_complete FROM period_basis").fetchall()
    assert len(rows) == 108
    for r in rows:
        w = periods.make_window(r[0], r[1], py_engine.first_period)
        assert (w.anchor, w.basis, w.basis_label, w.cur_start, w.cur_end, w.prior_start, w.prior_end, w.n_months,
                w.current_complete, w.prior_complete) == tuple(r), r[:2]
