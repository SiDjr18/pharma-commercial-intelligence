"""Structure, grain, keys and relationships of the analytical layer."""
import pytest

from pci_data.schema import DESCRIPTIVE, PROCESSED_DIR


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def columns(con, view):
    return {r[0]: r[1] for r in con.execute(f"DESCRIBE {view}").fetchall()}


def test_output_files_match_manifest(con, manifest):
    for name, meta in manifest["outputs"].items():
        path = PROCESSED_DIR / meta["file"]
        assert path.exists(), name
        assert one(con, f"SELECT count(*) FROM {name}") == meta["rows"], name


def test_column_map_covers_every_source_column_once(manifest, baseline):
    cmap = manifest["column_map"]
    assert len(cmap) == baseline["source_columns"]
    assert [c["source"] for c in cmap] == manifest["source"]["header"]
    assert sorted(c["position"] for c in cmap) == list(range(baseline["source_columns"]))
    kinds = {}
    for c in cmap:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    assert kinds == {"descriptive": 23, "monthly": 108, "snapshot": 27, "ni": 3, "split": 36, "price": 6}


def test_pack_schema(con):
    cols = columns(con, "pack")
    assert list(cols)[0] == "source_row"
    for _, target in DESCRIPTIVE:
        assert target in cols
    assert cols["pfc"] == "BIGINT"
    assert cols["pack_launch_month"] == "DATE"


def test_fact_schema(con):
    assert columns(con, "fact_pack_month") == {
        "pfc": "BIGINT", "period": "DATE", "value_cr": "DOUBLE", "units_k": "DOUBLE", "qty_k": "DOUBLE"}


def test_snapshot_schema(con):
    cols = columns(con, "pack_snapshot")
    expected = {"pfc", "snapshot_year", "snapshot_month", "ni_24m_mat_value_cr"}
    expected |= {f"{m}_{k}" for m in ("value", "units", "qty") for k in ("month", "ytd", "mat")}
    expected |= {f"{p}_mat_{m}" for p in ("ssa", "hsa", "dsa", "tsa") for m in ("value_cr", "units_k", "qty_k")}
    assert set(cols) == expected


@pytest.mark.parametrize("view,key", [
    ("pack", "pfc"),
    ("pack", "source_row"),
    ("fact_pack_month", "pfc, period"),
    ("pack_snapshot", "pfc, snapshot_year"),
    ("pack_price_month", "pfc, period"),
    ("dim_period", "period"),
    ("dim_product", "prod_code"),
    ("dim_product_subgroup", "index_desc"),
    ("dim_product_subgroup", "prod_code, subgroup"),
    ("dim_manufacturer", "manufacturer_code"),
    ("dim_company", "company"),
    ("dim_therapy", "subgroup"),
    ("dim_form", "nfc"),
])
def test_grain_is_unique(con, view, key):
    assert one(con, f"SELECT count(*) FROM (SELECT {key} FROM {view} GROUP BY ALL HAVING count(*) > 1)") == 0


def test_dense_row_counts(con, baseline):
    n = baseline["source_rows"]
    assert one(con, "SELECT count(*) FROM pack") == n
    assert one(con, "SELECT count(*) FROM fact_pack_month") == n * baseline["n_periods"]
    assert one(con, "SELECT count(*) FROM pack_snapshot") == n * len(baseline["snapshot_years"])
    assert one(con, "SELECT count(*) FROM pack_price_month") == n * baseline["n_price_periods"]


@pytest.mark.parametrize("view,key,expected", [
    ("dim_period", "period", "n_periods"),
    ("dim_product", "prod_code", "products"),
    ("dim_product_subgroup", "index_desc", "product_subgroups"),
    ("dim_manufacturer", "manufacturer_code", "manufacturers"),
    ("dim_company", "company", "companies"),
    ("dim_therapy", "subgroup", "subgroups"),
    ("dim_form", "nfc", "nfc_codes"),
])
def test_dimension_cardinality(con, baseline, view, key, expected):
    assert one(con, f"SELECT count(DISTINCT {key}) FROM {view}") == baseline[expected]


@pytest.mark.parametrize("child", ["fact_pack_month", "pack_snapshot", "pack_price_month"])
def test_referential_integrity_to_pack(con, child):
    assert one(con, f"SELECT count(*) FROM {child} c ANTI JOIN pack p USING (pfc)") == 0


@pytest.mark.parametrize("determinant,dependent", [
    ("pfc", "prod_code"), ("prod_code", "brand"), ("prod_code", "manufacturer_code"),
    ("prod_code", "company"), ("prod_code", "prod_launch_yyyymm"),
    ("manufacturer_code", "manufacturer_desc"), ("manufacturer_desc", "company"), ("company", "indian_mnc"),
    ("subgroup", "therapy_group"), ("subgroup", "supergroup"), ("subgroup", "acute_chronic"),
    ("nfc", "nfc3"), ("nfc3", "nfc2"), ("nfc2", "nfc1"), ("nfc", "form_short_desc"),
    ("molecule_desc", "plain_combination"), ("index_desc", "prod_code"), ("index_desc", "subgroup"),
])
def test_functional_dependencies(con, determinant, dependent):
    sql = (f"SELECT count(*) FROM (SELECT {determinant} FROM pack WHERE {determinant} IS NOT NULL "
           f"GROUP BY 1 HAVING count(DISTINCT {dependent}) > 1)")
    assert one(con, sql) == 0


@pytest.mark.parametrize("attr", ["subgroup", "molecule_desc"])
def test_product_is_not_single_therapy_or_molecule(con, attr):
    """Documented model constraint: these attributes live at pack grain, not product grain."""
    assert one(con, f"SELECT count(*) FROM (SELECT prod_code FROM pack GROUP BY 1 "
                    f"HAVING count(DISTINCT {attr}) > 1)") > 0
    assert attr not in columns(con, "dim_product")


def test_index_composition(con):
    """Source INDEX = BRANDS : SUBGROUP : MANUFACT. DESC : PROD_CODE (resolved open question)."""
    assert one(con, """SELECT count(*) FROM pack WHERE index_desc <>
        brand || ' : ' || subgroup || ' : ' || manufacturer_desc || ' : ' || CAST(prod_code AS VARCHAR)""") == 0


def test_period_dimension(con, baseline):
    rows = con.execute("SELECT period, period_index, mat_year_ending_may, source_label "
                       "FROM dim_period ORDER BY period").fetchall()
    assert str(rows[0][0]) == baseline["first_period"] and str(rows[-1][0]) == baseline["last_period"]
    assert [r[1] for r in rows] == list(range(1, baseline["n_periods"] + 1))
    assert rows[0][3] == "JUN'21" and rows[-1][3] == "MAY'24"
    assert {r[2] for r in rows} == set(baseline["snapshot_years"])
    assert all(sum(1 for r in rows if r[2] == y) == 12 for y in baseline["snapshot_years"])


def test_v_pack_month_is_lossless(con):
    assert one(con, "SELECT count(*) FROM v_pack_month") == one(con, "SELECT count(*) FROM fact_pack_month")
