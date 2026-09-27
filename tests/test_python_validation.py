"""Independent Python (pyarrow) re-implementation of the core M4 metrics, compared with SQL.

No DuckDB is used on the Python side: Parquet is read with pyarrow and aggregated with
pyarrow.compute / plain Python. Tolerance: |sql - py| <= 1e-9 + 1e-9 * |py| for sums,
1e-9 for percentages.
"""
import datetime as dt
import math
from collections import defaultdict

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from pci_data.schema import PROCESSED_DIR


def close(a, b, rel=1e-9, abs_=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


@pytest.fixture(scope="session")
def fact(manifest):
    return pq.read_table(PROCESSED_DIR / "fact_pack_month.parquet")


@pytest.fixture(scope="session")
def pack(manifest):
    t = pq.read_table(PROCESSED_DIR / "pack.parquet").to_pylist()
    return {r["pfc"]: r for r in t}


def shift(d, months):
    m = d.year * 12 + d.month - 1 + months
    return dt.date(m // 12, m % 12 + 1, 1)


def window(anchor, basis):
    start = {"MONTH": anchor, "YTD": anchor.replace(month=1), "MAT": shift(anchor, -11)}[basis]
    return start, anchor


def pack_sums(fact, start, end):
    """pfc -> (value, units) summed over [start, end] with pyarrow."""
    f = fact.filter(pc.and_(pc.greater_equal(fact["period"], start), pc.less_equal(fact["period"], end)))
    g = f.group_by("pfc").aggregate([("value_cr", "sum"), ("units_k", "sum")]).to_pydict()
    return {k: (v, u) for k, v, u in zip(g["pfc"], g["value_cr_sum"], g["units_k_sum"])}


def py_entity(fact, pack, keyfn, anchor, basis):
    s, e = window(anchor, basis)
    cur, pri = pack_sums(fact, s, e), pack_sums(fact, shift(s, -12), shift(e, -12))
    first = dt.date(2021, 6, 1)
    prior_ok = shift(s, -12) >= first
    out = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for pfc, attrs in pack.items():
        k = keyfn(attrs)
        o = out[k]
        o[0] += cur[pfc][0]
        o[2] += cur[pfc][1]
        if prior_ok:
            o[1] += pri[pfc][0]
            o[3] += pri[pfc][1]
    res = {}
    tv = sum(o[0] for o in out.values())
    tp = sum(o[1] for o in out.values()) if prior_ok else None
    for k, (vc, vp, uc, up) in out.items():
        vp = vp if prior_ok else None
        res[k] = {"value_cur": vc, "value_prior": vp, "units_cur": uc,
                  "value_growth_pct": (vc / vp * 100 - 100) if vp else None,
                  "value_share_pct": vc / tv * 100 if tv > 0 else None,
                  "contribution_to_growth_pp": (vc - vp) / tp * 100 if tp else None}
    ranked = sorted(res, key=lambda k: (-round(res[k]["value_cur"], 9), k.encode()))  # same rule as SQL
    for i, k in enumerate(ranked, 1):
        res[k]["rank_value"] = i
    return res


def sql_entity(con, etype, anchor, basis):
    cur = con.execute("""SELECT entity_key, value_cur, value_prior, units_cur, value_growth_pct, value_share_pct,
                                contribution_to_growth_pp, rank_value FROM entity_period(?, ?, ?)""", [etype, anchor, basis])
    cols = [d[0] for d in cur.description]
    return {r[0]: dict(zip(cols[1:], r[1:])) for r in cur.fetchall()}


def compare(py, sql):
    assert set(py) == set(sql)
    bad = []
    for k in py:
        for m in ("value_cur", "value_prior", "units_cur", "value_growth_pct", "value_share_pct",
                  "contribution_to_growth_pp", "rank_value"):
            if not close(py[k][m], sql[k][m]):
                bad.append((k, m, py[k][m], sql[k][m]))
    assert not bad, bad[:5]


KEYFNS = {
    "total": lambda p: "TOTAL",
    "supergroup": lambda p: p["supergroup"],
    "subgroup": lambda p: p["subgroup"],
    "molecule": lambda p: p["molecule_desc"] or "(UNCLASSIFIED)",
    "company": lambda p: p["company"],
    "product": lambda p: str(p["prod_code"]),
    "acute_chronic": lambda p: p["acute_chronic"],
    "indian_mnc": lambda p: p["indian_mnc"],
    "plain_combination": lambda p: p["plain_combination"] or "(UNCLASSIFIED)",
    "dosage_form": lambda p: p["form_short_desc"],
}


def test_monthly_totals(con, fact):
    g = fact.group_by("period").aggregate([("value_cr", "sum"), ("units_k", "sum")]).to_pydict()
    py = {p: (v, u) for p, v, u in zip(g["period"], g["value_cr_sum"], g["units_k_sum"])}
    sql = {r[0]: (r[1], r[2]) for r in con.execute("SELECT period, value_cr, units_k FROM market_trend('total','TOTAL')").fetchall()}
    assert set(py) == set(sql) and len(py) == 36
    assert all(close(py[p][0], sql[p][0]) and close(py[p][1], sql[p][1]) for p in py)


@pytest.mark.parametrize("etype,anchor,basis", [
    ("total", dt.date(2024, 5, 1), "MAT"),
    ("total", dt.date(2023, 12, 1), "YTD"),
    ("total", dt.date(2022, 5, 1), "MAT"),          # prior unavailable
    ("supergroup", dt.date(2024, 5, 1), "MAT"),
    ("supergroup", dt.date(2024, 2, 1), "YTD"),
    ("subgroup", dt.date(2024, 3, 1), "YTD"),
    ("molecule", dt.date(2023, 5, 1), "MAT"),
    ("company", dt.date(2024, 5, 1), "MAT"),
    ("product", dt.date(2024, 5, 1), "MAT"),
    ("product", dt.date(2023, 11, 1), "MONTH"),
    ("acute_chronic", dt.date(2024, 5, 1), "YTD"),
    ("indian_mnc", dt.date(2024, 4, 1), "MONTH"),
    ("plain_combination", dt.date(2024, 5, 1), "MAT"),
    ("dosage_form", dt.date(2023, 5, 1), "MAT"),
])
def test_sql_matches_python(con, fact, pack, etype, anchor, basis):
    compare(py_entity(fact, pack, KEYFNS[etype], anchor, basis), sql_entity(con, etype, anchor, basis))


def test_market_assignment_matches_python(con, pack):
    py = defaultdict(int)
    for p in pack.values():
        py[p["subgroup"]] += 1
    sql = dict(con.execute("SELECT entity_key, count(*) FROM pack_entity WHERE entity_type='subgroup' GROUP BY 1").fetchall())
    assert dict(py) == sql


def test_therapy_rollup_matches_python(con, fact, pack):
    sub = py_entity(fact, pack, KEYFNS["subgroup"], dt.date(2024, 5, 1), "MAT")
    sg_of = {p["subgroup"]: p["supergroup"] for p in pack.values()}
    roll = defaultdict(float)
    for k, v in sub.items():
        roll[sg_of[k]] += v["value_cur"]
    sql = dict(con.execute("SELECT entity_key, value_cur FROM therapy_performance('supergroup','2024-05-01','MAT')").fetchall())
    assert set(roll) == set(sql) and all(close(roll[k], sql[k]) for k in roll)


def test_brand_share_within_market_matches_python(con, fact, pack):
    sg = con.execute("SELECT entity_key FROM market_performance('subgroup','2024-05-01','MAT') WHERE rank_value=3").fetchone()[0]
    in_mkt = {k: v for k, v in pack.items() if v["subgroup"] == sg}
    py = py_entity(fact, in_mkt, KEYFNS["product"], dt.date(2024, 5, 1), "MAT")
    cur = con.execute("""SELECT entity_key, value_cur, value_prior, units_cur, value_growth_pct, value_share_pct,
                                contribution_to_growth_pp, rank_value
                         FROM product_performance('2024-05-01','MAT','subgroup',?)""", [sg])
    cols = [d[0] for d in cur.description]
    compare(py, {r[0]: dict(zip(cols[1:], r[1:])) for r in cur.fetchall()})
