"""Contract tests for the deterministic analytics functions (python/pci_analytics)."""
import json

import pytest

from pci_analytics import AnalyticsError, CommercialAnalytics

ENVELOPE = {"function", "filters", "period", "units", "row_count", "total_rows", "rows", "evidence", "caveats"}


@pytest.fixture(scope="module")
def api(con):
    return CommercialAnalytics(con)


@pytest.mark.ims
def test_envelope_and_json(api):
    out = api.get_market_performance("supergroup", "2024-05-01", "MAT", top_n=5)
    assert set(out) == ENVELOPE
    assert out["row_count"] == 5 and out["total_rows"] == 25
    assert [r["rank_value"] for r in out["rows"]] == [1, 2, 3, 4, 5]
    assert out["period"]["basis_label"].startswith("MAT") and out["period"]["prior_start"] == "2022-06-01"
    assert out["evidence"]["source_sha256"] and "market_performance" in out["evidence"]["sql"]
    json.dumps(out)


@pytest.mark.ims
def test_each_function_runs(api):
    sg = api.get_market_performance("subgroup", top_n=1)["rows"][0]["entity_key"]
    pc = api.get_brand_performance(top_n=1)["rows"][0]["entity_key"]
    assert api.get_market_trends("subgroup", sg)["row_count"] == 36
    assert api.get_brand_performance("2024-05-01", "YTD", "subgroup", sg, top_n=10)["row_count"] <= 10
    assert api.get_brand_growth(pc)["row_count"] == 36
    share = api.get_brand_share(pc, "total", "TOTAL")["rows"][0]
    assert share["entity_key"] == pc and share["value_share_pct"] > 0
    assert api.get_company_performance(top_n=3)["row_count"] == 3
    assert api.get_therapy_performance("subgroup", within_supergroup="CARDIAC")["row_count"] > 0
    seg = api.get_segment_analysis("acute_chronic", market_level="supergroup", market_key="CARDIAC")
    assert abs(sum(r["value_share_pct"] for r in seg["rows"]) - 100) < 1e-9


def test_company_filter_keeps_market_denominator(api):
    top = api.get_brand_performance(top_n=1)["rows"][0]
    out = api.get_brand_performance(company=top["company"], top_n=None)
    assert all(r["company"] == top["company"] for r in out["rows"])
    assert abs(out["rows"][0]["scope_value_cur"] - top["scope_value_cur"]) <= 1e-9 * top["scope_value_cur"]


def test_find_products_disambiguates_shared_brand_names(api, con):
    name = con.execute("""SELECT brand FROM pack GROUP BY 1 HAVING count(DISTINCT company) > 1
                          ORDER BY brand LIMIT 1""").fetchone()[0]
    rows = [r for r in api.find_products(name)["rows"] if r["brand"] == name]
    assert len({r["prod_code"] for r in rows}) > 1 and len({r["company"] for r in rows}) > 1


@pytest.mark.parametrize("call,code", [
    (lambda a: a.get_market_performance("subgroup", "2024-05-01", "FY"), "invalid_parameter"),
    (lambda a: a.get_market_performance("subgroup", "not-a-date", "MAT"), "invalid_parameter"),
    (lambda a: a.get_market_performance("subgroup", "2025-01-01", "MAT"), "period_unavailable"),
    (lambda a: a.get_market_performance("subgroup", "2022-01-01", "MAT"), "period_unavailable"),
    (lambda a: a.get_market_performance("subgroup", "2021-10-01", "YTD"), "period_unavailable"),
    (lambda a: a.get_market_performance("geography"), "unsupported"),
    (lambda a: a.get_segment_analysis("channel"), "unsupported"),
    (lambda a: a.get_segment_analysis("hsa"), "unsupported"),
    (lambda a: a.get_market_performance("company"), "invalid_parameter"),
    (lambda a: a.get_market_trends("subgroup", "NO SUCH SUBGROUP"), "not_found"),
    (lambda a: a.get_brand_share("999999999", "total", "TOTAL"), "not_found"),
    (lambda a: a.get_brand_performance(company="NO SUCH COMPANY"), "not_found"),
    (lambda a: a.get_market_performance("subgroup", top_n=0), "invalid_parameter"),
    (lambda a: a.find_products("x"), "invalid_parameter"),
])
def test_errors(api, call, code):
    with pytest.raises(AnalyticsError) as e:
        call(api)
    assert e.value.code == code
    assert e.value.to_dict()["error"]["code"] == code


def test_brand_share_outside_market_is_not_found(api, con):
    pc, sg = con.execute("""SELECT p.prod_code, t.subgroup FROM dim_product p, dim_therapy t
                            WHERE NOT EXISTS (SELECT 1 FROM pack k WHERE k.prod_code = p.prod_code AND k.subgroup = t.subgroup)
                            ORDER BY 1, 2 LIMIT 1""").fetchone()
    with pytest.raises(AnalyticsError) as e:
        api.get_brand_share(str(pc), "subgroup", sg)
    assert e.value.code == "not_found"


def test_prior_unavailable_caveat(api):
    out = api.get_market_performance("total", "2022-05-01", "MAT")
    assert out["rows"][0]["value_growth_pct"] is None
    assert any("Comparison window predates" in c for c in out["caveats"])
