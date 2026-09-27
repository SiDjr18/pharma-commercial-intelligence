"""Source vs analytical layer reconciliation, and preservation of the source's internal logic.

Source-side controls come from raw cells (computed during the streamed read and stored in
_manifest.json). The `source` test re-reads the workbook independently.
"""
import datetime as dt
import hashlib
import math

import pytest

from conftest import skip_source
from pci_data.schema import SOURCE_PATH, SOURCE_SHEET

REL = 1e-9


def close(a, b, rel=REL, abs_=1e-9):
    return math.isclose(a or 0.0, b or 0.0, rel_tol=rel, abs_tol=abs_)


def one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def _measure_sql(entry):
    """SQL (sum, non-null count) in the analytical layer for one source column."""
    k, t = entry["kind"], entry["target"]
    if k == "monthly":
        return f"SELECT sum({t}), count({t}) FROM fact_pack_month WHERE period = DATE '{entry['period']}'"
    if k == "price":
        return f"SELECT sum(price_rs), count(price_rs) FROM pack_price_month WHERE period = DATE '{entry['period']}'"
    if k in ("snapshot", "ni", "split"):
        return f"SELECT sum({t}), count({t}) FROM pack_snapshot WHERE snapshot_year = {entry['snapshot_year']}"
    return f"SELECT sum({t}), count({t}) FROM pack"  # integer descriptive columns


# ---------------- row counts ----------------
def test_row_count(con, manifest, baseline):
    assert manifest["source"]["data_rows"] == baseline["source_rows"]
    assert manifest["source"]["blank_rows_skipped"] == 0
    assert one(con, "SELECT count(*) FROM pack") == manifest["source"]["data_rows"]
    assert one(con, "SELECT min(source_row) FROM pack") == 2  # Excel row 1 is the header
    assert one(con, "SELECT max(source_row) FROM pack") == baseline["source_rows"] + 1


# ---------------- every numeric source column ----------------
def test_every_numeric_source_column_reconciles(con, manifest):
    controls = manifest["source_column_controls"]
    by_source = {c["source"]: c for c in manifest["column_map"]}
    failures = []
    for col, ctl in controls.items():
        s, n = con.execute(_measure_sql(by_source[col])).fetchone()
        if n != ctl["non_null"] or not close(s, ctl["sum"]):
            failures.append((col, ctl, s, n))
    assert len(controls) == 6 + 180  # 6 integer descriptive columns + 180 measure columns
    assert not failures, failures[:5]


@pytest.mark.parametrize("measure", ["value_cr", "units_k", "qty_k"])
def test_grand_totals_by_measure(con, manifest, measure):
    ctl = manifest["source_column_controls"]
    src = math.fsum(ctl[c["source"]]["sum"] for c in manifest["column_map"]
                    if c["kind"] == "monthly" and c["target"] == measure)
    assert close(one(con, f"SELECT sum({measure}) FROM fact_pack_month"), src)


def test_period_totals(con, manifest):
    ctl = manifest["source_column_controls"]
    got = {str(p): (v, u, q) for p, v, u, q in con.execute(
        "SELECT period, sum(value_cr), sum(units_k), sum(qty_k) FROM fact_pack_month GROUP BY 1").fetchall()}
    assert len(got) == 36
    for c in manifest["column_map"]:
        if c["kind"] == "monthly":
            i = ["value_cr", "units_k", "qty_k"].index(c["target"])
            assert close(got[c["period"]][i], ctl[c["source"]]["sum"]), c["source"]


# ---------------- important dimension totals ----------------
DIM_TO_COL = {"SUPERGROUP": "supergroup", "ACUTE_CHRONIC": "acute_chronic", "INDIAN_MNC": "indian_mnc",
              "Plain/Combination": "plain_combination", "SHORT DESCRIPTION": "form_short_desc", "COMPANY": "company"}


@pytest.mark.parametrize("source_dim", list(DIM_TO_COL))
def test_dimension_totals(con, manifest, source_dim):
    col = DIM_TO_COL[source_dim]
    by_source = {c["source"]: c for c in manifest["column_map"]}
    controls = manifest["source_dimension_controls"][source_dim]
    measures = sorted({m for v in controls.values() for m in v})
    for m in measures:
        e = by_source[m]
        if e["kind"] == "monthly":
            sql = (f"SELECT coalesce(CAST(p.{col} AS VARCHAR), '__NULL__'), sum(f.{e['target']}) "
                   f"FROM fact_pack_month f JOIN pack p USING (pfc) WHERE f.period = DATE '{e['period']}' GROUP BY 1")
        else:
            sql = (f"SELECT coalesce(CAST(p.{col} AS VARCHAR), '__NULL__'), sum(s.{e['target']}) "
                   f"FROM pack_snapshot s JOIN pack p USING (pfc) WHERE s.snapshot_year = {e['snapshot_year']} GROUP BY 1")
        got = dict(con.execute(sql).fetchall())
        assert set(got) == set(controls), (source_dim, m)
        bad = [k for k in controls if not close(got[k], controls[k].get(m, 0.0), abs_=1e-6)]
        assert not bad, (source_dim, m, bad[:5])


# ---------------- source internal logic preserved ----------------
def _window(year, months):
    end = dt.date(year, 5, 1)
    y, m = end.year, end.month
    out = []
    for _ in range(months):
        out.append(dt.date(y, m, 1))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return min(out), max(out)


@pytest.mark.parametrize("year", [2022, 2023, 2024])
@pytest.mark.parametrize("kind,months", [("mat", 12), ("ytd", 5), ("month", 1)])
@pytest.mark.parametrize("measure", ["value", "units", "qty"])
def test_snapshot_equals_sum_of_months(con, year, kind, months, measure):
    fact_col = {"value": "value_cr", "units": "units_k", "qty": "qty_k"}[measure]
    lo, hi = _window(year, months)
    sql = f"""
      WITH f AS (SELECT pfc, sum({fact_col}) s FROM fact_pack_month
                 WHERE period BETWEEN DATE '{lo}' AND DATE '{hi}' GROUP BY pfc)
      SELECT count(*) FROM pack_snapshot p JOIN f USING (pfc)
      WHERE p.snapshot_year = {year}
        AND abs(p.{measure}_{kind} - f.s) > 1e-6 + 1e-9 * abs(f.s)"""
    assert one(con, sql) == 0


@pytest.mark.parametrize("m", ["value_cr", "units_k", "qty_k"])
def test_tsa_equals_sum_of_splits(con, m):
    assert one(con, f"""SELECT count(*) FROM pack_snapshot
        WHERE abs(tsa_mat_{m} - (ssa_mat_{m} + hsa_mat_{m} + dsa_mat_{m})) > 1e-6""") == 0


@pytest.mark.parametrize("split,mat,tol", [("value_cr", "value_mat", 1e-5), ("units_k", "units_mat", 1e-6),
                                           ("qty_k", "qty_mat", 0.0051)])
def test_tsa_equals_mat(con, split, mat, tol):
    """TSA-ST vs QTY MAT differs by source rounding up to 0.005 (documented)."""
    assert one(con, f"SELECT count(*) FROM pack_snapshot WHERE abs(tsa_mat_{split} - {mat}) > {tol}") == 0


def test_new_introduction_equals_mat_where_present(con):
    assert one(con, """SELECT count(*) FROM pack_snapshot
        WHERE ni_24m_mat_value_cr IS NOT NULL AND ni_24m_mat_value_cr <> value_mat""") == 0


def test_new_introduction_only_for_recent_launches(con):
    assert one(con, """SELECT count(*) FROM pack_snapshot s JOIN pack p USING (pfc)
        WHERE s.ni_24m_mat_value_cr IS NOT NULL
          AND (p.pack_launch_month IS NULL
               OR p.pack_launch_month < make_date(s.snapshot_year - 2, 6, 1)
               OR p.pack_launch_month > s.snapshot_month)""") == 0


def test_price_equals_value_over_units(con):
    """PR = 10,000 x value (Rs crore) / units ('000) = Rs per pack, wherever units > 0."""
    assert one(con, """SELECT count(*) FROM pack_price_month pr
        JOIN fact_pack_month f USING (pfc, period)
        WHERE f.units_k > 0 AND abs(pr.price_rs - 1e4 * f.value_cr / f.units_k) > 1e-3 * abs(pr.price_rs) + 1e-9""") == 0


# ---------------- independent re-read of the private source ----------------
@pytest.mark.source
@pytest.mark.skipif(skip_source(), reason="PCI_SKIP_SOURCE=1")
def test_independent_source_reread(con, manifest):
    if not SOURCE_PATH.exists():
        pytest.skip("private source not available on this machine")
    h = hashlib.sha256()
    with open(SOURCE_PATH, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    assert h.hexdigest() == manifest["source"]["sha256"], "source changed since build — rebuild required"

    import openpyxl
    wb = openpyxl.load_workbook(SOURCE_PATH, read_only=True, data_only=True)
    it = wb[SOURCE_SHEET].iter_rows(values_only=True)
    header = list(next(it))
    idx = [i for i, c in enumerate(manifest["column_map"]) if c["kind"] != "descriptive"]
    parts = {i: [] for i in idx}
    n = 0
    buf = {i: [] for i in idx}
    for row in it:
        if row is None or all(v is None for v in row):
            continue
        n += 1
        for i in idx:
            if row[i] is not None:
                buf[i].append(float(row[i]))
        if n % 10_000 == 0:
            for i in idx:
                parts[i].append(math.fsum(buf[i]))
                buf[i] = []
    wb.close()
    assert n == one(con, "SELECT count(*) FROM pack")
    by_pos = manifest["column_map"]
    bad = []
    for i in idx:
        src = math.fsum(parts[i] + [math.fsum(buf[i])])
        got = con.execute(_measure_sql(by_pos[i])).fetchone()[0]
        if not close(got, src):
            bad.append((header[i], src, got))
    assert not bad, bad[:5]
