"""M6: opportunity scoring — hand-calculated cases, properties on real data, API, fault injection."""
import dataclasses
import datetime as dt
import math

import pyarrow as pa
import pytest

from pci_analytics import AnalyticsError, opportunity as O, opportunity_config as OC
from pci_analytics.domains import PyCommercialAnalytics
from pci_analytics.engine import DataEngine
from pci_analytics.validation import _sql_rows

PINNED_VERSION = "OPP-1.0.0"
PINNED_FINGERPRINT = "ac4362f187f25004"
PINNED_PRODUCT_WEIGHTS = {"market_growth": 0.30, "relative_momentum": 0.40, "market_position": 0.30}
PINNED_MARKET_WEIGHTS = {"market_growth": 0.60, "market_size": 0.40}
A = "2024-05-01"

# ======================================================================= synthetic, hand-calculated
PERIODS = [dt.date(2021 + (5 + i) // 12, (5 + i) % 12 + 1, 1) for i in range(36)]


def _pack(pfc, prod, subgroup, brand, company="C1"):
    return {"pfc": pfc, "prod_code": prod, "brand": brand, "company": company, "manufacturer_code": 1,
            "manufacturer_desc": company, "supergroup": "SGA" if subgroup in ("S1", "S2") else "SGB",
            "therapy_group": "G", "subgroup": subgroup, "molecule_desc": "M", "plain_combination": "Plain",
            "molecule_count": 1, "acute_chronic": "ACUTE", "indian_mnc": "INDIAN", "form_short_desc": "Oral Solids",
            "nfc1": "A", "index_desc": f"{brand} : {subgroup} : {company} : {prod}"}


# pfc, prod, subgroup, brand, company, monthly value before 2023-06 (a), from 2023-06 (b)
SPEC = [
    (1, 10, "S1", "P1", "C1", 1, 2),        # E1: MAT 12 -> 24
    (2, 20, "S1", "P2", "C2", 1, 1),        # E2: 12 -> 12
    (3, 30, "S2", "P3", "C1", 1, 1),        # E3: 12 -> 12  (EI exactly 100)
    (4, 40, "S2", "P4", "C2", 3, 3),        # E4: 36 -> 36
    (5, 50, "S3", "P5", "C1", 0, 1),        # prior 0 -> INSUFFICIENT prior_zero
    (7, 70, "S4", "P7", "C1", 1, 1),        # alone in market -> single_product_market
    (8, 80, "S5", "P8", "C1", 0.01, 0.02),  # prior 0.12 < floor 1 -> below_materiality
    (9, 90, "S6", "P9", "C1", 2, 0),        # market S6 has no current sales -> market_zero_current
    (10, 91, "S6", "P10", "C2", 0, 0),      # dormant
]
EXPECTED_PRODUCT = {"P1 : S1 : C1 : 10": 85.0, "P2 : S1 : C2 : 20": 35.0,
                    "P3 : S2 : C1 : 30": 25.0, "P4 : S2 : C2 : 40": 55.0}
EXPECTED_REASONS = {"P5 : S3 : C1 : 50": "prior_zero", "P7 : S4 : C1 : 70": "single_product_market",
                    "P8 : S5 : C1 : 80": "below_materiality", "P9 : S6 : C1 : 90": "market_zero_current",
                    "P10 : S6 : C2 : 91": "prior_zero"}
EXPECTED_MARKET = {"S1": 60 + 40 * 2 / 3, "S2": 70.0, "S4": 30 + 40 / 3, "S6": 0.0}


@pytest.fixture(scope="module")
def syn():
    rows = {"pfc": [], "period": [], "value_cr": [], "units_k": [], "qty_k": []}
    packs = []
    for pfc, prod, sg, brand, co, a, b in SPEC:
        packs.append(_pack(pfc, prod, sg, brand, co))
        for i, p in enumerate(PERIODS):
            v = float(a if i < 24 else b)
            rows["pfc"].append(pfc); rows["period"].append(p)
            rows["value_cr"].append(v); rows["units_k"].append(v); rows["qty_k"].append(v)
    fact = pa.table(rows, schema=pa.schema([("pfc", pa.int64()), ("period", pa.date32()), ("value_cr", pa.float64()),
                                            ("units_k", pa.float64()), ("qty_k", pa.float64())]))
    return DataEngine(fact=fact, packs=packs)


def _cfg():
    # read DEFAULT_CONFIG at call time so fault injection on the module attribute is visible
    return dataclasses.replace(OC.DEFAULT_CONFIG, min_prior_value_cr=1.0)


def _score(eng, level="product"):
    eng.__dict__.pop("_opportunity_cache", None)
    return {r["entity_key"]: r for r in O.score_all(eng, level, A, "MAT", _cfg())["rows"]}


def guard_failures(eng) -> list[str]:
    """All hand-verifiable M6 invariants on the synthetic dataset; returns the list of violations."""
    f = []
    try:
        p = _score(eng)
        for k, s in EXPECTED_PRODUCT.items():
            if p[k]["score_status"] != O.SCORED or not math.isclose(p[k]["score"], s, abs_tol=1e-9):
                f.append(f"product score {k}")
        for k, reason in EXPECTED_REASONS.items():
            if p[k]["score_status"] != O.INSUFFICIENT or p[k]["insufficient_reason"] != reason or p[k]["score"] is not None:
                f.append(f"insufficient {k}")
        ranks = {k: p[k]["opportunity_rank"] for k in EXPECTED_PRODUCT}
        if [k for k, _ in sorted(ranks.items(), key=lambda x: x[1])] != \
                ["P1 : S1 : C1 : 10", "P4 : S2 : C2 : 40", "P2 : S1 : C2 : 20", "P3 : S2 : C1 : 30"]:
            f.append("product ranking")
        m = _score(eng, "market")
        for k, s in EXPECTED_MARKET.items():
            if not math.isclose(m[k]["score"], s, abs_tol=1e-9):
                f.append(f"market score {k}")
        env = O.scores_envelope(eng, "product", A, "MAT", "subgroup", "S1", cfg=_cfg())
        if {r["subgroup"] for r in env["rows"]} != {"S1"} or env["total_rows"] != 2:
            f.append("market filter")
        c = OC.DEFAULT_CONFIG
        if c.version != PINNED_VERSION or c.fingerprint() != PINNED_FINGERPRINT:
            f.append("methodology version/fingerprint")
        if {x.name: x.weight for x in c.product_components} != PINNED_PRODUCT_WEIGHTS:
            f.append("product weights")
    except Exception as e:  # a fault that crashes the engine is also a detection
        f.append(f"exception: {type(e).__name__}: {e}")
    return f


def test_hand_calculated_synthetic_case(syn):
    assert guard_failures(syn) == []


def test_normalization_by_hand():
    n = O.percentile_normalize({"a": 50.0, "b": 50.0, "c": 0.0, "d": 0.0})
    assert n == {"c": 1 / 6, "d": 1 / 6, "a": 5 / 6, "b": 5 / 6}
    assert O.percentile_normalize({"x": 7.0}) == {"x": 0.5}
    assert O.percentile_normalize({}) == {}
    n = O.percentile_normalize({"a": 1.0, "b": 2.0, "c": 1e12})       # outlier does not stretch the scale
    assert n == {"a": 0.0, "b": 0.5, "c": 1.0}
    n = O.percentile_normalize({"a": 0.1 + 0.2, "b": 0.3})             # float noise is a tie
    assert n["a"] == n["b"] == 0.5


def test_aggregate_and_bounds():
    assert O.aggregate_score([(0.3, 1.0), (0.4, 1.0), (0.3, 1.0)]) == pytest.approx(100.0)
    assert O.aggregate_score([(0.3, 0.0), (0.4, 0.0), (0.3, 0.0)]) == 0.0


def test_matrix_boundaries():
    cfg = OC.DEFAULT_CONFIG
    base = {"score_status": O.SCORED, "evolution_index": 100.0, "market_value_growth_pct": 5.0}
    assert O.matrix_quadrant(base, 5.0, cfg) == "Outperforming in faster-growing market"      # both inclusive
    assert O.matrix_quadrant(dict(base, evolution_index=99.999), 5.0, cfg) == "Underperforming in faster-growing market"
    assert O.matrix_quadrant(dict(base, market_value_growth_pct=4.999), 5.0, cfg) == "Outperforming in slower-growing market"
    assert O.matrix_quadrant(dict(base, evolution_index=50, market_value_growth_pct=-1), 5.0, cfg) == \
        "Underperforming in slower-growing market"
    assert O.matrix_quadrant(dict(base, score_status=O.INSUFFICIENT), 5.0, cfg) is None
    assert O.matrix_quadrant(base, None, cfg) is None


def test_synthetic_matrix_and_explanations(syn):
    p = _score(syn)
    assert p["P1 : S1 : C1 : 10"]["matrix_quadrant"] == "Outperforming in faster-growing market"
    assert p["P2 : S1 : C2 : 20"]["matrix_quadrant"] == "Underperforming in faster-growing market"
    assert p["P3 : S2 : C1 : 30"]["matrix_quadrant"] == "Outperforming in slower-growing market"   # EI == 100
    assert p["P5 : S3 : C1 : 50"]["matrix_quadrant"] is None
    e1 = p["P1 : S1 : C1 : 10"]
    assert [d.split(":")[0] for d in e1["positive_drivers"]] == ["Market growth", "Relative momentum (evolution index)"]
    assert e1["constraints"] == O.DATA_LIMITATIONS
    assert p["P5 : S3 : C1 : 50"]["constraints"][0] == O.REASONS["prior_zero"]


def test_config_validation():
    with pytest.raises(ValueError):
        OC.OpportunityConfig(product_components=(OC.Component("a", "evolution_index", 0.5, 1, "A"),))
    with pytest.raises(ValueError):
        OC.OpportunityConfig(market_components=(OC.Component("a", "value_growth_pct", 1.0, 0, "A"),))


def test_methodology_pinned():
    c = OC.DEFAULT_CONFIG
    assert (c.version, c.fingerprint()) == (PINNED_VERSION, PINNED_FINGERPRINT), \
        "methodology changed: bump version, document it, and update the pinned fingerprint"
    assert {x.name: x.weight for x in c.product_components} == PINNED_PRODUCT_WEIGHTS
    assert {x.name: x.weight for x in c.market_components} == PINNED_MARKET_WEIGHTS


def test_sensitivity_zero_delta_is_identity(syn):
    s = O.sensitivity(syn, "product", A, "MAT", _cfg(), delta=0.0, top_k=2)
    assert all(math.isclose(v["spearman_vs_baseline"], 1.0) for v in s["variants"][:-1])


# ======================================================================= fault injection
def _worse_weights():
    c = OC.DEFAULT_CONFIG
    comps = tuple(dataclasses.replace(x, weight={"market_growth": 0.35, "relative_momentum": 0.35}.get(x.name, x.weight))
                  for x in c.product_components)
    return dataclasses.replace(c, product_components=comps)


def _inverted_growth():
    c = OC.DEFAULT_CONFIG
    return dataclasses.replace(c, product_components=tuple(
        dataclasses.replace(x, direction=-1) if x.name == "market_growth" else x for x in c.product_components))


def _minmax(values, decimals=9):
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    return {k: 0.5 if hi == lo else (v - lo) / (hi - lo) for k, v in values.items()}


FAULTS = {
    "changed weight": ("config", _worse_weights),
    "inverted growth contribution": ("config", _inverted_growth),
    "version mismatch": ("config", lambda: dataclasses.replace(OC.DEFAULT_CONFIG, version="OPP-0.9.0")),
    "incorrect normalization (min-max)": ("percentile_normalize", lambda: _minmax),
    "incorrect aggregation (unweighted mean)": ("aggregate_score",
                                                lambda: (lambda parts: 100.0 * sum(n for _, n in parts) / len(parts))),
    "ranking direction reversed": ("score_rank_key", lambda: (lambda r: (O._r(r["score"]), r["entity_key"].encode()))),
    "missing data treated as zero": ("product_eligibility",
                                     lambda: (lambda orig: (lambda r, cfg: (O.SCORED, None)
                                                            if (r["value_prior"] or 0) <= 0 and r["value_cur"] > 0
                                                            else orig(r, cfg)))(O.product_eligibility)),
    "market filter removed": ("select_rows", lambda: (lambda rows, *a, **k: rows)),
}


@pytest.mark.parametrize("fault", list(FAULTS))
def test_fault_injection_detected(syn, monkeypatch, fault):
    target, make = FAULTS[fault]
    assert guard_failures(syn) == []                                    # baseline passes
    if target == "config":
        monkeypatch.setattr(OC, "DEFAULT_CONFIG", make())
    elif target == "product_eligibility":
        # undefined growth/EI silently replaced by 0 and the entity scored
        monkeypatch.setattr(O, "product_eligibility", make())
        monkeypatch.setattr(O, "directed", lambda v, c: (v or 0.0) * c.direction)
    else:
        monkeypatch.setattr(O, target, make())
    assert guard_failures(syn) != [], f"fault not detected: {fault}"    # fault detected
    monkeypatch.undo()
    assert guard_failures(syn) == []                                    # restored


# ======================================================================= real data
@pytest.fixture(scope="module")
def real(py_engine):
    return {"product": O.score_all(py_engine, "product", A, "MAT")["rows"],
            "market": O.score_all(py_engine, "market", A, "MAT")["rows"]}


@pytest.mark.parametrize("level,n", [("product", 65_398), ("market", 1_889)])
def test_real_bounds_components_statuses(real, level, n):
    rows = real[level]
    assert len(rows) == n
    scored = [r for r in rows if r["score_status"] == O.SCORED]
    assert scored and all(r["score_status"] in (O.SCORED, O.INSUFFICIENT) for r in rows)
    for r in scored:
        assert 0.0 <= r["score"] <= 100.0
        assert math.isclose(sum(c["weighted_contribution"] for c in r["components"]), r["score"], abs_tol=1e-9)
        assert all(0.0 <= c["normalized"] <= 1.0 and c["raw"] is not None for c in r["components"])
        assert r["methodology_version"] == OC.DEFAULT_CONFIG.version
    for r in rows:
        if r["score_status"] == O.INSUFFICIENT:
            assert r["score"] is None and r["opportunity_rank"] is None and r["insufficient_reason"] in O.REASONS
            assert all(c["normalized"] is None and c["weighted_contribution"] is None for c in r["components"])
    ranks = sorted(r["opportunity_rank"] for r in scored)
    assert ranks == list(range(1, len(scored) + 1))
    by_rank = sorted(scored, key=lambda r: r["opportunity_rank"])
    for a, b in zip(by_rank, by_rank[1:]):
        ra, rb = O._r(a["score"]), O._r(b["score"])
        assert ra > rb or (ra == rb and a["entity_key"].encode() < b["entity_key"].encode())
    n = len(scored)
    for comp in scored[0]["components"]:
        pairs = [(O._r(c["raw"] * c.get("direction", 1)), c["normalized"]) for r in scored
                 for c in r["components"] if c["name"] == comp["name"]]
        lo, hi = min(p[0] for p in pairs), max(p[0] for p in pairs)
        n_lo, n_hi = sum(p[0] == lo for p in pairs), sum(p[0] == hi for p in pairs)
        # ties at the extremes share the average rank: (tie_count - 1) / 2 / (n - 1) from each end
        assert {p[1] for p in pairs if p[0] == lo} == {(n_lo - 1) / 2 / (n - 1)}
        assert {p[1] for p in pairs if p[0] == hi} == {1 - (n_hi - 1) / 2 / (n - 1)}
        assert all(0.0 <= p[1] <= 1.0 for p in pairs)


def test_real_missing_evidence_never_scored(real):
    rows = real["product"]
    assert all(r["score_status"] == O.INSUFFICIENT for r in rows if r["value_prior"] is not None and r["value_prior"] <= 0)
    assert all(r["score_status"] == O.INSUFFICIENT for r in rows if r["value_share_in_market_pct"] is None)
    assert any(r["insufficient_reason"] == "prior_zero" for r in rows)
    assert all(r["score_status"] == O.SCORED for r in rows if r["value_cur"] == 0 and r["insufficient_reason"] is None)


def test_real_ties_share_normalized_value(real):
    scored = [r for r in real["product"] if r["score_status"] == O.SCORED]
    zero_share = [r for r in scored if r["value_share_in_market_pct"] == 0]
    assert len(zero_share) > 1   # discontinued products: genuine low performance, scored, tied
    vals = {next(c["normalized"] for c in r["components"] if c["name"] == "market_position") for r in zero_share}
    assert len(vals) == 1


def test_real_outlier_does_not_dominate(real):
    scored = [r for r in real["product"] if r["score_status"] == O.SCORED]
    top_ei = max(scored, key=lambda r: r["evolution_index"])
    comp = next(c for c in top_ei["components"] if c["name"] == "relative_momentum")
    assert comp["raw"] == top_ei["evolution_index"] and comp["normalized"] == 1.0
    assert comp["weighted_contribution"] == pytest.approx(40.0)    # capped by its weight, never more


def test_real_deterministic_repeat(py_engine, real):
    py_engine.__dict__.pop("_opportunity_cache", None)
    again = O.score_all(py_engine, "product", A, "MAT")["rows"]
    assert [(r["entity_key"], r["score"], r["opportunity_rank"], r["score_status"]) for r in again] == \
           [(r["entity_key"], r["score"], r["opportunity_rank"], r["score_status"]) for r in real["product"]]


def test_insufficient_history(py_engine):
    rows = O.score_all(py_engine, "product", "2023-04-01", "MAT")["rows"]
    assert rows and all(r["score_status"] == O.INSUFFICIENT and r["insufficient_reason"] == "insufficient_history"
                        for r in rows)


def test_inputs_match_m4_sql(con, real):
    """Component inputs are the validated M4 metrics (share in market, EI, market growth)."""
    rows = real["product"]
    for sg in sorted({r["subgroup"] for r in rows if r["score_status"] == O.SCORED})[:5]:
        sql = {r["entity_key"]: r for r in _sql_rows(
            con, "SELECT entity_key, value_share_pct, evolution_index FROM product_performance(?, 'MAT', 'subgroup', ?)",
            [dt.date(2024, 5, 1), sg])}
        for r in rows:
            if r["subgroup"] == sg:
                s = sql[r["prod_code"]]
                for a, b in ((r["value_share_in_market_pct"], s["value_share_pct"]), (r["evolution_index"], s["evolution_index"])):
                    assert (a is None and b is None) or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    msql = {r["entity_key"]: r for r in _sql_rows(con, "SELECT entity_key, value_growth_pct, value_share_pct "
                                                       "FROM market_performance('subgroup', DATE '2024-05-01', 'MAT')", [])}
    for r in real["market"]:
        for a, b in ((r["value_growth_pct"], msql[r["entity_key"]]["value_growth_pct"]),
                     (r["value_share_of_total_pct"], msql[r["entity_key"]]["value_share_pct"])):
            assert (a is None and b is None) or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


# ======================================================================= API
@pytest.fixture(scope="module")
def api(py_engine):
    return PyCommercialAnalytics(py_engine)


def test_api_scores_envelope(api):
    env = api.get_opportunity_scores(top_n=10)
    assert env["row_count"] == 10 and env["methodology"]["version"] == PINNED_VERSION
    assert env["methodology"]["fingerprint"] == PINNED_FINGERPRINT
    assert [r["rank_in_selection"] for r in env["rows"]] == list(range(1, 11))
    assert [r["opportunity_rank"] for r in env["rows"]] == list(range(1, 11))
    assert env["status_counts"]["SCORED"] + env["status_counts"]["INSUFFICIENT_EVIDENCE"] == 65_398
    import json
    json.dumps(env)


def test_api_filters_do_not_change_scores(api):
    full = {r["entity_key"]: r["score"] for r in api.get_opportunity_scores(top_n=None)["rows"]}
    sub = api.get_opportunity_scores(market_level="supergroup", market_key="CARDIAC", top_n=None)
    assert sub["rows"] and all(r["supergroup"] == "CARDIAC" for r in sub["rows"])
    assert all(full[r["entity_key"]] == r["score"] for r in sub["rows"])
    assert [r["rank_in_selection"] for r in sub["rows"]] == list(range(1, len(sub["rows"]) + 1))
    co = sub["rows"][0]["company"]
    byco = api.get_opportunity_scores(company=co, top_n=None)
    assert byco["rows"] and all(r["company"] == co for r in byco["rows"])
    mk = api.get_opportunity_scores(level="market", market_level="supergroup", market_key="CARDIAC", top_n=None)
    assert mk["rows"] and all(r["supergroup"] == "CARDIAC" for r in mk["rows"])


def test_api_include_insufficient_and_detail(api):
    env = api.get_opportunity_scores(include_insufficient=True, top_n=None)
    assert env["total_rows"] == 65_398
    tail = [r for r in env["rows"] if r["score_status"] == O.INSUFFICIENT]
    assert tail and all(r["rank_in_selection"] is None for r in tail)
    assert env["rows"].index(tail[0]) == env["status_counts"]["SCORED"]     # insufficient listed after all scored
    d = api.get_opportunity_detail("product", tail[0]["entity_key"])
    assert d["rows"][0]["score"] is None and d["rows"][0]["insufficient_reason"] in O.REASONS
    top = env["rows"][0]
    d = api.get_opportunity_detail("product", top["entity_key"])
    assert d["rows"][0]["score"] == top["score"] and len(d["rows"][0]["components"]) == 3


@pytest.mark.parametrize("call,code", [
    (lambda a: a.get_opportunity_scores(basis="MONTH"), "invalid_parameter"),
    (lambda a: a.get_opportunity_scores(level="company"), "invalid_parameter"),
    (lambda a: a.get_opportunity_scores(anchor="2025-01-01"), "period_unavailable"),
    (lambda a: a.get_opportunity_scores(anchor="2022-01-01"), "period_unavailable"),
    (lambda a: a.get_opportunity_scores(market_level="geography", market_key="X"), "unsupported"),
    (lambda a: a.get_opportunity_scores(market_level="subgroup", market_key="NO SUCH"), "not_found"),
    (lambda a: a.get_opportunity_scores(level="market", market_level="subgroup", market_key="X"), "invalid_parameter"),
    (lambda a: a.get_opportunity_scores(level="market", company="X"), "invalid_parameter"),
    (lambda a: a.get_opportunity_scores(top_n=0), "invalid_parameter"),
    (lambda a: a.get_opportunity_detail("product", "NO SUCH"), "not_found"),
])
def test_api_errors(api, call, code):
    with pytest.raises(AnalyticsError) as e:
        call(api)
    assert e.value.code == code


def test_sql_api_delegates_to_same_implementation(con, api):
    from pci_analytics import CommercialAnalytics
    s = CommercialAnalytics(con)
    s._opp_engine = api.engine
    assert s.get_opportunity_scores(top_n=20)["rows"] == api.get_opportunity_scores(top_n=20)["rows"]
