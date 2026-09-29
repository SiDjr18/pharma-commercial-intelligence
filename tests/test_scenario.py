"""M7: deterministic scenario engine — hand-calculated cases, unit conversion, source price
reconciliation, baseline parity with M4 SQL, validation/errors, and fault injection."""
import datetime as dt
import math

import pyarrow as pa
import pytest

from pci_analytics import AnalyticsError, scenario as S
from pci_analytics.domains import PyCommercialAnalytics
from pci_analytics.engine import DataEngine
from pci_analytics.validation import _sql_rows

PERIODS = [dt.date(2021 + (5 + i) // 12, (5 + i) % 12 + 1, 1) for i in range(36)]
MAY24 = "2024-05-01"
approx = lambda x: pytest.approx(x, rel=1e-12, abs=1e-15)  # noqa: E731


# ======================================================================= synthetic data
def _pack(pfc, prod, sg, company):
    return {"pfc": pfc, "prod_code": prod, "brand": f"B{prod}", "company": company, "manufacturer_code": 1,
            "manufacturer_desc": company, "supergroup": "SG", "therapy_group": "G", "subgroup": sg,
            "molecule_desc": "M", "plain_combination": "Plain", "molecule_count": 1, "acute_chronic": "ACUTE",
            "indian_mnc": "INDIAN", "form_short_desc": "Oral Solids", "nfc1": "A",
            "index_desc": f"B{prod} : {sg} : {company} : {prod}"}


# pfc, prod, subgroup, company, (value_cr, units_k) in May 2024, (value_cr, units_k) in every other month
SPEC = [
    (1, 10, "S1", "C1", (0.002, 1.0), (0.004, 2.0)),    # Rs 20 / pack
    (2, 10, "S1", "C1", (0.003, 1.0), (0.006, 2.0)),    # Rs 30 / pack
    (3, 20, "S1", "C2", (0.005, 0.5), (0.010, 1.0)),    # Rs 100 / pack
    (4, 30, "S2", "C1", (0.0, 0.0), (0.001, 0.1)),      # no sales in May 2024
    (5, 40, "S3", "C2", (0.0, 0.0), (0.0, 0.0)),        # dormant market
]


@pytest.fixture(scope="module")
def syn():
    rows = {"pfc": [], "period": [], "value_cr": [], "units_k": [], "qty_k": []}
    for pfc, prod, sg, co, may, other in SPEC:
        for i, p in enumerate(PERIODS):
            v, u = may if i == 35 else other
            rows["pfc"].append(pfc); rows["period"].append(p)
            rows["value_cr"].append(v); rows["units_k"].append(u); rows["qty_k"].append(u * 10)
    fact = pa.table(rows, schema=pa.schema([("pfc", pa.int64()), ("period", pa.date32()), ("value_cr", pa.float64()),
                                            ("units_k", pa.float64()), ("qty_k", pa.float64())]))
    return DataEngine(fact=fact, packs=[_pack(*s[:4]) for s in SPEC])


def run(eng, *a, **k):
    return S.run_scenario(eng, *a, **k)


def guard_failures(eng) -> list[str]:
    """Every hand-verified M7 expectation; returns violations (empty = all correct)."""
    f = []

    def check(name, cond):
        if not cond:
            f.append(name)
    try:
        # unit conversion
        check("price conversion 1 crore / 1,000 packs", S.price_rs(1.0, 1.0) == 10_000.0)
        check("price conversion pack1", S.price_rs(0.002, 1.0) == pytest.approx(20.0, rel=1e-12))
        check("value from price", S.value_from_price(10_000.0, 1.0) == pytest.approx(1.0, rel=1e-12))
        # PRICE_CHANGE: product 10, May 2024: value 0.005 cr, units 2.0 ('000), price Rs 25
        r = run(eng, "PRICE_CHANGE", "product", "10", {"price_change_pct": 10}, MAY24, "MONTH")
        b, s, c, p = r["baseline"], r["scenario_result"], r["absolute_change"], r["percentage_change"]
        check("baseline value", b["value_cr"] == approx(0.005))
        check("baseline units", b["units_k"] == approx(2.0))
        check("baseline price", b["price_rs_per_pack"] == approx(25.0))
        check("baseline price per counting unit", b["price_rs_per_counting_unit"] == approx(2.5))
        check("price-only: scenario price", s["price_rs_per_pack"] == approx(27.5))
        check("price-only: units unchanged", s["units_k"] == approx(2.0) and c["units_k"] == 0)
        check("price-only: scenario value", s["value_cr"] == approx(0.0055))
        check("price-only: abs change", c["value_cr"] == approx(0.0005))
        check("price-only: pct change", p["value_pct"] == approx(10.0) and p["units_pct"] == 0.0)
        # VOLUME_CHANGE +20 %
        r = run(eng, "VOLUME_CHANGE", "product", "10", {"volume_change_pct": 20}, MAY24, "MONTH")
        s, p = r["scenario_result"], r["percentage_change"]
        check("volume-only: price unchanged", s["price_rs_per_pack"] == approx(25.0) and p["price_pct"] == 0.0)
        check("volume-only: units", s["units_k"] == approx(2.4) and s["qty_k"] == approx(24.0))
        check("volume-only: value", s["value_cr"] == approx(0.006) and p["value_pct"] == approx(20.0))
        # PRICE_VOLUME_CHANGE +10 % price, +20 % volume
        r = run(eng, "PRICE_VOLUME_CHANGE", "product", "10", {"price_change_pct": 10, "volume_change_pct": 20},
                MAY24, "MONTH")
        d = r["absolute_change"]["value_decomposition_cr"]
        check("combined value", r["scenario_result"]["value_cr"] == approx(0.0066))
        check("combined pct", r["percentage_change"]["value_pct"] == approx(32.0))
        check("decomposition", (d["price_effect"], d["volume_effect"], d["price_x_volume_interaction"]) ==
              (approx(0.0005), approx(0.001), approx(0.0001)))
        # MAT baseline: 11 x (0.004 + 0.006) + 0.005 = 0.115 cr; 11 x 4 + 2 = 46 ('000); Rs 25
        r = run(eng, "PRICE_CHANGE", "product", "10", {"price_change_pct": -20}, MAY24, "MAT")
        check("MAT baseline", r["baseline"]["value_cr"] == approx(0.115) and r["baseline"]["units_k"] == approx(46.0))
        check("MAT scenario", r["scenario_result"]["value_cr"] == approx(0.092))
        # MARKET_GROWTH S1 (May 2024 value 0.010) +5 %
        r = run(eng, "MARKET_GROWTH", "subgroup", "S1", {"market_growth_pct": 5}, MAY24, "MONTH")
        check("market growth", r["scenario_result"]["value_cr"] == approx(0.0105)
              and r["percentage_change"]["value_pct"] == approx(5.0))
        # MARKET_SHARE product 10 in S1: 0.005 / 0.010 = 50 %; target 60 %, market +10 %
        r = run(eng, "MARKET_SHARE", "product", "10", {"target_share_pct": 60}, MAY24, "MONTH", "subgroup", "S1")
        check("share baseline", r["baseline"]["share_pct"] == approx(50.0) and r["baseline"]["market_value_cr"] == approx(0.010))
        check("share scenario", r["scenario_result"]["value_cr"] == approx(0.006)
              and r["absolute_change"]["share_pp"] == approx(10.0) and r["percentage_change"]["value_pct"] == approx(20.0))
        r = run(eng, "MARKET_SHARE", "product", "10", {"target_share_pct": 60, "market_growth_pct": 10}, MAY24,
                "MONTH", "subgroup", "S1")
        check("share + growth", r["scenario_result"]["value_cr"] == approx(0.0066)
              and r["scenario_result"]["market_value_cr"] == approx(0.011))
        # labels / kinds / units
        check("kinds", (r["baseline"]["kind"], r["assumptions"]["kind"], r["scenario_result"]["kind"],
                        r["absolute_change"]["kind"], r["percentage_change"]["kind"]) ==
              ("OBSERVED", "ASSUMED", "CALCULATED", "CALCULATED", "CALCULATED"))
        check("unit labels", r["units"]["value_cr"].startswith("INR crore") and r["units"]["units_k"].startswith("'000 packs")
              and r["units"]["price_rs_per_pack"].startswith("INR per pack"))
        check("disclaimer", S.DISCLAIMER in r["assumptions_and_limitations"])
        # rejections (never silently corrected)
        for st, a, extra in [("MARKET_SHARE", {"target_share_pct": 150}, ("subgroup", "S1")),
                             ("MARKET_SHARE", {"target_share_pct": -5}, ("subgroup", "S1")),
                             ("PRICE_CHANGE", {"price_change_pct": -150}, (None, None)),
                             ("PRICE_CHANGE", {"price_change_pct": -100}, (None, None)),
                             ("VOLUME_CHANGE", {"volume_change_pct": -150}, (None, None))]:
            try:
                run(eng, st, "product", "10", a, MAY24, "MONTH", *extra)
                f.append(f"accepted invalid {st} {a}")
            except AnalyticsError as e:
                check(f"error code {st} {a}", e.code == "invalid_assumption")
    except Exception as e:  # a crash under a fault also counts as detection
        f.append(f"exception: {type(e).__name__}: {e}")
    return f


def test_hand_calculated(syn):
    assert guard_failures(syn) == []


def test_zero_and_edge_values(syn):
    r = run(syn, "VOLUME_CHANGE", "product", "10", {"volume_change_pct": -100}, MAY24, "MONTH")
    assert r["scenario_result"]["units_k"] == 0 and r["scenario_result"]["value_cr"] == 0
    assert r["scenario_result"]["price_rs_per_pack"] == approx(25.0) and r["percentage_change"]["value_pct"] == -100.0
    assert r["scenario_result"]["price_rs_per_counting_unit"] is None       # undefined with 0 qty
    r = run(syn, "MARKET_SHARE", "product", "10", {"target_share_pct": 0}, MAY24, "MONTH", "subgroup", "S1")
    assert r["scenario_result"]["value_cr"] == 0
    r = run(syn, "PRICE_CHANGE", "product", "10", {"price_change_pct": 0}, MAY24, "MONTH")
    assert r["absolute_change"]["value_cr"] == 0 and r["percentage_change"]["value_pct"] == 0


def test_share_of_product_with_zero_baseline_value(syn):
    r = run(syn, "MARKET_SHARE", "product", "30", {"target_share_pct": 10}, "2024-04-01", "MONTH", "total")
    assert r["baseline"]["value_cr"] > 0
    r = run(syn, "MARKET_SHARE", "product", "30", {"target_share_pct": 10}, MAY24, "MONTH", "total")
    assert r["baseline"]["value_cr"] == 0 and r["percentage_change"]["value_pct"] is None
    assert any("undefined" in x for x in r["assumptions_and_limitations"])


@pytest.mark.parametrize("args,code", [
    (("PRICE_CHANGE", "product", "30", {"price_change_pct": 5}, MAY24, "MONTH"), "insufficient_baseline"),  # 0 units
    (("VOLUME_CHANGE", "product", "40", {"volume_change_pct": 5}, MAY24, "MAT"), "insufficient_baseline"),
    (("MARKET_GROWTH", "subgroup", "S3", {"market_growth_pct": 5}, MAY24, "MAT"), "insufficient_baseline"),
    (("MARKET_SHARE", "product", "40", {"target_share_pct": 5}, MAY24, "MAT", "subgroup", "S3"), "insufficient_baseline"),
])
def test_insufficient_baseline(syn, args, code):
    with pytest.raises(AnalyticsError) as e:
        run(syn, *args)
    assert e.value.code == code


def test_deterministic_and_id(syn):
    a = run(syn, "PRICE_VOLUME_CHANGE", "product", "10", {"price_change_pct": 3, "volume_change_pct": 4}, MAY24, "MAT")
    b = run(syn, "PRICE_VOLUME_CHANGE", "product", "10", {"volume_change_pct": 4, "price_change_pct": 3}, MAY24, "MAT")
    c = run(syn, "PRICE_VOLUME_CHANGE", "product", "10", {"price_change_pct": 3, "volume_change_pct": 5}, MAY24, "MAT")
    assert a == b and a["scenario_id"].startswith("SCN-") and a["scenario_id"] != c["scenario_id"]


def test_response_schema(syn):
    r = run(syn, "PRICE_CHANGE", "product", "10", {"price_change_pct": 1}, MAY24, "YTD")
    required = {"status", "scenario_id", "scenario_type", "methodology_version", "entity", "period", "baseline",
                "assumptions", "scenario_result", "absolute_change", "percentage_change", "units",
                "calculation_basis", "assumptions_and_limitations"}
    assert required <= set(r) and r["status"] == "OK" and r["methodology_version"] == S.METHODOLOGY_VERSION
    assert r["period"]["basis_label"] == "Calendar YTD" and r["period"]["window_start"] == "2024-01-01"
    # outside the explicit disclaimer/limitations, no forecast language may appear anywhere
    text = repr({k: v for k, v in r.items() if k != "assumptions_and_limitations"}).lower()
    for word in ("forecast", "probability", "confidence interval", "predicted", "prediction"):
        assert word not in text
    assert S.DISCLAIMER == r["assumptions_and_limitations"][0]


# ======================================================================= fault injection
def _shifted_window(orig):
    def f(engine, anchor, basis):
        w = orig(engine, anchor, basis)
        from pci_analytics import periods
        return periods.make_window(periods.add_months(w.anchor, -1), w.basis, engine.first_period)
    return f


FAULTS = {
    "incorrect price conversion": ("RUPEES_PER_CRORE", lambda: 100_000),
    "price change applied to units": ("apply_changes",
                                      lambda: (lambda p, u, q, pp, vp: (p, u * (1 + pp / 100), q * (1 + pp / 100)))),
    "volume change applied to price": ("apply_changes",
                                       lambda: (lambda p, u, q, pp, vp: (p * (1 + pp / 100) * (1 + vp / 100), u, q))),
    "incorrect percentage change": ("pct_change", lambda: (lambda b, n: None if not b or not n else (n - b) / n * 100)),
    "incorrect baseline period": ("baseline_window", lambda: _shifted_window(S.baseline_window)),
    "invalid market share accepted": ("ASSUMPTION_BOUNDS",
                                      lambda: {**S.ASSUMPTION_BOUNDS, "target_share_pct": (-1000.0, True, 1000.0, True)}),
    "invalid negative scenario accepted": ("ASSUMPTION_BOUNDS",
                                           lambda: {**S.ASSUMPTION_BOUNDS, "price_change_pct": (-1000.0, True, 1000.0, True),
                                                    "volume_change_pct": (-1000.0, True, 1000.0, True)}),
    "scenario presented as observed": ("CALCULATED", lambda: "OBSERVED"),
    "wrong unit label": ("UNIT_LABELS", lambda: {**S.UNIT_LABELS, "value_cr": "INR lakh"}),
    "incorrect scenario value calculation": ("value_from_price", lambda: (lambda price, units_k: price * units_k)),
}


@pytest.mark.parametrize("fault", list(FAULTS))
def test_fault_injection_detected(syn, monkeypatch, fault):
    attr, make = FAULTS[fault]
    assert guard_failures(syn) == []
    monkeypatch.setattr(S, attr, make())
    assert guard_failures(syn) != [], f"fault not detected: {fault}"
    monkeypatch.undo()
    assert guard_failures(syn) == []


# ======================================================================= real data
@pytest.mark.ims
def test_derived_price_reproduces_source_pr(con):
    """Pack level, all 6 months with source PR (Dec 2023 - May 2024), all therapy areas, both years."""
    rows = con.execute("""SELECT pr.period, p.supergroup, f.value_cr, f.units_k, pr.price_rs
                          FROM pack_price_month pr JOIN fact_pack_month f USING (pfc, period) JOIN pack p USING (pfc)""").fetchall()
    tested, months, areas, years = 0, set(), set(), set()
    for period, sg, v, u, pr in rows:
        d = S.price_rs(v, u)
        if u > 0:
            assert math.isclose(d, pr, rel_tol=1e-12), (period, sg)
            tested += 1
            months.add(period); areas.add(sg); years.add(period.year)
        else:
            assert d is None                    # source carries price forward; derived price is undefined
    assert tested > 450_000 and len(months) == 6 and len(areas) == 25 and years == {2023, 2024}


@pytest.fixture(scope="module")
def api(py_engine):
    return PyCommercialAnalytics(py_engine)


def test_baselines_match_m4_sql(con, api, py_engine):
    from pci_analytics.validation import resolve
    pc, sg = resolve(py_engine, "@rank:product:7"), resolve(py_engine, "@rank:subgroup:4")
    for basis in ("MONTH", "YTD", "MAT"):
        b = api.get_scenario_baseline("product", pc, MAY24, basis)["baseline"]
        s = _sql_rows(con, "SELECT value_cur, units_cur, qty_cur FROM product_performance(?, ?) WHERE entity_key = ?",
                      [dt.date(2024, 5, 1), basis, pc])[0]
        assert (b["value_cr"], b["units_k"], b["qty_k"]) == (approx(s["value_cur"]), approx(s["units_cur"]), approx(s["qty_cur"]))
        m = api.get_scenario_baseline("subgroup", sg, MAY24, basis)["baseline"]
        ms = _sql_rows(con, "SELECT value_cur FROM market_performance('subgroup', ?, ?) WHERE entity_key = ?",
                       [dt.date(2024, 5, 1), basis, sg])[0]
        assert m["value_cr"] == approx(ms["value_cur"])
    top = api.get_brand_performance(market_level="subgroup", market_key=sg, top_n=1)["rows"][0]
    b = api.get_scenario_baseline("product", top["entity_key"], MAY24, "MAT", "subgroup", sg)["baseline"]
    assert b["share_pct"] == approx(top["value_share_pct"]) and b["market_value_cr"] == approx(top["scope_value_cur"])


def test_real_scenarios_arithmetic(api, py_engine):
    from pci_analytics.validation import resolve
    pc = resolve(py_engine, "@rank:product:3")
    r = api.run_scenario("PRICE_VOLUME_CHANGE", "product", pc, {"price_change_pct": 7.5, "volume_change_pct": -4})
    v0 = r["baseline"]["value_cr"]
    assert r["scenario_result"]["value_cr"] == approx(v0 * 1.075 * 0.96)
    assert sum(r["absolute_change"]["value_decomposition_cr"].values()) == approx(r["absolute_change"]["value_cr"])
    assert api.run_scenario("MARKET_GROWTH", "total", "TOTAL", {"market_growth_pct": 3})["scenario_result"]["value_cr"] > 0
    assert api.run_scenario("PRICE_CHANGE", "company", api.get_company_performance(top_n=1)["rows"][0]["entity_key"],
                            {"price_change_pct": 2}, basis="YTD")["percentage_change"]["value_pct"] == approx(2.0)


@pytest.mark.parametrize("call,code", [
    (lambda a: a.run_scenario("PRICE_CHANGE", "product", "X", {"price_change_pct": 1}, basis="FY"), "invalid_parameter"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": 1}, anchor="2025-01-01"), "period_unavailable"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": 1}, anchor="2022-01-01"), "period_unavailable"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": 1}, anchor="2021-10-01", basis="YTD"), "period_unavailable"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "product", "999999999", {"price_change_pct": 1}), "not_found"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "geography", "X", {"price_change_pct": 1}), "unsupported"),
    (lambda a: a.run_scenario("FORECAST", "total", "TOTAL", {}), "unsupported"),
    (lambda a: a.run_scenario("PRICE_ELASTICITY", "total", "TOTAL", {}), "unsupported"),
    (lambda a: a.run_scenario("MAGIC", "total", "TOTAL", {}), "invalid_parameter"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {}), "invalid_assumption"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": 1, "volume_change_pct": 1}), "invalid_assumption"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": True}), "invalid_assumption"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": float("nan")}), "invalid_assumption"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", {"price_change_pct": "5"}), "invalid_assumption"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "total", "TOTAL", "5%"), "invalid_assumption"),
    (lambda a: a.run_scenario("MARKET_GROWTH", "total", "TOTAL", {"market_growth_pct": -101}), "invalid_assumption"),
    (lambda a: a.run_scenario("MARKET_GROWTH", "product", "1", {"market_growth_pct": 5}), "invalid_parameter"),
    (lambda a: a.run_scenario("MARKET_SHARE", "product", "1", {"target_share_pct": 5}), "invalid_parameter"),
    (lambda a: a.run_scenario("MARKET_SHARE", "subgroup", "X", {"target_share_pct": 5}, market_level="total"), "invalid_parameter"),
    (lambda a: a.run_scenario("MARKET_SHARE", "product", "1", {"target_share_pct": 101}, market_level="total"), "invalid_assumption"),
    (lambda a: a.run_scenario("MARKET_SHARE", "company", "NO SUCH", {"target_share_pct": 5}, market_level="total"), "not_found"),
    (lambda a: a.run_scenario("PRICE_CHANGE", "product", "1", {"price_change_pct": 1}, market_level="subgroup", market_key="NO SUCH"), "not_found"),
    pytest.param(*(lambda a: a.get_scenario_baseline("product", "1", market_level="channel", market_key="x"), "unsupported"), marks=pytest.mark.ims),
])
def test_errors(api, call, code):
    with pytest.raises(AnalyticsError) as e:
        call(api)
    assert e.value.code == code


def test_product_outside_market_not_found(api, py_engine):
    subs = {}
    for p in py_engine.packs:
        subs.setdefault(p["prod_code"], set()).add(p["subgroup"])
    pc = min(subs)
    sg = min(p["subgroup"] for p in py_engine.packs if p["subgroup"] not in subs[pc])
    with pytest.raises(AnalyticsError) as e:
        api.run_scenario("MARKET_SHARE", "product", str(pc), {"target_share_pct": 5}, market_level="subgroup", market_key=sg)
    assert e.value.code == "not_found"


def test_real_zero_unit_baseline_is_insufficient(api, py_engine):
    from pci_analytics import metrics
    rows = metrics.entity_period(py_engine, "product", dt.date(2024, 5, 1), "MONTH")
    dead = next(r["entity_key"] for r in rows if r["units_cur"] == 0)
    with pytest.raises(AnalyticsError) as e:
        api.run_scenario("PRICE_CHANGE", "product", dead, {"price_change_pct": 5}, basis="MONTH")
    assert e.value.code == "insufficient_baseline"


@pytest.mark.ims
def test_sql_api_delegates(con, api):
    from pci_analytics import CommercialAnalytics
    s = CommercialAnalytics(con)
    s._opp_engine = api.engine
    args = ("MARKET_GROWTH", "supergroup", "CARDIAC", {"market_growth_pct": 4})
    assert s.run_scenario(*args) == api.run_scenario(*args)
