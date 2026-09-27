"""Shared agent-layer schemas: statuses, metric units, deterministic number formatting, findings.

Agents never compute numbers. A finding only *references* a value inside a recorded tool result
(call sequence number + JSON path); `make_value` copies it and formats it deterministically, and the
QA engine later re-reads the same path and re-formats to prove provenance.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- statuses
OK = "OK"
AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
UNRECOGNIZED_REQUEST = "UNRECOGNIZED_REQUEST"
UNSAFE_REQUEST = "UNSAFE_REQUEST"
TOOL_NOT_PERMITTED = "TOOL_NOT_PERMITTED"
QA_FAILED = "QA_FAILED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
# tool-layer error codes (pci_app.tools.ERROR_CATEGORIES) are passed through unchanged as statuses

SCENARIO_BANNER = "SCENARIO / WHAT-IF ANALYSIS — NOT A FORECAST"
OPPORTUNITY_LABEL = "DESCRIPTIVE OPPORTUNITY SCORING"
MARKET_DEFINITION_NOTE = "Market = source therapy SUBGROUP hierarchy (analytical definition); national data only."
DEMO_MODE_LABEL = "DETERMINISTIC DEMO MODE"

# ---------------------------------------------------------------- units per metric (the only allowed units)
UNIT_OF = {
    "value_cur": "INR crore", "value_prior": "INR crore", "value_abs_chg": "INR crore", "value_cr": "INR crore",
    "value_mat": "INR crore", "value_ytd": "INR crore", "market_value_cr": "INR crore",
    "value_growth_pct": "%", "units_growth_pct": "%", "value_mat_growth_pct": "%", "value_share_pct": "%",
    "value_share_in_market_pct": "%", "market_value_growth_pct": "%", "value_pct": "%", "share_pct": "%",
    "assumption_pct": "%", "national_value_growth_pct": "%",
    "units_cur": "'000 packs", "units_k": "'000 packs",
    "evolution_index": "index (100 = in line with market)",
    "contribution_to_growth_pp": "pp", "value_share_chg_pp": "pp", "share_pp": "pp",
    "score": "descriptive score 0-100", "weighted_contribution": "score points", "normalized_pct": "percentile",
    "weight": "weight",
    "rank_value": "rank", "rank_growth": "rank", "opportunity_rank": "rank", "count": "count",
    "price_rs_per_pack": "INR per pack",
}
_SIGNED = {"value_growth_pct", "units_growth_pct", "value_mat_growth_pct", "market_value_growth_pct", "value_pct",
           "assumption_pct", "national_value_growth_pct", "contribution_to_growth_pp", "value_share_chg_pp",
           "share_pp", "value_abs_chg"}


def fmt(value, metric: str) -> str:
    """Deterministic display of a tool value (never computes anything but formatting)."""
    if value is None:
        return "n/a"
    unit = UNIT_OF[metric]
    if unit in ("rank", "count"):
        return str(int(value))
    if unit == "INR crore":
        return f"{value:+,.2f}" if metric in _SIGNED else f"{value:,.2f}"
    if unit in ("%", "pp"):
        return f"{value:+.2f}" if metric in _SIGNED else f"{value:.2f}"
    if unit == "'000 packs":
        return f"{value:,.0f}"
    if unit == "weight":
        return f"{value:.2f}"
    if unit == "INR per pack":
        return f"{value:,.2f}"
    return f"{value:.1f}"   # index, score, score points, percentile


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def number_tokens(text: str) -> set[str]:
    """Unsigned numeric tokens as they appear in text (used by QA provenance checks)."""
    return set(_NUM.findall(str(text)))


def get_path(obj, path):
    for p in path:
        obj = obj[p]
    return obj


def make_value(ctx, call: int, path: list, metric: str, name: str | None = None) -> dict:
    """Reference one numeric field of a recorded tool result (no arithmetic)."""
    v = get_path(ctx.results[call], path)
    return {"name": name or metric, "metric": metric, "value": v, "display": fmt(v, metric),
            "unit": UNIT_OF[metric], "source": {"call": call, "path": list(path)}}


def make_ref(ctx, call: int, path: list, label: str) -> dict:
    """Reference a non-numeric context field (entity key/label, period) for consistency checks."""
    return {"label": label, "value": get_path(ctx.results[call], path), "source": {"call": call, "path": list(path)}}


def make_finding(agent: str, template: str, values: list, refs: list, kind: str = "metric") -> dict:
    """Statement = template with {name} placeholders filled by value displays and ref values."""
    def show(v):
        if v["display"] == "n/a":
            return "n/a (not available)"
        if v["unit"] in ("rank", "count", "weight"):
            return v["display"]
        if v["unit"] == "%":
            return v["display"] + "%"
        if v["unit"] == "descriptive score 0-100":
            return v["display"] + "/100"
        return f"{v['display']} {v['unit']}"

    mapping = {v["name"]: show(v) for v in values}
    mapping.update({r["label"]: str(r["value"]) for r in refs})
    return {"agent": agent, "kind": kind, "statement": template.format(**mapping), "values": values, "refs": refs}
