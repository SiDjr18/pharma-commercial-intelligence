"""M13 — synthetic public dataset: additive tests (need NO private data).

The synthetic layer is generated into a temporary folder (deterministic, ~2 s) and exercised through the
UNCHANGED engines: DuckDB SQL views, the independent Python engine (M5 cross-validation matrix), opportunity
scoring (M6) and the scenario engine (M7). Private fixtures in conftest.py are never used here.
"""
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from pci_synthetic import generate as G
from pci_synthetic import spec as S

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
COMMITTED_FP = ROOT / "data" / "synthetic" / G.FINGERPRINT_FILE


@pytest.fixture(scope="module")
def syn(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic")
    info = G.write(d)
    return d, info


@pytest.fixture(scope="module")
def con(syn):
    from pci_data.db import connect
    c = connect(syn[0])
    yield c
    c.close()


@pytest.fixture(scope="module")
def eng(syn):
    from pci_analytics.engine import DataEngine
    return DataEngine(syn[0])


@pytest.fixture(scope="module")
def api(syn, con):
    from pci_analytics.api import CommercialAnalytics
    return CommercialAnalytics(con, syn[0] / "_manifest.json")


def q(con, sql):
    return con.execute(sql).fetchall()


# ------------------------------------------------------------------------------------------ determinism
def test_deterministic_and_matches_committed_fingerprint(syn):
    d, info = syn
    again = G.canonical_hash(G.generate())
    assert info["fingerprint_sha256"] == again
    committed = json.loads(COMMITTED_FP.read_text(encoding="utf-8"))
    assert committed["fingerprint_sha256"] == again and committed["version"] == S.VERSION and committed["seed"] == S.SEED
    assert committed["rows"] == info["rows"]
    assert G.canonical_hash(G.generate(S.SEED + 1)) != again


def test_generator_reads_no_data_file():
    """Structure-only: the generator's code never touches the licensed source, processed layer or profiles."""
    src = "".join((ROOT / "python" / "pci_synthetic" / f).read_text(encoding="utf-8")
                  for f in ("generate.py", "spec.py", "names.py", "__init__.py"))
    code = re.sub(r'""".*?"""', "", src, flags=re.S)                   # docstrings may describe what is NOT read
    for bad in ("data/processed", "data\\\\processed", "processed\"", "IMS Base", ".xlsx", "openpyxl", "read_parquet",
                "pq.read_table", "pci_data", "data/profile", "duckdb", "SOURCE_PATH", "MANIFEST_PATH"):
        assert bad not in code, bad


# ------------------------------------------------------------------------------------------ schema / grain
def test_schema_parity_with_processed_layer(syn):
    snap = json.loads((ROOT / "python" / "pci_synthetic" / "processed_schema.json").read_text(encoding="utf-8"))["tables"]
    for t, cols in snap.items():
        s = pq.read_schema(syn[0] / f"{t}.parquet")
        assert [[f.name, str(f.type)] for f in s] == cols, t


def test_processed_schema_snapshot_matches_private_layer():
    """Keeps the committed contract in sync with the private layer (IMS mode only; skipped elsewhere)."""
    from pci_data.schema import DATASETS
    p = DATASETS.get("ims")
    if p is None or not (p / "pack.parquet").exists():
        pytest.skip("private IMS layer not configured (PCI_DATASET=ims + PCI_IMS_DATA_DIR)")
    snap = json.loads((ROOT / "python" / "pci_synthetic" / "processed_schema.json").read_text(encoding="utf-8"))["tables"]
    for t, cols in snap.items():
        assert [[f.name, str(f.type)] for f in pq.read_schema(p / f"{t}.parquet")] == cols, t


def test_grain_calendar_and_manifest(syn, con):
    d, info = syn
    n = info["rows"]["pack"]
    assert 3500 <= n <= 5000 and info["rows"]["fact_pack_month"] == n * 36
    assert info["rows"]["pack_snapshot"] == n * 3 and info["rows"]["pack_price_month"] == n * 6
    assert q(con, "SELECT count(*) = count(DISTINCT pfc) FROM pack")[0][0]
    assert q(con, "SELECT count(*) = count(DISTINCT (pfc, period)) FROM fact_pack_month")[0][0]
    assert q(con, "SELECT min(period), max(period), count(DISTINCT period) FROM fact_pack_month")[0] == \
        (dt.date(2021, 6, 1), dt.date(2024, 5, 1), 36)
    assert q(con, "SELECT count(*) FROM fact_pack_month WHERE value_cr IS NULL OR units_k IS NULL OR qty_k IS NULL "
                  "OR value_cr < 0 OR units_k < 0 OR qty_k < 0")[0][0] == 0
    m = json.loads((d / "_manifest.json").read_text(encoding="utf-8"))
    assert m["dataset"] == "synthetic" and m["source"]["sha256"] == info["fingerprint_sha256"]
    assert {"built_at"} <= set(m["build"]) and len(m["periods"]) == 36 and m["snapshot_years"] == [2022, 2023, 2024]
    assert m["price_periods"][0] == "2023-12-01" and len(m["price_periods"]) == 6
    assert q(con, f"SELECT min(pfc) >= {S.FIRST_CODE}, min(prod_code) >= {S.FIRST_CODE}, "
                  f"min(manufacturer_code) >= {S.FIRST_CODE} FROM pack")[0] == (True, True, True)


# ------------------------------------------------------------------------------------------ DQ structural rules
@pytest.mark.parametrize("determinant,dependents", [
    ("prod_code", ["brand", "manufacturer_code", "prod_launch_yyyymm"]),
    ("manufacturer_code", ["manufacturer_desc", "company", "indian_mnc"]),
    ("manufacturer_desc", ["company"]),
    ("company", ["indian_mnc"]),
    ("subgroup", ["therapy_group", "supergroup", "acute_chronic"]),
    ("nfc", ["nfc1", "nfc2", "nfc3", "form_short_desc"]),
    ("pfc", ["pack_desc", "prod_code", "molecule_desc", "pack_launch_yyyymm"]),
])
def test_functional_dependencies(con, determinant, dependents):
    for dep in dependents:
        assert q(con, f"SELECT count(*) FROM (SELECT {determinant} FROM pack GROUP BY 1 "
                      f"HAVING count(DISTINCT coalesce(CAST({dep} AS VARCHAR), '~')) > 1)")[0][0] == 0, (determinant, dep)


def test_dq_quirks_reproduced(con):
    one = lambda s: q(con, s)[0][0]
    assert one("SELECT count(*) FROM (SELECT therapy_group FROM pack GROUP BY 1 HAVING count(DISTINCT supergroup) > 1)") == 2
    assert one("SELECT count(*) FROM (SELECT brand FROM pack GROUP BY 1 HAVING count(DISTINCT company) > 1)") > 0
    assert one("SELECT count(*) FROM (SELECT brand FROM pack GROUP BY 1 HAVING count(DISTINCT prod_code) > 1)") > 0
    assert one("SELECT count(*) FROM (SELECT prod_code FROM pack GROUP BY 1 HAVING count(DISTINCT subgroup) > 1)") > 0
    assert one("SELECT count(*) FROM (SELECT prod_code FROM pack GROUP BY 1 HAVING count(DISTINCT molecule_desc) > 1)") > 0
    assert one("SELECT count(*) - count(DISTINCT pack_desc) FROM pack") > 0
    assert q(con, "SELECT count(*) FILTER (WHERE molecule_desc IS NULL), count(*) FILTER (WHERE plain_combination IS NULL), "
                  "count(*) FILTER (WHERE molecule_count IS NULL), count(*) FILTER (WHERE molecule_desc IS NULL AND "
                  "plain_combination IS NULL AND molecule_count IS NULL) FROM pack")[0] == (1, 1, 1, 1)
    assert one("SELECT count(*) FROM pack WHERE pack_launch_yyyymm = 0") > 0
    assert one("SELECT count(*) FROM pack WHERE prod_launch_yyyymm = 0") > 0
    assert one("SELECT count(*) FROM pack WHERE (pack_launch_yyyymm = 0) <> (pack_launch_month IS NULL) "
               "OR (prod_launch_yyyymm = 0) <> (prod_launch_month IS NULL)") == 0
    assert one("SELECT count(*) FROM pack WHERE pack_launch_yyyymm > 0 AND prod_launch_yyyymm > 0 "
               "AND pack_launch_yyyymm < prod_launch_yyyymm") == 0
    assert one("SELECT count(*) FROM (SELECT pfc FROM fact_pack_month GROUP BY 1 HAVING max(value_cr) = 0 "
               "AND max(units_k) = 0)") > 0
    assert one("SELECT count(*) FROM pack WHERE index_desc <> brand || ' : ' || subgroup || ' : ' || "
               "manufacturer_desc || ' : ' || CAST(prod_code AS VARCHAR)") == 0
    assert one("SELECT count(*) FROM pack WHERE molecule_desc IS NOT NULL AND (molecule_count <> "
               "len(string_split(molecule_desc, ' + ')) OR plain_combination <> CASE WHEN molecule_count = 1 "
               "THEN 'Plain' ELSE 'Combination' END)") == 0
    dom = {c: {r[0] for r in q(con, f"SELECT DISTINCT {c} FROM pack WHERE {c} IS NOT NULL")}
           for c in ("acute_chronic", "indian_mnc", "plain_combination")}
    assert dom == {"acute_chronic": {"ACUTE", "CHRONIC"}, "indian_mnc": {"INDIAN", "MNC"},
                   "plain_combination": {"Plain", "Combination"}}


def test_snapshot_and_price_rules(con):
    # snapshots = sums of the monthly fact (MONTH, calendar YTD, MAT), as the M3 tests prove for the source
    assert q(con, """
        WITH f AS (SELECT s.pfc, s.snapshot_year, s.value_month, s.value_ytd, s.value_mat, s.units_mat, s.qty_ytd,
                          sum(f.value_cr) FILTER (WHERE f.period = s.snapshot_month) AS m,
                          sum(f.value_cr) FILTER (WHERE year(f.period) = s.snapshot_year AND f.period <= s.snapshot_month) AS y,
                          sum(f.value_cr) FILTER (WHERE f.period > s.snapshot_month - INTERVAL 12 MONTH AND f.period <= s.snapshot_month) AS t,
                          sum(f.units_k) FILTER (WHERE f.period > s.snapshot_month - INTERVAL 12 MONTH AND f.period <= s.snapshot_month) AS u,
                          sum(f.qty_k) FILTER (WHERE year(f.period) = s.snapshot_year AND f.period <= s.snapshot_month) AS qy
                   FROM pack_snapshot s JOIN fact_pack_month f USING (pfc) GROUP BY ALL)
        SELECT count(*) FROM f WHERE abs(value_month - m) > 1e-9 OR abs(value_ytd - y) > 1e-9 OR abs(value_mat - t) > 1e-9
                                  OR abs(units_mat - u) > 1e-9 OR abs(qty_ytd - qy) > 1e-9""")[0][0] == 0
    assert q(con, "SELECT count(*) FROM pack_snapshot WHERE abs(ssa_mat_value_cr + hsa_mat_value_cr + dsa_mat_value_cr "
                  "- tsa_mat_value_cr) > 1e-9 * greatest(1, abs(tsa_mat_value_cr))")[0][0] == 0
    assert q(con, "SELECT count(*) FROM pack_snapshot WHERE ni_24m_mat_value_cr IS NOT NULL "
                  "AND abs(ni_24m_mat_value_cr - value_mat) > 1e-12")[0][0] == 0
    assert q(con, "SELECT count(*) FROM pack_snapshot WHERE ni_24m_mat_value_cr IS NOT NULL")[0][0] > 0
    # price = 1e4 x value / units where units > 0 (source PR definition); carried forward when units = 0
    assert q(con, "SELECT count(*) FROM pack_price_month p JOIN fact_pack_month f USING (pfc, period) "
                  "WHERE f.units_k > 0 AND abs(p.price_rs - 1e4 * f.value_cr / f.units_k) > 1e-9 * p.price_rs")[0][0] == 0
    assert q(con, "SELECT count(*) FROM pack_price_month WHERE price_rs IS NULL OR price_rs <= 0")[0][0] == 0


# ------------------------------------------------------------------------------------------ engines
def test_sql_python_parity_full_m5_matrix(con, eng):
    """The UNCHANGED M5 independent cross-validation matrix (period + trend) on synthetic data."""
    from pci_analytics.validation import run_matrix
    res = run_matrix(con, eng)
    failed = [r.summary() for r in res if not r.passed]
    assert len(res) >= 50 and not failed, failed[:3]


def test_api_functions_run(api):
    A = "2024-05-01"
    assert api.get_market_performance("subgroup", A, "MAT")["rows"]
    top = api.get_brand_performance(A, "MAT", top_n=5)["rows"]
    assert len(top) == 5 and all(r["entity_key"].isdigit() for r in top)
    assert api.get_company_performance(A, "YTD", top_n=None)["rows"]
    assert api.get_segment_analysis("acute_chronic", A, "MAT")["rows"]
    assert len(api.get_market_trends("total", "TOTAL")["rows"]) == 36
    ev = api.get_market_performance("total", A, "MAT")
    assert ev["evidence"]["source_sha256"] == json.loads(COMMITTED_FP.read_text(encoding="utf-8"))["fingerprint_sha256"]


def test_opportunity_scoring_runs(eng):
    from pci_analytics.opportunity import score_all
    for level in ("product", "market"):
        for basis, anchor in (("MAT", dt.date(2024, 5, 1)), ("YTD", dt.date(2024, 3, 1))):
            rows = score_all(eng, level, anchor, basis)["rows"]
            scored = [r for r in rows if r["score_status"] == "SCORED"]
            insuff = [r for r in rows if r["score_status"] == "INSUFFICIENT_EVIDENCE"]
            assert scored and insuff, (level, basis)
            assert all(0 <= r["score"] <= 100 and r["opportunity_rank"] for r in scored)
            assert all(r["score"] is None and r["opportunity_rank"] is None for r in insuff)
            assert sorted(r["opportunity_rank"] for r in scored) == list(range(1, len(scored) + 1))
            for r in scored[:200]:
                assert math.isclose(sum(c["weighted_contribution"] for c in r["components"]), r["score"], abs_tol=1e-9)


def test_scenarios_run_and_reject(api, eng):
    from pci_analytics.api import AnalyticsError
    from pci_analytics.scenario import run_scenario
    A = "2024-05-01"
    prod = api.get_brand_performance(A, "MAT", top_n=1)["rows"][0]["entity_key"]
    sub = api.get_market_performance("subgroup", A, "MAT", top_n=1)["rows"][0]["entity_key"]
    co = api.get_company_performance(A, "MAT", top_n=1)["rows"][0]["entity_key"]
    r = run_scenario(eng, "PRICE_VOLUME_CHANGE", "product", prod, {"price_change_pct": 10, "volume_change_pct": -5}, A, "MAT")
    b = r["baseline"]["value_cr"]
    assert math.isclose(r["scenario_result"]["value_cr"], b * 1.10 * 0.95, rel_tol=1e-12)
    assert r["baseline"]["kind"] == "OBSERVED" and r["scenario_result"]["kind"] == "CALCULATED"
    assert run_scenario(eng, "VOLUME_CHANGE", "company", co, {"volume_change_pct": 20}, A, "YTD")["status"] == "OK"
    assert run_scenario(eng, "PRICE_CHANGE", "product", prod, {"price_change_pct": -20}, A, "MONTH")["status"] == "OK"
    g = run_scenario(eng, "MARKET_GROWTH", "subgroup", sub, {"market_growth_pct": 8}, A, "MAT")
    assert math.isclose(g["scenario_result"]["value_cr"], g["baseline"]["value_cr"] * 1.08, rel_tol=1e-12)
    s = run_scenario(eng, "MARKET_SHARE", "company", co, {"target_share_pct": 12.5}, A, "MAT", "total", "TOTAL")
    assert math.isclose(s["scenario_result"]["share_pct"], 12.5)
    for bad in [("PRICE_CHANGE", "product", prod, {"price_change_pct": -100}),
                ("MARKET_GROWTH", "product", prod, {"market_growth_pct": 5}),
                ("PRICE_ELASTICITY", "product", prod, {"price_change_pct": 5})]:
        with pytest.raises(AnalyticsError):
            run_scenario(eng, *bad, A, "MAT")


# ------------------------------------------------------------------------------------------ dataset switch
def _schema_in_subprocess(value):
    env = {**os.environ}
    env.pop("PCI_DATASET", None)
    env.pop("PCI_IMS_DATA_DIR", None)
    if value is not None:
        env["PCI_DATASET"] = value
    return subprocess.run([PY, "-c", "import sys; sys.path.insert(0, 'python'); from pci_data import schema as s; "
                                     "print(s.DATASET, s.PROCESSED_DIR.name, s.MANIFEST_PATH.parent.name)"],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def test_dataset_switch():
    """P1: synthetic is the default; the licensed data needs PCI_DATASET=ims plus an external PCI_IMS_DATA_DIR."""
    assert _schema_in_subprocess(None).stdout.split() == ["synthetic", "synthetic", "synthetic"]
    assert _schema_in_subprocess("").stdout.split() == ["synthetic", "synthetic", "synthetic"]
    assert _schema_in_subprocess("Synthetic").stdout.split() == ["synthetic", "synthetic", "synthetic"]
    for value in ("../../elsewhere", "private", "processed", "IMS2"):
        bad = _schema_in_subprocess(value)
        assert bad.returncode != 0 and "PCI_DATASET must be one of" in bad.stderr, value
    ims = _schema_in_subprocess("ims")                      # no PCI_IMS_DATA_DIR: fail closed, no fallback
    assert ims.returncode != 0 and "requires PCI_IMS_DATA_DIR" in ims.stderr and not ims.stdout


def test_powerbi_synthetic_build_never_overwrites_private_project(tmp_path):
    from pci_data.schema import DATASETS
    from pci_powerbi import build
    with pytest.raises(ValueError, match="refusing"):
        build.build(out_dir=build.DASH, processed_dir=DATASETS["synthetic"], anchor_label="May 2024")
    info = build.build(out_dir=tmp_path, processed_dir=DATASETS["synthetic"], anchor_label="May 2024")
    expr = (tmp_path / f"{build.NAME}.SemanticModel" / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
    assert "data\\synthetic" in expr and "data\\processed" not in expr
    assert info["measures"] == len(__import__("pci_powerbi.model", fromlist=["x"]).measure_names())


# ------------------------------------------------------------------------------------------ Phase 3: app surfaces
def test_dataset_flag_and_dq_tool_on_synthetic(api):
    from pci_app.tools import ToolRegistry
    reg = ToolRegistry(api)
    meta = reg.invoke("get_application_metadata", {})["result"]
    assert meta["dataset"] == "synthetic"                          # drives the UI "Synthetic data" badge
    dq = reg.invoke("get_data_quality_status", {})
    assert dq["ok"] and dq["result"]["status"] == "PASS" and dq["result"]["coverage"]["geography"] == "none (national)"
    assert all(r["status"] in ("PASS", "INFO") for r in dq["result"]["rows"])


def test_ui_synthetic_indicator_markup():
    html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="ds-badge" class="badge b-synthetic hidden"' in html and 'id="me-syn-note" class="callout warn hidden"' in html
    assert 'm.dataset === "synthetic"' in js and 'tool("get_data_quality_status")' in js
