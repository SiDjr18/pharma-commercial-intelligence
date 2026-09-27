"""M4 SQL analytics: time logic, market assignment, reconciliation, growth, share, ranking."""
import datetime as dt

import pytest

TOL = 1e-6          # absolute tolerance on Rs crore / '000 sums (M3 reconciled to 1e-9 relative)
GROWTH_TOL = 1e-9   # tolerance on growth percentages


def one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def rows(con, sql, params=None):
    return con.execute(sql, params or []).fetchall()


PARTITION_TYPES = ["total", "supergroup", "therapy_group", "subgroup", "molecule", "company", "manufacturer",
                   "product", "product_subgroup", "acute_chronic", "indian_mnc", "plain_combination",
                   "molecule_count", "dosage_form", "nfc1"]


# ======================= time logic =======================
@pytest.mark.parametrize("basis,first_valid,first_growth", [
    ("MONTH", "2021-06-01", "2022-06-01"), ("YTD", "2022-01-01", "2023-01-01"), ("MAT", "2022-05-01", "2023-05-01")])
def test_first_valid_periods(con, basis, first_valid, first_growth):
    assert str(one(con, "SELECT min(anchor) FROM period_basis WHERE basis=? AND current_complete", [basis])) == first_valid
    assert str(one(con, "SELECT min(anchor) FROM period_basis WHERE basis=? AND prior_complete", [basis])) == first_growth


def test_window_shapes(con):
    for anchor, basis, start, n in [("2024-05-01", "MAT", "2023-06-01", 12), ("2024-05-01", "YTD", "2024-01-01", 5),
                                    ("2024-01-01", "YTD", "2024-01-01", 1), ("2023-12-01", "YTD", "2023-01-01", 12),
                                    ("2024-05-01", "MONTH", "2024-05-01", 1)]:
        r = rows(con, "SELECT cur_start, n_months, prior_start, prior_end FROM period_basis WHERE anchor=? AND basis=?",
                 [anchor, basis])[0]
        assert str(r[0]) == start and r[1] == n
        a = dt.date.fromisoformat(anchor)
        assert r[3] == a.replace(year=a.year - 1) and r[2] == r[0].replace(year=r[0].year - 1)


def test_ytd_is_calendar_not_fiscal(con):
    assert one(con, "SELECT count(*) FROM period_basis WHERE basis='YTD' AND month(cur_start) <> 1") == 0
    assert one(con, "SELECT basis_label FROM period_basis WHERE basis='YTD' LIMIT 1") == "Calendar YTD"


@pytest.mark.parametrize("anchor,basis", [("2022-04-01", "MAT"), ("2021-12-01", "YTD"), ("2021-06-01", "MAT")])
def test_incomplete_current_window_returns_nothing(con, anchor, basis):
    assert one(con, "SELECT count(*) FROM market_performance('total', ?, ?)", [anchor, basis]) == 0


@pytest.mark.parametrize("anchor,basis", [("2022-05-01", "MAT"), ("2022-12-01", "YTD"), ("2022-03-01", "MONTH")])
def test_prior_unavailable_gives_null_growth(con, anchor, basis):
    r = rows(con, """SELECT value_prior, value_growth_pct, value_growth_status, prior_start, contribution_to_growth_pp
                     FROM market_performance('supergroup', ?, ?)""", [anchor, basis])
    assert r and all(x[0] is None and x[1] is None and x[2] == "prior_unavailable" and x[3] is None and x[4] is None
                     for x in r)


# ======================= market assignment =======================
def test_entity_catalog_matches_pack_entity(con):
    cat = {r[0] for r in rows(con, "SELECT entity_type FROM entity_type_catalog")}
    assert cat == set(PARTITION_TYPES)
    assert {r[0] for r in rows(con, "SELECT DISTINCT entity_type FROM pack_entity")} == cat


@pytest.mark.parametrize("etype", PARTITION_TYPES)
def test_every_pack_in_exactly_one_entity(con, baseline, etype):
    n, d, nulls = rows(con, """SELECT count(*), count(DISTINCT pfc), count(*) FILTER (WHERE entity_key IS NULL)
                               FROM pack_entity WHERE entity_type = ?""", [etype])[0]
    assert n == d == baseline["source_rows"] and nulls == 0


@pytest.mark.parametrize("etype", ["molecule", "plain_combination", "molecule_count"])
def test_unclassified_member(con, baseline, etype):
    assert one(con, "SELECT count(*) FROM pack_entity WHERE entity_type=? AND entity_key='(UNCLASSIFIED)'",
               [etype]) == baseline["molecule_null_rows"]


def test_primary_market_nests_in_therapy_area_and_segment(con):
    assert one(con, "SELECT count(*) FROM (SELECT subgroup FROM pack GROUP BY 1 HAVING count(DISTINCT supergroup) > 1)") == 0
    assert one(con, "SELECT count(*) FROM (SELECT subgroup FROM pack GROUP BY 1 HAVING count(DISTINCT acute_chronic) > 1)") == 0


def test_entity_keys_are_labels_are_separate(con):
    """Product key is prod_code; the display label may repeat (brand names are shared)."""
    k, l = rows(con, "SELECT count(DISTINCT entity_key), count(DISTINCT entity_label) FROM pack_entity WHERE entity_type='product'")[0]
    assert k == 60_079 and l < k


# ======================= partition reconciliation =======================
@pytest.mark.parametrize("basis", ["MONTH", "YTD", "MAT"])
@pytest.mark.parametrize("etype", PARTITION_TYPES)
def test_partition_totals_reconcile_to_market(con, etype, basis):
    t = rows(con, "SELECT value_cur, value_prior, units_cur, units_prior, value_growth_pct "
                  "FROM entity_period('total', '2024-05-01', ?)", [basis])[0]
    s = rows(con, f"""SELECT sum(value_cur), sum(value_prior), sum(units_cur), sum(units_prior),
                             sum(value_share_pct), sum(contribution_to_growth_pp), sum(n_packs)
                      FROM entity_period(?, '2024-05-01', ?)""", [etype, basis])[0]
    for i in range(4):
        assert abs(s[i] - t[i]) <= TOL + 1e-12 * abs(t[i])
    assert abs(s[4] - 100) <= 1e-9
    assert abs(s[5] - t[4]) <= 1e-9
    assert s[6] == 105_317


# ======================= source (M3) reconciliation =======================
SNAP = {"MONTH": "month", "YTD": "ytd", "MAT": "mat"}


@pytest.mark.parametrize("year", [2022, 2023, 2024])
@pytest.mark.parametrize("basis", ["MONTH", "YTD", "MAT"])
@pytest.mark.parametrize("level,col", [("total", None), ("supergroup", "supergroup"), ("company", "company"),
                                       ("acute_chronic", "acute_chronic")])
def test_current_matches_source_snapshot(con, year, basis, level, col):
    k = SNAP[basis]
    key = "'TOTAL'" if col is None else f"p.{col}"
    src = dict(rows(con, f"""SELECT {key}, sum(s.value_{k}) FROM pack_snapshot s JOIN pack p USING (pfc)
                             WHERE s.snapshot_year = {year} GROUP BY 1"""))
    srcu = dict(rows(con, f"""SELECT {key}, sum(s.units_{k}) FROM pack_snapshot s JOIN pack p USING (pfc)
                              WHERE s.snapshot_year = {year} GROUP BY 1"""))
    got = {r[0]: r[1:] for r in rows(con, "SELECT entity_key, value_cur, units_cur FROM entity_period(?, ?, ?)",
                                     [level, dt.date(year, 5, 1), basis])}
    assert set(got) == set(src)
    for kk in src:
        assert abs(got[kk][0] - src[kk]) <= TOL and abs(got[kk][1] - srcu[kk]) <= TOL * 1000, kk


@pytest.mark.parametrize("year", [2023, 2024])
@pytest.mark.parametrize("basis", ["MONTH", "YTD", "MAT"])
@pytest.mark.parametrize("level,col", [("total", None), ("supergroup", "supergroup"), ("company", "company"),
                                       ("indian_mnc", "indian_mnc"), ("dosage_form", "form_short_desc")])
def test_growth_reproduces_source_pivot_formula(con, year, basis, level, col):
    """Pivot: VAL/UNIT/QTY GRTH {MONTH,CUMM,MAT} = cur/prior*100-100 on summed source snapshot columns."""
    k = SNAP[basis]
    key = "'TOTAL'" if col is None else f"p.{col}"
    exprs = ", ".join(f"""CASE WHEN sum(s.{m}_{k}) FILTER (WHERE s.snapshot_year = {year - 1}) > 0 THEN
              sum(s.{m}_{k}) FILTER (WHERE s.snapshot_year = {year}) /
              sum(s.{m}_{k}) FILTER (WHERE s.snapshot_year = {year - 1}) * 100 - 100 END""" for m in ("value", "units", "qty"))
    src = {r[0]: r[1:] for r in rows(con, f"SELECT {key}, {exprs} FROM pack_snapshot s JOIN pack p USING (pfc) GROUP BY 1")}
    got = {r[0]: r[1:] for r in rows(con, """SELECT entity_key, value_growth_pct, units_growth_pct, qty_growth_pct
                                             FROM entity_period(?, ?, ?)""", [level, dt.date(year, 5, 1), basis])}
    assert set(got) == set(src)
    for kk, v in src.items():
        for i in range(3):
            if v[i] is None:
                assert got[kk][i] is None, kk
            else:
                assert abs(got[kk][i] - v[i]) <= 1e-6, (kk, i)


def test_prior_equals_previous_year_current(con):
    cur23 = dict(rows(con, "SELECT entity_key, value_cur FROM market_performance('subgroup', '2023-05-01', 'MAT')"))
    pri24 = dict(rows(con, "SELECT entity_key, value_prior FROM market_performance('subgroup', '2024-05-01', 'MAT')"))
    assert set(cur23) == set(pri24)
    assert max(abs(cur23[k] - pri24[k]) for k in cur23) <= TOL


# ======================= growth edge cases =======================
def test_growth_never_zero_filled(con):
    r = rows(con, """SELECT count(*) FILTER (WHERE value_prior = 0 AND value_growth_pct IS NOT NULL),
                            count(*) FILTER (WHERE value_prior = 0 AND value_cur > 0 AND value_growth_status <> 'prior_zero'),
                            count(*) FILTER (WHERE value_prior = 0 AND value_cur = 0 AND value_growth_status <> 'no_sales'),
                            count(*) FILTER (WHERE value_growth_status = 'prior_zero'),
                            count(*) FILTER (WHERE value_growth_status = 'no_sales'),
                            count(*) FILTER (WHERE units_prior = 0 AND units_growth_pct IS NOT NULL),
                            count(*) FILTER (WHERE value_prior > 0 AND value_growth_status <> 'ok')
                     FROM entity_period('product', '2024-05-01', 'MAT')""")[0]
    assert r[0] == r[1] == r[2] == r[5] == r[6] == 0
    assert r[3] > 0 and r[4] > 0   # both edge cases exist in real data and are exercised


def test_evolution_index_identity(con):
    g_m = one(con, "SELECT value_growth_pct FROM market_performance('total', '2024-05-01', 'MAT')")
    bad = one(con, """SELECT count(*) FROM market_performance('supergroup', '2024-05-01', 'MAT')
                      WHERE abs(evolution_index - 100 * (1 + value_growth_pct / 100) / (1 + ? / 100)) > 1e-9""", [g_m])
    assert bad == 0


def test_share_change_definition(con):
    assert one(con, """SELECT count(*) FROM market_performance('subgroup', '2024-05-01', 'YTD')
                       WHERE abs(value_share_chg_pp - (value_share_pct - value_share_prior_pct)) > 1e-12""") == 0


# ======================= ranking =======================
@pytest.mark.parametrize("macro", [
    "market_performance('subgroup', '2024-05-01', 'MAT')",
    "product_performance('2024-05-01', 'MAT')",
    "company_performance('2024-05-01', 'YTD')",
    "therapy_performance('therapy_group', '2024-05-01', 'MONTH')",
    "segment_performance('dosage_form', '2024-05-01', 'MAT')",
])
def test_rank_is_deterministic_and_complete(con, macro):
    r = rows(con, f"SELECT rank_value, value_cur, entity_key, rank_growth, n_entities FROM {macro} ORDER BY rank_value")
    n = len(r)
    assert [x[0] for x in r] == list(range(1, n + 1))
    assert sorted(x[3] for x in r) == list(range(1, n + 1))
    assert all(x[4] == n for x in r)
    for a, b in zip(r, r[1:]):
        ra, rb = round(a[1], 9), round(b[1], 9)
        assert ra > rb or (ra == rb and a[2].encode() < b[2].encode())   # rounded value desc, ties by key asc
    again = rows(con, f"SELECT entity_key FROM {macro} ORDER BY rank_value")
    assert again == [(x[2],) for x in r]


def test_rank_ties_exist_and_are_broken_by_key(con):
    ties = rows(con, """SELECT entity_key, rank_value FROM product_performance('2024-05-01', 'MAT')
                        WHERE value_cur = 0 ORDER BY rank_value LIMIT 50""")
    assert len(ties) == 50 and [t[0] for t in ties] == sorted(t[0] for t in ties)


def test_rank_growth_nulls_last(con):
    r = rows(con, """SELECT max(rank_growth) FILTER (WHERE value_growth_pct IS NOT NULL),
                            min(rank_growth) FILTER (WHERE value_growth_pct IS NULL)
                     FROM product_performance('2024-05-01', 'MAT')""")[0]
    assert r[0] < r[1]


# ======================= brand / product, overlap =======================
def test_product_value_equals_sum_over_its_subgroup_markets(con):
    """Products spanning several subgroups are split by pack, never double counted."""
    bad = one(con, """
        WITH ps AS (SELECT p.prod_code, sum(e.value_cur) v
                    FROM entity_period('product_subgroup', '2024-05-01', 'MAT') e
                    JOIN dim_product_subgroup p ON p.index_desc = e.entity_key GROUP BY 1),
             pr AS (SELECT CAST(entity_key AS BIGINT) prod_code, value_cur v FROM entity_period('product', '2024-05-01', 'MAT'))
        SELECT count(*) FROM pr FULL JOIN ps USING (prod_code) WHERE abs(coalesce(pr.v, -1) - coalesce(ps.v, -2)) > 1e-9""")
    assert bad == 0


def test_multi_subgroup_product_scoped_shares(con):
    pc, subgroups = rows(con, """SELECT prod_code, list(DISTINCT subgroup ORDER BY subgroup) FROM pack GROUP BY 1
                                 HAVING count(DISTINCT subgroup) >= 3 ORDER BY prod_code LIMIT 1""")[0]
    total = one(con, "SELECT value_cur FROM product_performance('2024-05-01', 'MAT') WHERE entity_key = ?", [str(pc)])
    parts = 0.0
    for sg in subgroups:
        r = rows(con, """SELECT value_cur, value_share_pct, scope_value_cur FROM product_performance('2024-05-01','MAT','subgroup',?)
                         WHERE entity_key = ?""", [sg, str(pc)])[0]
        mkt = one(con, "SELECT value_cur FROM market_performance('subgroup','2024-05-01','MAT') WHERE entity_key=?", [sg])
        assert abs(r[2] - mkt) <= TOL
        if mkt > 0:
            assert abs(r[1] - r[0] / mkt * 100) <= 1e-9
        parts += r[0]
    assert abs(parts - total) <= TOL


@pytest.mark.parametrize("scope_type", ["subgroup", "supergroup", "molecule", "company"])
def test_scoped_shares_sum_to_100(con, scope_type):
    key = one(con, f"SELECT entity_key FROM entity_period('{scope_type}', '2024-05-01', 'MAT') ORDER BY rank_value LIMIT 1")
    s, scope_tot = rows(con, "SELECT sum(value_share_pct), any_value(scope_value_cur) FROM product_performance('2024-05-01','MAT',?,?)",
                        [scope_type, key])[0]
    direct = one(con, f"SELECT value_cur FROM entity_period('{scope_type}', '2024-05-01', 'MAT') WHERE entity_key = ?", [key])
    assert abs(s - 100) <= 1e-9 and abs(scope_tot - direct) <= TOL


def test_product_attributes_from_dim_product(con):
    assert one(con, """SELECT count(*) FROM product_performance('2024-05-01','MONTH') pp
                       JOIN dim_product d ON CAST(d.prod_code AS VARCHAR) = pp.entity_key
                       WHERE pp.brand <> d.brand OR pp.company <> d.company""") == 0


# ======================= company / therapy / segment =======================
def test_company_is_sum_of_manufacturers(con):
    bad = one(con, """WITH m AS (SELECT dm.company, sum(e.value_cur) v FROM entity_period('manufacturer','2024-05-01','MAT') e
                                 JOIN dim_manufacturer dm ON CAST(dm.manufacturer_code AS VARCHAR) = e.entity_key GROUP BY 1)
                      SELECT count(*) FROM company_performance('2024-05-01','MAT') c JOIN m ON m.company = c.entity_key
                      WHERE abs(m.v - c.value_cur) > 1e-9""")
    assert bad == 0


def test_subgroups_roll_up_to_supergroups(con):
    bad = one(con, """WITH s AS (SELECT supergroup, sum(value_cur) v, sum(value_prior) p
                                 FROM therapy_performance('subgroup','2024-05-01','MAT') GROUP BY 1)
                      SELECT count(*) FROM therapy_performance('supergroup','2024-05-01','MAT') t
                      JOIN s ON s.supergroup = t.entity_key
                      WHERE abs(s.v - t.value_cur) > 1e-6 OR abs(s.p - t.value_prior) > 1e-6""")
    assert bad == 0
    assert one(con, "SELECT count(*) FROM therapy_performance('subgroup','2024-05-01','MAT') WHERE supergroup IS NULL") == 0
    assert one(con, "SELECT count(*) FROM therapy_performance('therapy_group','2024-05-01','MAT') WHERE supergroup IS NOT NULL") == 0


def test_therapy_within_supergroup_scope(con):
    sg = one(con, "SELECT entity_key FROM market_performance('supergroup','2024-05-01','MAT') WHERE rank_value = 1")
    r = rows(con, """SELECT count(*), count(*) FILTER (WHERE supergroup <> ?), sum(value_share_pct)
                     FROM therapy_performance('subgroup','2024-05-01','MAT','supergroup',?)""", [sg, sg])[0]
    assert r[0] > 0 and r[1] == 0 and abs(r[2] - 100) <= 1e-9


@pytest.mark.parametrize("segment,expected", [("acute_chronic", 2), ("indian_mnc", 2), ("plain_combination", 3),
                                              ("molecule_count", 11), ("dosage_form", 13), ("nfc1", 17)])
def test_segment_members(con, segment, expected):
    assert one(con, "SELECT count(*) FROM segment_performance(?, '2024-05-01', 'MAT')", [segment]) == expected


@pytest.mark.parametrize("macro", ["segment_performance('company','2024-05-01','MAT')",
                                   "market_performance('company','2024-05-01','MAT')",
                                   "therapy_performance('molecule','2024-05-01','MAT')"])
def test_domain_macros_reject_wrong_types(con, macro):
    assert one(con, f"SELECT count(*) FROM {macro}") == 0


# ======================= trend =======================
def test_trend_dense_and_consistent(con):
    key = one(con, "SELECT entity_key FROM market_performance('subgroup','2024-05-01','MAT') WHERE rank_value = 7")
    t = {str(r[0]): r[1:] for r in rows(con, """SELECT period, value_cr, value_mat, value_ytd, value_mat_growth_pct,
                                                value_growth_pct, value_ytd_growth_pct FROM market_trend('subgroup', ?)""", [key])}
    assert len(t) == 36
    assert all(t[p][1] is None for p in list(t)[:11]) and t["2022-05-01"][1] is not None
    assert all(t[p][3] is None for p in list(t)[:23]) and t["2023-05-01"][3] is not None
    assert all(t[p][2] is None for p in t if p < "2022-01-01")
    assert all(t[p][4] is None for p in list(t)[:12])
    for anchor, basis, idx in [("2024-05-01", "MAT", 1), ("2024-03-01", "YTD", 2), ("2023-11-01", "MONTH", 0)]:
        v = one(con, "SELECT value_cur FROM market_performance('subgroup', ?, ?) WHERE entity_key = ?", [anchor, basis, key])
        assert abs(t[anchor][idx] - v) <= TOL
    g = one(con, "SELECT value_growth_pct FROM market_performance('subgroup','2024-05-01','MAT') WHERE entity_key=?", [key])
    assert abs(t["2024-05-01"][3] - g) <= GROWTH_TOL


def test_trend_unknown_key_is_empty(con):
    assert one(con, "SELECT count(*) FROM market_trend('subgroup', 'NO SUCH SUBGROUP')") == 0


def test_trend_zero_months_present(con):
    """Missing sales months are zero-filled (dense), not dropped."""
    pc = one(con, """SELECT prod_code FROM pack p JOIN fact_pack_month f USING (pfc) GROUP BY 1
                     HAVING min(f.value_cr) = 0 AND max(f.value_cr) > 0 ORDER BY 1 LIMIT 1""")
    r = rows(con, "SELECT count(*), count(*) FILTER (WHERE value_cr = 0) FROM product_trend(?)", [str(pc)])[0]
    assert r[0] == 36 and r[1] > 0
