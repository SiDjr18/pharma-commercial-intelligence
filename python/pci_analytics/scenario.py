"""Deterministic what-if scenario engine (M7) — canonical implementation (Python only, no SQL copy).

A scenario applies EXPLICIT user assumptions to an OBSERVED baseline taken from the validated
M5 Python engine (metrics.entity_period). It is arithmetic, not a forecast: no probabilities,
no confidence intervals, no predicted outcomes, no elasticities. Methodology: docs/SCENARIO_ENGINE.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

from . import metrics, periods
from .engine import ENTITY_TYPES

METHODOLOGY_VERSION = "SCN-1.0.0"

# ---- unit conversion (verified against source PR_* fields: exact to 4.4e-16 relative, M7) ----
RUPEES_PER_CRORE = 10_000_000          # value_cr is Rs crore (CONFIRMED)
UNITS_PER_THOUSAND = 1_000             # units_k / qty_k are '000 (INFERRED; consistent with source PR)
PRICE_FACTOR = RUPEES_PER_CRORE / UNITS_PER_THOUSAND   # = 10,000: Rs/pack = 1e4 * value_cr / units_k

UNIT_LABELS = {
    "value_cr": "INR crore (1 crore = 10,000,000 INR; confirmed)",
    "units_k": "'000 packs (inferred)",
    "qty_k": "'000 counting units (inferred)",
    "price_rs_per_pack": "INR per pack = value_cr x 10^7 / (units_k x 10^3) (pack scale inferred)",
    "price_rs_per_counting_unit": "INR per counting unit = value_cr x 10^7 / (qty_k x 10^3) (scale inferred)",
    "pct": "percent",
    "share_pct": "percent of market value (same window)",
}
OBSERVED, ASSUMED, CALCULATED = "OBSERVED", "ASSUMED", "CALCULATED"
DISCLAIMER = ("A scenario is deterministic what-if arithmetic on an observed baseline under explicit assumptions. "
              "It is not a forecast, prediction or probability.")

SCENARIO_TYPES = ("PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE", "MARKET_GROWTH", "MARKET_SHARE")
UNSUPPORTED_SCENARIOS = {
    "PRICE_ELASTICITY": "No elasticity can be estimated from this source (no price experiments/controls); "
                        "supply an explicit volume_change_pct with PRICE_VOLUME_CHANGE instead.",
    "FORECAST": "The engine performs what-if arithmetic only; no forecasting model exists.",
    "GEOGRAPHY": "The source has no geography field.",
    "CHANNEL": "The source has no labelled channel field (SSA/HSA/DSA unconfirmed).",
    "PROMOTION": "The source has no promotion/spend data.",
}
MARKET_LEVELS = ("total", "supergroup", "therapy_group", "subgroup", "molecule")
SHARE_ENTITIES = ("product", "company")
SCOPE_LEVELS = MARKET_LEVELS + ("company",)

# assumption name -> (low, low_inclusive, high, high_inclusive). Out-of-range is REJECTED, never corrected.
ASSUMPTION_BOUNDS = {
    "price_change_pct": (-100.0, False, 1000.0, True),     # price must stay > 0
    "volume_change_pct": (-100.0, True, 1000.0, True),     # units >= 0
    "market_growth_pct": (-100.0, True, 1000.0, True),     # value >= 0
    "target_share_pct": (0.0, True, 100.0, True),          # a share is 0..100 %
}
REQUIRED = {
    "PRICE_CHANGE": ({"price_change_pct"}, set()),
    "VOLUME_CHANGE": ({"volume_change_pct"}, set()),
    "PRICE_VOLUME_CHANGE": ({"price_change_pct", "volume_change_pct"}, set()),
    "MARKET_GROWTH": ({"market_growth_pct"}, set()),
    "MARKET_SHARE": ({"target_share_pct"}, {"market_growth_pct"}),
}


def _err(code, msg):
    from .api import AnalyticsError
    return AnalyticsError(code, msg)


# ---------------------------------------------------------------- pure arithmetic (hand-testable)
def price_rs(value_cr: float, units_k: float) -> float | None:
    """Rs per pack (or per counting unit when given qty_k). Undefined (None) when units are 0."""
    if units_k is None or units_k <= 0:
        return None
    return value_cr * RUPEES_PER_CRORE / (units_k * UNITS_PER_THOUSAND)


def value_from_price(price: float, units_k: float) -> float:
    """Rs crore from Rs/pack x '000 packs."""
    return price * units_k * UNITS_PER_THOUSAND / RUPEES_PER_CRORE


def apply_changes(price0: float, units0: float, qty0: float, price_pct: float, volume_pct: float):
    """Price change acts on price only; volume change acts on units and qty (pack mix held constant)."""
    f_p, f_v = 1 + price_pct / 100.0, 1 + volume_pct / 100.0
    return price0 * f_p, units0 * f_v, qty0 * f_v


def pct_change(base: float | None, new: float | None) -> float | None:
    """(new - base) / base * 100; undefined (None) when base is 0 or missing."""
    if base is None or new is None or base == 0:
        return None
    return (new - base) / base * 100.0


def validate_assumptions(scenario_type: str, assumptions: dict) -> dict:
    if not isinstance(assumptions, dict):
        raise _err("invalid_assumption", "assumptions must be a dict")
    req, opt = REQUIRED[scenario_type]
    unknown = set(assumptions) - req - opt
    if unknown:
        raise _err("invalid_assumption", f"unexpected assumption(s) for {scenario_type}: {sorted(unknown)}")
    missing = req - set(assumptions)
    if missing:
        raise _err("invalid_assumption", f"missing assumption(s): {sorted(missing)}")
    out = {}
    for k, v in assumptions.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise _err("invalid_assumption", f"{k} must be a finite number")
        lo, lo_inc, hi, hi_inc = ASSUMPTION_BOUNDS[k]
        if (v < lo or (v == lo and not lo_inc)) or (v > hi or (v == hi and not hi_inc)):
            raise _err("invalid_assumption", f"{k}={v} outside allowed range "
                                             f"{'[' if lo_inc else '('}{lo}, {hi}{']' if hi_inc else ')'}")
        out[k] = float(v)
    for k in opt - set(out):
        out[k] = 0.0
    return out


# ---------------------------------------------------------------- baseline (observed)
def baseline_window(engine, anchor, basis):
    b = str(basis).upper()
    if b not in periods.BASES:
        raise _err("invalid_parameter", f"basis must be one of {periods.BASES}")
    try:
        a = dt.date.fromisoformat(str(anchor)[:10]).replace(day=1)
    except ValueError as e:
        raise _err("invalid_parameter", f"anchor must be an ISO date: {anchor!r}") from e
    if not engine.first_period <= a <= engine.last_period:
        raise _err("period_unavailable", f"anchor {a} outside data range {engine.first_period}..{engine.last_period}")
    w = periods.make_window(a, b, engine.first_period)
    if not w.current_complete:
        raise _err("period_unavailable", f"{b} window {w.cur_start}..{w.cur_end} is incomplete (starts before data)")
    return w


def get_baseline(engine, entity_type, entity_key, anchor="2024-05-01", basis="MAT", market_level=None, market_key=None):
    """Observed baseline for one entity (optionally restricted to / measured within a market)."""
    from .api import UNSUPPORTED
    w = baseline_window(engine, anchor, basis)          # validate period parameters first
    et = str(entity_type).lower()
    if et in UNSUPPORTED:
        raise _err("unsupported", UNSUPPORTED[et])
    if et not in ENTITY_TYPES:
        raise _err("invalid_parameter", f"entity_type must be one of {tuple(ENTITY_TYPES)}")
    key = str(entity_key)
    if not engine.has_key(et, key):
        raise _err("not_found", f"no {et} with key {key!r}")
    if market_level is not None:
        ml = str(market_level).lower()
        if ml in UNSUPPORTED:
            raise _err("unsupported", UNSUPPORTED[ml])
        if ml not in SCOPE_LEVELS:
            raise _err("invalid_parameter", f"market_level must be one of {SCOPE_LEVELS}")
        mk = "TOTAL" if ml == "total" else str(market_key)
        if ml != "total" and not engine.has_key(ml, mk):
            raise _err("not_found", f"no {ml} with key {mk!r}")
        rows = metrics.entity_period(engine, et, w.anchor, w.basis, ml, mk)
        scope = {"market_level": ml, "market_key": mk}
    else:
        rows = metrics.entity_period(engine, et, w.anchor, w.basis, et, key)   # scope = the entity itself
        scope = None
    row = next((r for r in rows if r["entity_key"] == key), None)
    if row is None:
        raise _err("not_found", f"{et} {key!r} has no packs in {scope}")
    b = {"kind": OBSERVED, "value_cr": row["value_cur"], "units_k": row["units_cur"], "qty_k": row["qty_cur"],
         "price_rs_per_pack": price_rs(row["value_cur"], row["units_cur"]),
         "price_rs_per_counting_unit": price_rs(row["value_cur"], row["qty_cur"]), "n_packs": row["n_packs"]}
    if scope:
        b.update(market_value_cr=row["scope_value_cur"], share_pct=row["value_share_pct"])
    entity = {"entity_type": et, "entity_key": key, "entity_label": row["entity_label"], "scope": scope}
    period = {"anchor": w.anchor.isoformat(), "basis": w.basis, "basis_label": w.basis_label,
              "window_start": w.cur_start.isoformat(), "window_end": w.cur_end.isoformat(), "n_months": w.n_months}
    return entity, period, b


# ---------------------------------------------------------------- scenario calculations
def _price_volume(base, a, price_pct, volume_pct):
    p0, u0, q0, v0 = base["price_rs_per_pack"], base["units_k"], base["qty_k"], base["value_cr"]
    if p0 is None:
        raise _err("insufficient_baseline", "baseline units are 0 in this window, so price is undefined")
    p1, u1, q1 = apply_changes(p0, u0, q0, price_pct, volume_pct)
    v1 = value_from_price(p1, u1)
    fp, fv = price_pct / 100.0, volume_pct / 100.0
    res = {"kind": CALCULATED, "value_cr": v1, "units_k": u1, "qty_k": q1, "price_rs_per_pack": p1,
           "price_rs_per_counting_unit": price_rs(v1, q1)}
    abs_ch = {"kind": CALCULATED, "value_cr": v1 - v0, "units_k": u1 - u0, "qty_k": q1 - q0,
              "price_rs_per_pack": p1 - p0,
              "value_decomposition_cr": {"price_effect": v0 * fp, "volume_effect": v0 * fv,
                                         "price_x_volume_interaction": v0 * fp * fv}}
    pct = {"kind": CALCULATED, "value_pct": pct_change(v0, v1), "units_pct": pct_change(u0, u1),
           "price_pct": pct_change(p0, p1)}
    return res, abs_ch, pct


def _calculate(scenario_type, base, a):
    held = []
    if scenario_type == "PRICE_CHANGE":
        held = ["units_k (no elasticity: volume held constant unless supplied)", "qty_k"]
        return (*_price_volume(base, a, a["price_change_pct"], 0.0), held,
                ["price_1 = price_0 x (1 + price_change_pct/100)", "units_1 = units_0",
                 "value_1 [Rs crore] = price_1 [Rs/pack] x units_1 ['000 packs] x 10^3 / 10^7"])
    if scenario_type == "VOLUME_CHANGE":
        held = ["price_rs_per_pack", "pack mix (qty per pack)"]
        return (*_price_volume(base, a, 0.0, a["volume_change_pct"]), held,
                ["units_1 = units_0 x (1 + volume_change_pct/100)", "qty_1 = qty_0 x (1 + volume_change_pct/100)",
                 "price_1 = price_0", "value_1 = price_1 x units_1 x 10^3 / 10^7"])
    if scenario_type == "PRICE_VOLUME_CHANGE":
        held = ["pack mix (qty per pack)", "no link between price and volume other than the two assumptions"]
        return (*_price_volume(base, a, a["price_change_pct"], a["volume_change_pct"]), held,
                ["price_1 = price_0 x (1 + p)", "units_1 = units_0 x (1 + v)",
                 "value_1 = price_1 x units_1 x 10^3 / 10^7 = value_0 x (1 + p)(1 + v)",
                 "decomposition: price effect = value_0 x p; volume effect = value_0 x v; interaction = value_0 x p x v"])
    if scenario_type == "MARKET_GROWTH":
        v0 = base["value_cr"]
        if v0 <= 0:
            raise _err("insufficient_baseline", "baseline market value is 0; growth cannot be applied meaningfully")
        v1 = v0 * (1 + a["market_growth_pct"] / 100.0)
        return ({"kind": CALCULATED, "value_cr": v1}, {"kind": CALCULATED, "value_cr": v1 - v0},
                {"kind": CALCULATED, "value_pct": pct_change(v0, v1)},
                ["units/qty/price not modelled (assumption is on value only)"],
                ["value_1 = value_0 x (1 + market_growth_pct/100)"])
    if scenario_type == "MARKET_SHARE":
        m0, p0, s0 = base["market_value_cr"], base["value_cr"], base["share_pct"]
        if not m0 or m0 <= 0:
            raise _err("insufficient_baseline", "baseline market value is 0; share is undefined")
        m1 = m0 * (1 + a["market_growth_pct"] / 100.0)
        p1 = a["target_share_pct"] / 100.0 * m1
        return ({"kind": CALCULATED, "value_cr": p1, "market_value_cr": m1, "share_pct": a["target_share_pct"]},
                {"kind": CALCULATED, "value_cr": p1 - p0, "market_value_cr": m1 - m0,
                 "share_pp": a["target_share_pct"] - s0},
                {"kind": CALCULATED, "value_pct": pct_change(p0, p1), "market_value_pct": pct_change(m0, m1)},
                ["share gained/lost is taken from/given to the rest of the market (competitor total = market - entity)",
                 "market value changes only by market_growth_pct (default 0)", "units/qty/price not modelled"],
                ["market_1 = market_0 x (1 + market_growth_pct/100)", "value_1 = target_share_pct/100 x market_1",
                 "denominator = value of the stated market (market_level/market_key) in the same window"])
    raise _err("unsupported", f"scenario_type {scenario_type}")


def scenario_id(scenario_type, entity, period, assumptions) -> str:
    payload = json.dumps([METHODOLOGY_VERSION, scenario_type, entity, period, assumptions], sort_keys=True)
    return "SCN-" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def run_scenario(engine, scenario_type, entity_type, entity_key, assumptions, anchor="2024-05-01", basis="MAT",
                 market_level=None, market_key=None) -> dict:
    st = str(scenario_type).upper()
    if st in UNSUPPORTED_SCENARIOS:
        raise _err("unsupported", UNSUPPORTED_SCENARIOS[st])
    if st not in SCENARIO_TYPES:
        raise _err("invalid_parameter", f"scenario_type must be one of {SCENARIO_TYPES}")
    a = validate_assumptions(st, assumptions)
    et = str(entity_type).lower()
    if st == "MARKET_GROWTH" and et not in MARKET_LEVELS:
        raise _err("invalid_parameter", f"MARKET_GROWTH applies to market entities {MARKET_LEVELS}")
    if st == "MARKET_SHARE":
        if et not in SHARE_ENTITIES:
            raise _err("invalid_parameter", f"MARKET_SHARE applies to {SHARE_ENTITIES} within a market")
        if market_level is None or str(market_level).lower() not in MARKET_LEVELS:
            raise _err("invalid_parameter", f"MARKET_SHARE needs market_level in {MARKET_LEVELS} (the share denominator)")
    entity, period, base = get_baseline(engine, et, entity_key, anchor, basis, market_level, market_key)
    res, abs_ch, pct, held, formulas = _calculate(st, base, a)
    assumptions_block = {"kind": ASSUMED, **a, "implicit_premises": held}
    limitations = [DISCLAIMER,
                   "Baseline is observed national secondary-sales data for the stated window; no seasonality, "
                   "competitor reaction or elasticity is modelled.",
                   "Price is the value-weighted average price of the entity's packs (value/units); for a single "
                   "pack it equals the source PR field.",
                   "Units/qty '000 scale is inferred (consistent with the source PR field).",
                   "Market = source therapy SUBGROUP hierarchy (analytical definition)."]
    if pct.get("value_pct") is None:
        limitations.append("Percentage change undefined because the baseline value is 0.")
    return {
        "status": "OK", "function": "run_scenario",
        "scenario_id": scenario_id(st, entity, period, a), "scenario_type": st,
        "methodology_version": METHODOLOGY_VERSION,
        "entity": entity, "period": period, "baseline": base, "assumptions": assumptions_block,
        "scenario_result": res, "absolute_change": abs_ch, "percentage_change": pct,
        "units": UNIT_LABELS, "calculation_basis": formulas,
        "assumptions_and_limitations": limitations,
        "evidence": {"engine": "python (canonical scenario engine)",
                     "baseline_source": "metrics.entity_period (M5-validated; equals M4 SQL)",
                     "definitions": ["docs/SCENARIO_ENGINE.md"]},
    }


def baseline_envelope(engine, entity_type, entity_key, anchor="2024-05-01", basis="MAT", market_level=None,
                      market_key=None) -> dict:
    entity, period, base = get_baseline(engine, entity_type, entity_key, anchor, basis, market_level, market_key)
    return {"status": "OK", "function": "get_scenario_baseline", "methodology_version": METHODOLOGY_VERSION,
            "entity": entity, "period": period, "baseline": base, "units": UNIT_LABELS,
            "assumptions_and_limitations": [DISCLAIMER],
            "evidence": {"engine": "python", "baseline_source": "metrics.entity_period (M5-validated)"}}
