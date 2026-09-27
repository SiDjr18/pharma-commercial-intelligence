"""M5: independent Python metric functions and entity_period logic on synthetic data.

These tests need no IMS data: a tiny in-memory engine is built from hand-made packs, so every
expected number can be verified by hand.
"""
import datetime as dt
import math

import pyarrow as pa
import pytest

from pci_analytics import metrics
from pci_analytics.engine import DataEngine

D = dt.date


# ---------------------------------------------------------------- pure functions
@pytest.mark.parametrize("cur,prior,expected", [
    (110, 100, 10.0), (50, 100, -50.0), (0, 100, -100.0), (100, 100, 0.0),
    (5, 0, None), (0, 0, None), (5, None, None), (None, 5, None),
])
def test_growth_pct(cur, prior, expected):
    g = metrics.growth_pct(cur, prior)
    assert (g is None and expected is None) or math.isclose(g, expected, abs_tol=1e-12)


def test_growth_never_zero_filled():
    assert metrics.growth_pct(10, 0) is None and metrics.growth_pct(0, 0) is None


@pytest.mark.parametrize("cur,prior,status", [
    (1, 1, "ok"), (0, 1, "ok"), (1, 0, "prior_zero"), (0, 0, "no_sales"), (1, None, "prior_unavailable")])
def test_growth_status(cur, prior, status):
    assert metrics.growth_status(cur, prior) == status


def test_share_contribution_ei():
    assert metrics.share_pct(25, 100) == 25.0
    assert metrics.share_pct(0, 0) is None and metrics.share_pct(1, None) is None
    assert metrics.contribution_pp(30, 20, 200) == 5.0
    assert metrics.contribution_pp(30, None, 200) is None and metrics.contribution_pp(30, 20, 0) is None
    # entity 20 -> 30 (+50%), market 200 -> 220 (+10%): EI = 100 * 1.5 / 1.1
    assert math.isclose(metrics.evolution_index(30, 20, 220, 200), 100 * 1.5 / 1.1)
    assert metrics.evolution_index(30, 0, 220, 200) is None


# ---------------------------------------------------------------- synthetic engine
PERIODS = [dt.date(2021 + (5 + i) // 12, (5 + i) % 12 + 1, 1) for i in range(36)]  # 2021-06 .. 2024-05


def _pack(pfc, prod, subgroup, company="C1", brand="B", molecule="M1", plain="Plain", count=1):
    return {"pfc": pfc, "prod_code": prod, "brand": brand, "company": company, "manufacturer_code": 1,
            "manufacturer_desc": company, "supergroup": "SG", "therapy_group": "G", "subgroup": subgroup,
            "molecule_desc": molecule, "plain_combination": plain, "molecule_count": count,
            "acute_chronic": "ACUTE", "indian_mnc": "INDIAN", "form_short_desc": "Oral Solids", "nfc1": "A",
            "index_desc": f"{brand} : {subgroup} : {company} : {prod}"}


def _engine(values):
    """values: pfc -> function(period_index 0..35) -> value; units = value * 10, qty = value * 100."""
    rows = {"pfc": [], "period": [], "value_cr": [], "units_k": [], "qty_k": []}
    for pfc, f in values.items():
        for i, p in enumerate(PERIODS):
            v = float(f(i))
            rows["pfc"].append(pfc); rows["period"].append(p)
            rows["value_cr"].append(v); rows["units_k"].append(v * 10); rows["qty_k"].append(v * 100)
    fact = pa.table(rows, schema=pa.schema([("pfc", pa.int64()), ("period", pa.date32()), ("value_cr", pa.float64()),
                                            ("units_k", pa.float64()), ("qty_k", pa.float64())]))
    return fact


@pytest.fixture(scope="module")
def syn():
    packs = [
        _pack(1, 10, "S1", brand="ALPHA"),                    # constant 1 / month
        _pack(2, 10, "S2", brand="ALPHA"),                    # same product, other subgroup: 2 / month
        _pack(3, 20, "S1", brand="ALPHA", company="C2"),      # same display brand, other company: grows
        _pack(4, 30, "S3", brand="SOLO", molecule=None, plain=None, count=None),   # one-product market, launched 2023
        _pack(5, 40, "S4", brand="DEAD"),                     # zero market (never sells)
        _pack(6, 50, "S1", brand="TIE"),                      # same value as pack 1 -> tie with product 10 in S1
    ]
    values = {1: lambda i: 1, 2: lambda i: 2, 3: lambda i: 1 + i, 4: lambda i: 3 if i >= 19 else 0,
              5: lambda i: 0, 6: lambda i: 1}
    return DataEngine(fact=_engine(values), packs=packs)


def _by_key(rows):
    return {r["entity_key"]: r for r in rows}


def test_mat_values_and_growth_by_hand(syn):
    r = _by_key(metrics.entity_period(syn, "subgroup", D(2024, 5, 1), "MAT"))
    # S1 = pack1 (12) + pack3 (sum of 1+i for i=24..35 = 12 + 354) + pack6 (12)
    assert r["S1"]["value_cur"] == 12 + (12 + sum(range(24, 36))) + 12
    assert r["S1"]["value_prior"] == 12 + (12 + sum(range(12, 24))) + 12
    assert r["S2"]["value_cur"] == 24 and r["S2"]["value_growth_pct"] == 0.0
    assert r["S3"]["value_prior"] == 3 * 5 and r["S3"]["value_cur"] == 36     # launched period index 19 (2023-01)
    assert r["S4"]["value_growth_pct"] is None and r["S4"]["value_growth_status"] == "no_sales"
    total = sum(x["value_cur"] for x in r.values())
    assert math.isclose(sum(x["value_share_pct"] for x in r.values() if x["value_share_pct"] is not None), 100)
    assert math.isclose(r["S2"]["value_share_pct"], 24 / total * 100)
    g_total = (total / sum(x["value_prior"] for x in r.values()) - 1) * 100
    assert math.isclose(sum(x["contribution_to_growth_pp"] for x in r.values()), g_total)


def test_prior_zero_status(syn):
    r = _by_key(metrics.entity_period(syn, "product", D(2023, 5, 1), "MAT"))
    assert r["30"]["value_prior"] == 0 and r["30"]["value_cur"] > 0
    assert r["30"]["value_growth_pct"] is None and r["30"]["value_growth_status"] == "prior_zero"
    assert r["30"]["evolution_index"] is None


def test_missing_prior_first_mat(syn):
    rows = metrics.entity_period(syn, "total", D(2022, 5, 1), "MAT")
    assert rows[0]["value_prior"] is None and rows[0]["value_growth_status"] == "prior_unavailable"
    assert rows[0]["prior_start"] is None and rows[0]["contribution_to_growth_pp"] is None


def test_incomplete_current_window_is_empty(syn):
    assert metrics.entity_period(syn, "total", D(2022, 4, 1), "MAT") == []
    assert metrics.entity_period(syn, "total", D(2021, 12, 1), "YTD") == []


def test_one_product_market(syn):
    rows = metrics.entity_period(syn, "product", D(2024, 5, 1), "MAT", "subgroup", "S3")
    assert len(rows) == 1 and rows[0]["value_share_pct"] == 100.0 and rows[0]["rank_value"] == 1


def test_zero_market_denominator(syn):
    rows = metrics.entity_period(syn, "product", D(2024, 5, 1), "MAT", "subgroup", "S4")
    assert len(rows) == 1 and rows[0]["scope_value_cur"] == 0
    assert rows[0]["value_share_pct"] is None and rows[0]["contribution_to_growth_pp"] is None


def test_product_spanning_subgroups_split_not_duplicated(syn):
    prod = _by_key(metrics.entity_period(syn, "product", D(2024, 5, 1), "MAT"))["10"]["value_cur"]
    in_s1 = _by_key(metrics.entity_period(syn, "product", D(2024, 5, 1), "MAT", "subgroup", "S1"))["10"]["value_cur"]
    in_s2 = _by_key(metrics.entity_period(syn, "product", D(2024, 5, 1), "MAT", "subgroup", "S2"))["10"]["value_cur"]
    assert prod == in_s1 + in_s2 == 12 + 24


def test_duplicate_display_labels_keep_distinct_keys(syn):
    rows = metrics.entity_period(syn, "product", D(2024, 5, 1), "MAT")
    labels = [r["entity_label"] for r in rows if r["entity_label"].startswith("ALPHA")]
    assert sorted(labels) == ["ALPHA (C1)", "ALPHA (C2)"]
    assert {r["entity_key"] for r in rows} == {"10", "20", "30", "40", "50"}


def test_unclassified_members(syn):
    assert "(UNCLASSIFIED)" in _by_key(metrics.entity_period(syn, "molecule", D(2024, 5, 1), "MAT"))
    assert "(UNCLASSIFIED)" in _by_key(metrics.entity_period(syn, "plain_combination", D(2024, 5, 1), "MAT"))
    assert "(UNCLASSIFIED)" in _by_key(metrics.entity_period(syn, "molecule_count", D(2024, 5, 1), "MAT"))


def test_units_qty_follow_same_logic(syn):
    r = _by_key(metrics.entity_period(syn, "subgroup", D(2024, 5, 1), "YTD"))["S2"]
    assert r["units_cur"] == r["value_cur"] * 10 and r["qty_cur"] == r["value_cur"] * 100
    assert r["units_growth_pct"] == r["value_growth_pct"] == 0.0


def test_trend_by_hand(syn):
    t = metrics.entity_trend(syn, "subgroup", "S2")
    assert len(t) == 36 and all(x["value_cr"] == 2 for x in t)
    assert t[10]["value_mat"] is None and t[11]["value_mat"] == 24
    assert t[22]["value_mat_growth_pct"] is None and t[23]["value_mat_growth_pct"] == 0.0
    assert t[6]["value_ytd"] is None                      # 2021-12: calendar YTD 2021 incomplete
    assert t[7]["value_ytd"] == 2 and t[11]["value_ytd"] == 10   # 2022-01, 2022-05
    assert t[12]["value_growth_pct"] == 0.0 and t[11]["value_growth_pct"] is None
    assert metrics.entity_trend(syn, "subgroup", "NOPE") == []


def test_scope_filter(syn):
    rows = metrics.entity_period(syn, "company", D(2024, 5, 1), "MAT", "subgroup", "S2")
    assert [r["entity_key"] for r in rows] == ["C1"] and rows[0]["value_cur"] == 24
    assert metrics.entity_period(syn, "company", D(2024, 5, 1), "MAT", "subgroup", "NOPE") == []
