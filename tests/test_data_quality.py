"""Automated data-quality checks (extends data/profile/DATA_QUALITY_BASELINE.md)."""
import pytest


def one(con, sql):
    return con.execute(sql).fetchone()[0]


# ---------------- nulls ----------------
def test_pack_nulls_only_where_expected(con, baseline):
    cols = [r[0] for r in con.execute("DESCRIBE pack").fetchall()]
    for c in cols:
        n = one(con, f"SELECT count(*) FROM pack WHERE {c} IS NULL")
        if c in {"plain_combination", "molecule_count", "molecule_desc"}:
            assert n == baseline["molecule_null_rows"], c
        elif c == "pack_launch_month":
            assert n == baseline["pack_launch_unknown"]
        elif c == "prod_launch_month":
            assert n == baseline["prod_launch_unknown"]
        else:
            assert n == 0, c
    # the molecule nulls are the same single row
    assert one(con, """SELECT count(*) FROM pack WHERE molecule_desc IS NULL
                       AND plain_combination IS NULL AND molecule_count IS NULL""") == baseline["molecule_null_rows"]


def test_launch_null_iff_source_sentinel_zero(con):
    assert one(con, "SELECT count(*) FROM pack WHERE (pack_launch_month IS NULL) <> (pack_launch_yyyymm = 0)") == 0
    assert one(con, "SELECT count(*) FROM pack WHERE (prod_launch_month IS NULL) <> (prod_launch_yyyymm = 0)") == 0


@pytest.mark.parametrize("view,cols", [
    ("fact_pack_month", ["value_cr", "units_k", "qty_k"]),
    ("pack_price_month", ["price_rs"]),
])
def test_measures_not_null(con, view, cols):
    for c in cols:
        assert one(con, f"SELECT count(*) FROM {view} WHERE {c} IS NULL") == 0, c


def test_snapshot_nulls_only_in_new_introduction(con):
    cols = [r[0] for r in con.execute("DESCRIBE pack_snapshot").fetchall()]
    for c in cols:
        if c != "ni_24m_mat_value_cr":
            assert one(con, f"SELECT count(*) FROM pack_snapshot WHERE {c} IS NULL") == 0, c


# ---------------- duplicates ----------------
def test_no_duplicate_pack_records(con):
    assert one(con, "SELECT count(*) - count(DISTINCT pfc) FROM pack") == 0


def test_brand_name_is_not_a_key(con, baseline):
    """Known risk: brand names shared across companies; analytics must key on prod_code."""
    assert one(con, """SELECT count(*) FROM (SELECT brand FROM pack GROUP BY 1
                       HAVING count(DISTINCT company) > 1)""") == baseline["brand_names_shared_across_companies"]


def test_group_supergroup_exceptions_unchanged(con, baseline):
    assert one(con, """SELECT count(*) FROM (SELECT therapy_group FROM pack GROUP BY 1
                       HAVING count(DISTINCT supergroup) > 1)""") == baseline["group_supergroup_exceptions"]


# ---------------- date validity ----------------
def test_launch_dates_valid(con, baseline):
    assert one(con, """SELECT count(*) FROM pack WHERE pack_launch_yyyymm <> 0
                       AND (pack_launch_yyyymm % 100 NOT BETWEEN 1 AND 12)""") == 0
    assert one(con, f"SELECT count(*) FROM pack WHERE pack_launch_month > DATE '{baseline['last_period']}'") == 0
    assert one(con, f"SELECT count(*) FROM pack WHERE prod_launch_month > DATE '{baseline['last_period']}'") == 0
    assert one(con, "SELECT count(*) FROM pack WHERE pack_launch_month < DATE '1900-01-01'") == 0
    assert one(con, "SELECT count(*) FROM pack WHERE pack_launch_month < prod_launch_month") == 0


def test_periods_are_first_of_month(con):
    assert one(con, "SELECT count(*) FROM dim_period WHERE day(period) <> 1") == 0
    assert one(con, "SELECT count(*) FROM pack_price_month WHERE day(period) <> 1") == 0


# ---------------- period completeness ----------------
def test_period_completeness(con, baseline):
    periods = [r[0] for r in con.execute("SELECT period FROM dim_period ORDER BY 1").fetchall()]
    assert len(periods) == baseline["n_periods"]
    for a, b in zip(periods, periods[1:]):
        assert (b.year * 12 + b.month) - (a.year * 12 + a.month) == 1
    assert one(con, f"""SELECT count(*) FROM (SELECT pfc FROM fact_pack_month GROUP BY pfc
                       HAVING count(*) <> {baseline['n_periods']})""") == 0


def test_price_periods(con):
    got = [str(r[0]) for r in con.execute("SELECT DISTINCT period FROM pack_price_month ORDER BY 1").fetchall()]
    assert got == ["2023-12-01", "2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"]


# ---------------- numeric validity ----------------
@pytest.mark.parametrize("view,cols", [
    ("fact_pack_month", ["value_cr", "units_k", "qty_k"]),
    ("pack_price_month", ["price_rs"]),
    ("pack_snapshot", ["value_mat", "units_mat", "qty_mat", "tsa_mat_value_cr", "ssa_mat_value_cr",
                       "hsa_mat_value_cr", "dsa_mat_value_cr", "ni_24m_mat_value_cr"]),
])
def test_no_negative_or_non_finite(con, view, cols):
    for c in cols:
        assert one(con, f"SELECT count(*) FROM {view} WHERE {c} < 0 OR NOT isfinite({c})") == 0, c


def test_molecule_count_range_and_meaning(con):
    assert one(con, "SELECT count(*) FROM pack WHERE molecule_count NOT BETWEEN 1 AND 10") == 0
    assert one(con, """SELECT count(*) FROM pack WHERE molecule_desc IS NOT NULL
        AND molecule_count <> length(molecule_desc) - length(replace(molecule_desc, '+', '')) + 1""") == 0


def test_dormant_pack_count(con, baseline):
    assert one(con, """SELECT count(*) FROM (SELECT pfc FROM fact_pack_month GROUP BY pfc
                       HAVING max(value_cr) = 0)""") == baseline["dormant_packs_all_36_months_zero"]


def test_units_zero_implies_value_and_qty_zero(con):
    assert one(con, "SELECT count(*) FROM fact_pack_month WHERE units_k = 0 AND qty_k > 0") == 0


# ---------------- unexpected categories ----------------
ALLOWED = {
    "plain_combination": {"Plain", "Combination"},
    "indian_mnc": {"INDIAN", "MNC"},
    "acute_chronic": {"ACUTE", "CHRONIC"},
    "form_short_desc": {"Oral Solids", "Oral liquids", "Topical", "Parentral", "Ophthal", "Oral topical",
                        "Nasal", "Vaginal", "Device", "OTIC", "external", "SUPPOSITORIES", "Rectal"},
    "supergroup": {"DERMA", "NEURO / CNS", "VITAMINS/MINERALS/NUTRIENTS", "CARDIAC", "ANTI-INFECTIVES",
                   "GASTRO INTESTINAL", "PAIN / ANALGESICS", "RESPIRATORY", "ANTI DIABETIC", "GYNAEC.",
                   "OTHERS", "OPHTHAL / OTOLOGICALS", "ANTINEOPLAST/IMMUNOMODULATOR", "UROLOGY", "HORMONES",
                   "STOMATOLOGICALS", "HEPATOPROTECTIVES", "BLOOD RELATED", "ANTIVIRAL", "ANTI-PARASITIC",
                   "ANTI MALARIALS", "VACCINES", "SEX STIMULANTS / REJUVENATORS", "PARENTERAL", "ANTI-TB"},
}


@pytest.mark.parametrize("col", list(ALLOWED))
def test_categories(con, col):
    got = {r[0] for r in con.execute(f"SELECT DISTINCT {col} FROM pack WHERE {col} IS NOT NULL").fetchall()}
    assert got == ALLOWED[col], got ^ ALLOWED[col]


def test_nfc1_codes(con):
    got = {r[0][:1] for r in con.execute("SELECT DISTINCT nfc1 FROM pack").fetchall()}
    assert len(got) == 17
    assert one(con, "SELECT count(*) FROM pack WHERE left(nfc, 1) <> left(nfc1, 1)") == 0


def test_text_fields_trimmed_and_non_blank(con):
    for c in ["pack_desc", "brand", "company", "subgroup", "therapy_group", "supergroup", "index_desc"]:
        assert one(con, f"SELECT count(*) FROM pack WHERE {c} <> trim({c}) OR trim({c}) = ''") == 0, c


# ---------------- aggregation consistency ----------------
@pytest.mark.parametrize("dim", ["supergroup", "company", "subgroup", "acute_chronic", "indian_mnc", "nfc1"])
def test_rollups_sum_to_grand_total(con, dim):
    total = one(con, "SELECT sum(value_cr) FROM fact_pack_month")
    by_dim = one(con, f"SELECT sum(s) FROM (SELECT {dim}, sum(value_cr) s FROM v_pack_month GROUP BY 1)")
    assert abs(total - by_dim) <= 1e-9 * total


def test_therapy_rollup_via_subgroup_equals_direct(con):
    diff = one(con, """
        WITH a AS (SELECT supergroup, sum(value_cr) v FROM v_pack_month GROUP BY 1),
             b AS (SELECT t.supergroup, sum(f.value_cr) v FROM fact_pack_month f JOIN pack p USING (pfc)
                   JOIN dim_therapy t ON t.subgroup = p.subgroup GROUP BY 1)
        SELECT max(abs(a.v - b.v)) FROM a JOIN b USING (supergroup)""")
    assert diff <= 1e-6
