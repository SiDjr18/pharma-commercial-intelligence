"""M12 — Power BI layer: offline contract tests (no Power BI Desktop needed).

The live reconciliation against Power BI Desktop is `python -m pci_powerbi.reconcile`; its committed,
value-free summary (evaluation/reports/POWER_BI_RECONCILIATION.md) is checked here too.
"""
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

import pytest

from pci_powerbi import build, model, report, theme
from pci_powerbi.reconcile import Result, compare

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboards"
PBIP = DASH / f"{build.NAME}.pbip"
SM, RP = DASH / f"{build.NAME}.SemanticModel", DASH / f"{build.NAME}.Report"
VERS = {"opportunity": "OPP-1.0.0", "fingerprint": "ac4362f187f25004", "scenario": "SCN-1.0.0"}


@pytest.fixture(scope="module")
def tables():
    return {t.name: t for t in model.tables()}


@pytest.fixture(scope="module")
def pages():
    return report.pages("May 2024", VERS)


def _columns(tables):
    return {(t.name, c.name) for t in tables.values() for c in t.columns}


# ---------------------------------------------------------------------------------------- model
def test_model_tables_and_grain(tables):
    assert set(tables) >= {"Pack Month", "Pack", "Product", "Market", "Company", "Period", "Anchor", "Basis",
                           "Opportunity Product", "Opportunity Market", "Metrics"}
    assert "Measures" not in tables                         # reserved name in Power BI (found in M12 QA)
    fact = tables["Pack Month"]
    assert [c.name for c in fact.columns] == ["pfc", "period", "value_cr", "units_k", "qty_k"]
    assert "fact_pack_month.parquet" in fact.source and "pack.parquet" in tables["Pack"].source
    for t in tables.values():                               # no raw Excel, no network source
        assert ".xls" not in t.source.lower() and "http" not in t.source.lower() and "Web.Contents" not in t.source


def test_relationships_are_single_direction_many_to_one_and_keyed_on_codes(tables):
    cols = _columns(tables)
    for name, frm, to in model.RELATIONSHIPS:
        for ref in (frm, to):
            t, c = ref.rsplit(".", 1)
            assert (t.strip("'"), c) in cols, ref
    tos = [to for _, _, to in model.RELATIONSHIPS]
    assert "Product.prod_code" in tos and not any("Brand" in r for _, a, b in model.RELATIONSHIPS for r in (a, b))
    assert "crossFilteringBehavior" not in model.relationships_tmdl()     # default: one direction


def test_no_unsupported_dimensions(tables):
    text = " ".join(c.name.lower() for t in tables.values() for c in t.columns) + " " + \
        " ".join(t.source.lower() for t in tables.values())
    for bad in ("ssa", "hsa", "dsa", "tsa_", "geograph", "region", "state", "city", "channel", "prescriber", "hcp", "promotion"):
        assert not re.search(rf"\b{bad}", text), bad


def test_measures_unique_and_dax_balanced(tables):
    ms = tables["Metrics"].measures
    names = [m.name for m in ms]
    assert len(names) == len(set(names)) and len(names) >= 140
    for m in ms:
        code = re.sub(r'"[^"]*"', '""', m.expr)                    # ignore text inside DAX string literals
        assert code.count("(") == code.count(")"), m.name
        assert code.count("{") == code.count("}"), m.name
    known = set(names)
    for m in ms:
        code = re.sub(r'"[^"]*"', '""', m.expr)
        for ref in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", code):
            if ref not in known and not ref.startswith(("Date", "Value")):
                raise AssertionError(f"{m.name} references unknown measure [{ref}]")


def test_growth_is_blank_not_zero_when_prior_not_positive(tables):
    ms = {m.name: m for m in tables["Metrics"].measures}
    for n in ("Value Growth %", "Units Growth %", "Qty Growth %", "National Growth %", "Market Growth %", "Trend Growth %"):
        e = ms[n].expr
        assert "IF ( p > 0, c / p * 100 - 100 )" in e, n
        assert "COALESCE" not in e and "+ 0" not in e
    assert "ISBLANK ( p ), \"prior_unavailable\"" in ms["Growth Status"].expr


def test_period_logic_matches_sql_definitions(tables):
    ms = {m.name: m.expr for m in tables["Metrics"].measures}
    assert '"YTD", DATE ( YEAR ( a ), 1, 1 )' in ms["Current Start"]           # calendar YTD, not Indian FY
    assert '"MAT", EDATE ( a, -11 )' in ms["Current Start"]
    assert "EDATE ( s, -12 )" in ms["Prior Start"] and "EDATE ( e, -12 )" in ms["Prior End"]
    assert "s >= [First Data Month]" in ms["Current Complete"] and "s >= [First Data Month]" in ms["Prior Complete"]
    sql = (ROOT / "sql" / "periods.sql").read_text(encoding="utf-8")
    assert "make_date(year(anchor), 1, 1)" in sql and "anchor - INTERVAL 11 MONTH" in sql
    assert "BLANK ()" in ms["Anchor Month"] and "BLANK ()" in ms["Basis Selected"]   # multi-select -> nothing


def test_opportunity_is_imported_never_recomputed(tables):
    for m in tables["Metrics"].measures:
        if m.folder and m.folder.startswith("7"):
            assert not re.search(r"0\.[3-6]0?\b", m.expr), m.name        # no component weights in DAX
            assert "RANK" not in m.expr and "PERCENTILE" not in m.expr
    assert "Opportunity Product" in tables and "opportunity_product.parquet" in tables["Opportunity Product"].source


def test_scenario_grids_match_m7_bounds(tables):
    from pci_analytics.scenario import ASSUMPTION_BOUNDS
    src = {n: tables[n].source for n in ("Price Change", "Volume Change", "Market Growth Assumption", "Target Share")}
    assert "GENERATESERIES ( -99, 1000, 1 )" in src["Price Change"] and ASSUMPTION_BOUNDS["price_change_pct"][:2] == (-100.0, False)
    assert "GENERATESERIES ( -100, 1000, 1 )" in src["Volume Change"] and ASSUMPTION_BOUNDS["volume_change_pct"][:2] == (-100.0, True)
    assert "GENERATESERIES ( -100, 1000, 1 )" in src["Market Growth Assumption"]
    assert "GENERATESERIES ( 0, 1000, 1 )" in src["Target Share"] and "[Value] / 10" in src["Target Share"]
    status = next(m.expr for m in tables["Metrics"].measures if m.name == "Scenario Status")
    for code in ("INVALID PARAMETER", "INVALID ASSUMPTION", "INSUFFICIENT BASELINE", "NOT FOUND", "PERIOD UNAVAILABLE"):
        assert code in status
    for st in ("PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE", "MARKET_GROWTH", "MARKET_SHARE"):
        assert st in tables["Scenario Type"].source
    text = json.dumps([m.expr for m in tables["Metrics"].measures]).upper().replace("NO ELASTICITY", "").replace("NOT A FORECAST", "")
    assert "ELASTIC" not in text and "FORECAST" not in text                  # only the disclaimer mentions them


# ---------------------------------------------------------------------------------------- report
def test_every_visual_binds_to_existing_fields(tables, pages):
    cols, meas = _columns(tables), {m.name for m in tables["Metrics"].measures}
    n = 0
    for p in pages:
        for v in p.visuals:
            for f in v.fields():
                ent, prop = report.ref(f)
                if "Measure" in f:
                    assert ent == "Metrics" and prop in meas, (p.name, v.name, prop)
                else:
                    assert (ent, prop) in cols, (p.name, v.name, ent, prop)
                n += 1
    assert n > 200


def test_pages_ia_titles_and_labels(pages):
    names = [p.display for p in pages]
    assert names[:7] == ["Executive Overview", "Market Intelligence", "Brand & Portfolio", "Company Intelligence",
                         "Opportunity Intelligence", "Scenario Planning", "Methodology & Data Quality"]
    for p in pages[:7]:
        title = p.visuals[0].objects["general"][0]["properties"]["paragraphs"][0]["textRuns"][0]["value"]
        assert title.endswith("?"), title                                  # question-led (M11)
    dt_pages = [p for p in pages if p.drillthrough]
    assert {p.drillthrough for p in dt_pages} == {("Product", "Product Code"), ("Company", "Company")}
    assert all(p.hidden for p in dt_pages)
    txt = json.dumps([v.to_json(0) for p in pages for v in p.visuals])
    assert "NOT A FORECAST" in txt and "not a forecast" in txt.lower() and "DESCRIPTIVE OPPORTUNITY SCORING" in txt
    for bad in ("gauge", "donutChart", "pieChart", "3D"):
        assert f'"visualType": "{bad}"' not in txt


def test_product_identity_never_brand_alone(pages):
    for p in pages:
        for v in p.visuals:
            refs = [report.ref(f) for f in v.fields()]
            assert ("Product", "Brand") not in refs, (p.name, v.name)
            if ("Product", "Product") in refs and v.vtype == "tableEx":
                assert ("Product", "Product Code") in refs                 # tables show the key


def test_topn_bars_carry_only_their_topn_measure(pages):
    """Any extra measure (e.g. a tooltip) is non-blank for every category and would pull all 60,079 products
    into a 'Top 15' chart (found by the M12 visual smoke test)."""
    n = 0
    for p in pages:
        for v in p.visuals:
            ms = [report.ref(f)[1] for f in v.fields() if "Measure" in f]
            if any(report.is_topn_measure(m) for m in ms):
                assert "Tooltips" not in v.roles and len(set(ms)) == 1, (p.name, v.name, ms)
                n += 1
    assert n >= 6


def test_slicer_defaults_hold_no_business_values(pages):
    lits = []
    for p in pages:
        for v in p.visuals:
            g = v.objects.get("general", [{}])[0].get("properties", {})
            if "filter" in g:
                lits += [x[0]["Literal"]["Value"] for x in g["filter"]["filter"]["Where"][0]["Condition"]["In"]["Values"]]
    assert lits and set(lits) <= {"'May 2024'", "'MAT'"}


def test_theme_uses_m11_tokens():
    t, th = theme.tokens(), theme.theme()
    assert th["dataColors"][0] == t["c-main"] and th["good"] == t["pos"] and th["bad"] == t["neg"]
    assert th["visualStyles"]["page"]["*"]["background"][0]["color"]["solid"]["color"] == t["paper"]
    css = (ROOT / "app" / "web" / "style.css").read_text(encoding="utf-8")
    assert all(c.lower() in css.lower() for c in th["dataColors"])


# ---------------------------------------------------------------------------------------- artefacts
@pytest.mark.ims
def test_generated_project_is_current(tmp_path):
    """The committed PBIP definition equals a fresh generation (no hand edits, no drift)."""
    build.build(out_dir=tmp_path, anchor_label="May 2024")
    fresh = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    committed = {p.relative_to(DASH).as_posix(): p.read_bytes() for p in DASH.rglob("*")
                 if p.is_file() and ".pbi" not in p.parts and p.name != ".gitkeep"
                 and p.suffix in (".json", ".tmdl", ".pbip", ".pbir", ".pbism")}
    # Power BI Desktop may add its own files (e.g. diagramLayout.json); every generated file must match
    for k, v in fresh.items():
        assert k in committed and committed[k] == v, k


def test_pbip_contains_no_data_and_caches_are_ignored():
    for p in DASH.rglob("*"):
        if p.is_file() and ".pbi" not in p.parts:
            assert p.suffix.lower() not in (".pbix", ".pbit", ".abf", ".parquet", ".csv", ".xlsx"), p
    ign = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for rule in ("*.pbix", "*.pbit", "*.abf", "**/.pbi/"):
        assert rule in ign
    probe = [f"dashboards/{build.NAME}.SemanticModel/.pbi/cache.abf", "dashboards/x.pbix", "dashboards/x.pbit",
             "data/processed/powerbi/opportunity_product.parquet", "evaluation/reports/powerbi_reconciliation.json"]
    r = subprocess.run(["git", "check-ignore", *probe], cwd=ROOT, capture_output=True, text=True)
    assert sorted(r.stdout.split()) == sorted(probe)


def test_committed_reconciliation_summary_has_no_failures_and_no_values():
    md = (ROOT / "evaluation" / "reports" / "POWER_BI_RECONCILIATION.md").read_text(encoding="utf-8")
    row = next(ln for ln in md.splitlines() if re.match(r"\| \d+ \| \d+ \| \d+ \|", ln))
    cases, passed, failed = [int(x) for x in row.split("|")[1:4]]
    assert cases >= 60 and failed == 0 and passed == cases
    assert "FAIL**" not in md
    assert "₹" not in md.split("## Summary")[1]          # values stay in the git-ignored JSON


# ---------------------------------------------------------------------------------------- reconcile logic
def test_compare_blank_is_not_zero_and_tolerance():
    ok = compare(Result("a", "t", "d"), {"k": {"g": None, "v": 1.0}}, {"k": {"g": None, "v": 1.0 + 1e-12}},
                 [("g", "num"), ("v", "num")])
    assert ok.status == "PASS" and ok.blank_checks == 1
    bad = compare(Result("b", "t", "d"), {"k": {"g": None}}, {"k": {"g": 0.0}}, [("g", "num")])
    assert bad.status == "FAIL"
    bad2 = compare(Result("c", "t", "d"), {"k": {"g": 0.0}}, {"k": {"g": None}}, [("g", "num")])
    assert bad2.status == "FAIL"
    far = compare(Result("d", "t", "d"), {"k": {"v": 100.0}}, {"k": {"v": 100.001}}, [("v", "num")])
    assert far.status == "FAIL"
    keys = compare(Result("e", "t", "d"), {"k": {"v": 1.0}, "j": {"v": 1.0}}, {"k": {"v": 1.0}}, [("v", "num")])
    assert keys.status == "FAIL" and keys.missing_keys == 1


def test_bridge_parser_handles_crlf_types_and_blank(monkeypatch, tmp_path):
    from pci_powerbi import desktop
    f = tmp_path / "r.tsv"
    f.write_bytes("Market[Subgroup]\t[v]\t[n]\t[d]\r\nString\tDouble\tInt64\tDateTime\r\nA · B\t1.5\t3\t2024-05-01\r\nC\t\\N\t\\N\t\\N\r\n"
                  .encode("utf-8"))
    monkeypatch.setattr(desktop, "_bridge", lambda *a, **k: f)
    rows = desktop.query(0, "EVALUATE x")
    assert rows == [{"Subgroup": "A · B", "v": 1.5, "n": 3, "d": dt.date(2024, 5, 1)},
                    {"Subgroup": "C", "v": None, "n": None, "d": None}]


def test_bridge_is_local_only():
    ps1 = (ROOT / "python" / "pci_powerbi" / "as_bridge.ps1").read_text(encoding="utf-8")
    assert "Data Source=localhost:" in ps1 and "http" not in ps1.lower()
    src = "".join((ROOT / "python" / "pci_powerbi" / f).read_text(encoding="utf-8")
                  for f in ("desktop.py", "reconcile.py", "export.py", "build.py", "model.py", "report.py"))
    for bad in ("requests", "urllib", "http.client", "socket", "Web.Contents"):
        assert bad not in src


# ---------------------------------------------------------------------------------------- export
def test_export_anchor_set_and_key_safety():
    from pci_analytics.engine import default_engine
    from pci_powerbi.export import assert_keys_powerbi_safe, eligible_anchors
    anchors = eligible_anchors(default_engine())
    assert sum(b == "MAT" for _, b in anchors) == 13 and sum(b == "YTD" for _, b in anchors) == 17
    assert all(b in ("MAT", "YTD") for _, b in anchors)
    assert all(a == b for a, b in assert_keys_powerbi_safe().values())


def test_export_rows_copy_canonical_scores():
    from pci_analytics.engine import default_engine
    from pci_analytics.opportunity import score_all
    from pci_powerbi.export import market_rows, product_rows
    eng, a = default_engine(), dt.date(2024, 5, 1)
    res = score_all(eng, "product", a, "MAT")
    rows = product_rows(res, a, "MAT")
    assert len(rows) == len(res["rows"])
    for r, x in zip(rows[:2000], res["rows"][:2000]):
        assert r["Score"] == x["score"] and r["Opportunity Rank"] == x["opportunity_rank"]
        assert r["Score Status"] == x["score_status"] and r["Matrix Quadrant"] == x["matrix_quadrant"]
        if x["score"] is None:
            assert r["Evidence"].startswith("Insufficient: ")
    m = market_rows(score_all(eng, "market", a, "MAT"), a, "MAT")
    assert {r["Score Status"] for r in m} <= {"SCORED", "INSUFFICIENT_EVIDENCE"}
