"""M5: SQL layer vs independent Python engine — validation matrix, edge cases, API parity, fault injection."""
import datetime as dt
import math

import pytest

from pci_analytics import AnalyticsError, CommercialAnalytics, metrics, periods, rankings
from pci_analytics import validation as V
from pci_analytics.domains import PyCommercialAnalytics

A = dt.date(2024, 5, 1)


# ======================= validation matrix =======================
@pytest.mark.parametrize("case", V.PERIOD_MATRIX, ids=lambda c: f"{c[1]}-{c[3]}-{c[2]}-{c[4]}")
def test_period_matrix(con, py_engine, case):
    domain, etype, anchor, basis, st, sk = case
    r = V.crosscheck_period(con, py_engine, etype, anchor, basis, st, V.resolve(py_engine, sk))
    assert r.n_rows_py > 0
    assert r.passed, (r.summary(), r.mismatches[:5])


def test_trend_matrix(con, py_engine):
    results = V.run_matrix(con, py_engine, period_matrix=[])
    assert len(results) == len(V.TREND_MATRIX)
    failed = [r.summary() for r in results if not r.passed]
    assert not failed, failed


def test_matrix_coverage():
    """Documented coverage: every domain, every basis, scoped and unscoped, first-valid periods."""
    types = {c[1] for c in V.PERIOD_MATRIX}
    assert {"total", "supergroup", "therapy_group", "subgroup", "molecule", "company", "manufacturer", "product",
            "product_subgroup", "acute_chronic", "indian_mnc", "plain_combination", "molecule_count", "dosage_form",
            "nfc1"} <= types
    assert {c[3] for c in V.PERIOD_MATRIX} == {"MONTH", "YTD", "MAT"}
    assert {c[4] for c in V.PERIOD_MATRIX} >= {"total", "subgroup", "supergroup", "molecule", "company"}
    assert {"2021-06-01", "2022-01-01", "2022-05-01"} <= {c[2] for c in V.PERIOD_MATRIX}
    assert set(V.PERIOD_STRUCTURAL + V.PERIOD_NUMERIC) >= {
        "value_cur", "units_cur", "qty_cur", "value_prior", "value_abs_chg", "value_growth_pct", "value_share_pct",
        "units_share_pct", "contribution_to_growth_pp", "evolution_index", "rank_value", "rank_growth"}


# ======================= edge cases (real data, both engines) =======================
def _both(con, py_engine, etype, anchor, basis, st="total", sk="TOTAL"):
    sql = {r["entity_key"]: r for r in V._sql_rows(con, "SELECT * FROM entity_period(?, ?, ?, ?, ?)",
                                                    [etype, anchor, basis, st, sk])}
    py = {r["entity_key"]: r for r in metrics.entity_period(py_engine, etype, anchor, basis, st, sk)}
    return sql, py


def test_zero_and_missing_prior(con, py_engine):
    sql, py = _both(con, py_engine, "product", A, "MAT")
    for status in ("prior_zero", "no_sales"):
        ks = {k for k, r in py.items() if r["value_growth_status"] == status}
        assert ks and ks == {k for k, r in sql.items() if r["value_growth_status"] == status}
        assert all(py[k]["value_growth_pct"] is None and sql[k]["value_growth_pct"] is None for k in ks)
    sql, py = _both(con, py_engine, "total", dt.date(2022, 5, 1), "MAT")
    assert py["TOTAL"]["value_prior"] is None and sql["TOTAL"]["value_prior"] is None


@pytest.mark.parametrize("anchor,basis", [(dt.date(2021, 5, 1), "MONTH"), (dt.date(2021, 12, 1), "YTD"),
                                          (dt.date(2022, 4, 1), "MAT"), (dt.date(2024, 6, 1), "MONTH")])
def test_unavailable_windows_empty_in_both(con, py_engine, anchor, basis):
    sql, py = _both(con, py_engine, "total", anchor, basis)
    assert sql == {} and py == {}


def test_one_product_market(con, py_engine):
    one = [k for k, n in _product_counts(py_engine).items() if n == 1]
    assert len(one) > 0
    sql, py = _both(con, py_engine, "product", A, "MAT", "subgroup", one[0])
    assert len(py) == 1 and V.compare_rows("one-product", list(sql.values()), list(py.values()), ["entity_key"],
                                           V.PERIOD_STRUCTURAL, V.PERIOD_NUMERIC).passed
    r = next(iter(py.values()))
    assert r["rank_value"] == 1 and (r["value_share_pct"] in (100.0, None))


def _product_counts(engine):
    s = {}
    for p in engine.packs:
        s.setdefault(p["subgroup"], set()).add(p["prod_code"])
    return {k: len(v) for k, v in s.items()}


def test_zero_market_denominator(con, py_engine):
    zero = [k for k, r in _both(con, py_engine, "subgroup", A, "MAT")[1].items() if r["value_cur"] == 0]
    assert zero
    sql, py = _both(con, py_engine, "product", A, "MAT", "subgroup", zero[0])
    assert all(r["value_share_pct"] is None for r in py.values())
    assert V.compare_rows("zero-mkt", list(sql.values()), list(py.values()), ["entity_key"],
                          V.PERIOD_STRUCTURAL, V.PERIOD_NUMERIC).passed


def test_duplicated_display_labels(py_engine):
    rows = metrics.entity_period(py_engine, "product", A, "MAT")
    labels = [r["entity_label"] for r in rows]
    assert len(set(labels)) < len(labels) and len({r["entity_key"] for r in rows}) == len(rows) == 60_079


def test_product_spanning_subgroups_independent(py_engine):
    prod = {r["entity_key"]: r["value_cur"] for r in metrics.entity_period(py_engine, "product", A, "MAT")}
    parts = {}
    idx_to_prod = {p["index_desc"]: str(p["prod_code"]) for p in py_engine.packs}
    for r in metrics.entity_period(py_engine, "product_subgroup", A, "MAT"):
        parts.setdefault(idx_to_prod[r["entity_key"]], []).append(r["value_cur"])
    multi = [k for k, v in parts.items() if len(v) > 1]
    assert len(multi) == 4_519
    assert all(math.isclose(prod[k], math.fsum(parts[k]), rel_tol=1e-12, abs_tol=1e-15) for k in prod)


def test_unclassified_and_combination(con, py_engine):
    sql, py = _both(con, py_engine, "molecule", A, "MAT")
    assert py["(UNCLASSIFIED)"]["n_packs"] == sql["(UNCLASSIFIED)"]["n_packs"] == 1
    pc_ = {r["entity_key"]: r["value_cur"] for r in metrics.entity_period(py_engine, "plain_combination", A, "MAT")}
    mc = {r["entity_key"]: r["value_cur"] for r in metrics.entity_period(py_engine, "molecule_count", A, "MAT")}
    # confirmed source relationship: Plain <=> 1 molecule; Combination <=> 2..10 molecules
    assert math.isclose(pc_["Plain"], mc["1"], rel_tol=1e-12)
    assert math.isclose(pc_["Combination"], math.fsum(v for k, v in mc.items() if k not in ("1", "(UNCLASSIFIED)")),
                        rel_tol=1e-12)


def test_sql_run_to_run_noise_is_within_policy_and_ranks_stable(con):
    q = "SELECT * FROM entity_period('subgroup', DATE '2024-05-01', 'MAT')"
    a, b = V._sql_rows(con, q, []), V._sql_rows(con, q, [])
    r = V.compare_rows("sql-vs-sql", a, b, ["entity_key"], V.PERIOD_STRUCTURAL, V.PERIOD_NUMERIC)
    assert r.passed and r.max_rel_diff < 1e-10


# ======================= API parity =======================
@pytest.fixture(scope="module")
def apis(con, py_engine):
    return CommercialAnalytics(con), PyCommercialAnalytics(py_engine)


def _parity(sql_env, py_env, key, structural, numeric, extra=()):
    for f in ("function", "filters", "period", "units", "row_count", "total_rows", "caveats"):
        assert sql_env[f] == py_env[f], f
    iso = lambda rows: [{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()} for r in rows]  # noqa: E731
    r = V.compare_rows(sql_env["function"], iso(sql_env["rows"]), iso(py_env["rows"]), key,
                       list(structural) + list(extra), numeric)
    assert r.passed, r.mismatches[:5]
    assert [x[key[0]] for x in sql_env["rows"]] == [x[key[0]] for x in py_env["rows"]]   # same order


def test_api_parity_all_functions(apis, py_engine):
    s, p = apis
    sg = V.resolve(py_engine, "@rank:subgroup:2")
    pc_ = V.resolve(py_engine, "@rank:product:3")
    per = (["entity_key"], V.PERIOD_STRUCTURAL, V.PERIOD_NUMERIC)
    tr = (["period"], V.TREND_STRUCTURAL, V.TREND_NUMERIC)
    _parity(s.get_market_performance("subgroup", "2024-05-01", "MAT", top_n=50),
            p.get_market_performance("subgroup", "2024-05-01", "MAT", top_n=50), *per)
    _parity(s.get_market_trends("subgroup", sg), p.get_market_trends("subgroup", sg), *tr)
    _parity(s.get_brand_performance("2024-03-01", "YTD", "subgroup", sg, top_n=None),
            p.get_brand_performance("2024-03-01", "YTD", "subgroup", sg, top_n=None), *per,
            extra=("brand", "company", "manufacturer_code"))
    top_co = s.get_brand_performance(top_n=1)["rows"][0]["company"]
    _parity(s.get_brand_performance(company=top_co, top_n=25), p.get_brand_performance(company=top_co, top_n=25),
            *per, extra=("brand", "company"))
    _parity(s.get_brand_growth(pc_), p.get_brand_growth(pc_), *tr)
    _parity(s.get_brand_share(pc_, "total", "TOTAL", "2023-11-01", "MONTH"),
            p.get_brand_share(pc_, "total", "TOTAL", "2023-11-01", "MONTH"), *per)
    _parity(s.get_company_performance("2024-05-01", "MAT", "subgroup", sg),
            p.get_company_performance("2024-05-01", "MAT", "subgroup", sg), *per, extra=("indian_mnc",))
    _parity(s.get_therapy_performance("subgroup", within_supergroup="CARDIAC"),
            p.get_therapy_performance("subgroup", within_supergroup="CARDIAC"), *per,
            extra=("therapy_group", "supergroup", "acute_chronic"))
    _parity(s.get_therapy_performance("therapy_group", "2023-05-01", "MAT"),
            p.get_therapy_performance("therapy_group", "2023-05-01", "MAT"), *per, extra=("supergroup",))
    _parity(s.get_segment_analysis("dosage_form", "2024-05-01", "MONTH", "supergroup", "CARDIAC"),
            p.get_segment_analysis("dosage_form", "2024-05-01", "MONTH", "supergroup", "CARDIAC"), *per)
    fs, fp = s.find_products("cal", limit=500), p.find_products("cal", limit=500)
    assert [r["prod_code"] for r in fs["rows"]] == [r["prod_code"] for r in fp["rows"]] and fs["row_count"] > 0


def test_find_products_treats_wildcards_literally(apis, py_engine):
    """Regression (found in M5 parity): SQL ILIKE treated '_' / '%' in user input as wildcards."""
    s, p = apis
    brand = next(p_["brand"] for p_ in py_engine.packs if len(p_["brand"]) >= 5 and p_["brand"].isalpha())
    pattern = brand[:2] + "_" + brand[3:5]
    assert [r["prod_code"] for r in s.find_products(pattern)["rows"]] == \
           [r["prod_code"] for r in p.find_products(pattern)["rows"]]
    assert all("_" in r["brand"] for r in s.find_products(pattern)["rows"])


@pytest.mark.parametrize("call", [
    lambda a: a.get_market_performance("subgroup", "2024-05-01", "FY"),
    lambda a: a.get_market_performance("subgroup", "2025-01-01", "MAT"),
    lambda a: a.get_market_performance("subgroup", "2022-01-01", "MAT"),
    lambda a: a.get_market_performance("geography"),
    lambda a: a.get_segment_analysis("ssa"),
    lambda a: a.get_market_performance("company"),
    lambda a: a.get_market_trends("subgroup", "NO SUCH"),
    lambda a: a.get_brand_share("999999999", "total", "TOTAL"),
    lambda a: a.get_brand_performance(company="NO SUCH"),
    lambda a: a.get_company_performance(top_n=0),
    lambda a: a.find_products("x"),
])
def test_error_parity(apis, call):
    codes = []
    for api in apis:
        with pytest.raises(AnalyticsError) as e:
            call(api)
        codes.append(e.value.code)
    assert codes[0] == codes[1]


# ======================= fault injection =======================
# Each fault is injected into the Python engine with monkeypatch (auto-restored after the test);
# the cross-check must FAIL while the fault is present and PASS again after restoration.
FAULTS = {
    "growth formula (missing -100)": ("growth_pct", lambda c, p: None if not p or p <= 0 or c is None else c / p * 100,
                                      ("supergroup", "2024-05-01", "MAT", "total", "TOTAL")),
    "wrong comparison period (11 months back)": ("comparison_window",
                                                 lambda w: (periods.add_months(w.cur_start, -11),
                                                            periods.add_months(w.cur_end, -11)),
                                                 ("company", "2024-05-01", "YTD", "total", "TOTAL")),
    "share denominator missing one entity": ("share_denominator", lambda xs: math.fsum(xs[:-1]),
                                             ("acute_chronic", "2024-05-01", "MAT", "total", "TOTAL")),
    "dropped market filter": ("scope_members", lambda engine, st, sk: engine.packs,
                              ("company", "2024-05-01", "MAT", "subgroup", "@rank:subgroup:1")),
}


@pytest.mark.parametrize("fault", list(FAULTS))
def test_fault_injection_detected(con, py_engine, monkeypatch, fault):
    attr, bad, (etype, anchor, basis, st, sk) = FAULTS[fault]
    sk = V.resolve(py_engine, sk)
    assert V.crosscheck_period(con, py_engine, etype, anchor, basis, st, sk).passed        # baseline passes
    monkeypatch.setattr(metrics, attr, bad)
    assert not V.crosscheck_period(con, py_engine, etype, anchor, basis, st, sk).passed    # fault detected
    monkeypatch.undo()
    assert V.crosscheck_period(con, py_engine, etype, anchor, basis, st, sk).passed        # restored


def test_fault_injection_ranking_order(con, py_engine, monkeypatch):
    args = ("subgroup", "2024-05-01", "MAT", "total", "TOTAL")
    monkeypatch.setattr(rankings, "value_rank_key", lambda r: (rankings._r(r["value_cur"]), r["entity_key"].encode()))
    assert not V.crosscheck_period(con, py_engine, *args).passed
    monkeypatch.undo()
    assert V.crosscheck_period(con, py_engine, *args).passed


def test_fault_injection_sql_side_detected(py_engine):
    """A defect in the SQL layer (growth formula) is equally detected by the Python engine."""
    from pci_data.db import SQL_DIR, connect
    from pci_data.schema import PROCESSED_DIR
    c = connect()
    src = (SQL_DIR / "metrics.sql").read_text(encoding="utf-8").replace("{processed}", PROCESSED_DIR.as_posix())
    good = "THEN a.value_cur / a.value_prior * 100 - 100 END AS value_growth_pct"
    assert good in src
    c.execute(src.replace(good, "THEN a.value_cur / a.value_prior * 100 - 99 END AS value_growth_pct"))
    assert not V.crosscheck_period(c, py_engine, "supergroup", "2024-05-01", "MAT").passed
    c.close()
