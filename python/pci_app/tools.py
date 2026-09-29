"""Tool / application API boundary (M8).

A thin, whitelisted layer over the validated analytics API (pci_analytics.CommercialAnalytics):
- a registry of named tools with machine-readable contracts (JSON-Schema subset),
- input validation BEFORE any engine call (types, enums, ranges, required, no extra keys),
- structured, user-safe errors (no tracebacks, no paths),
- response sanitation (filesystem paths are never returned).
No metric is computed here; every number comes from the M4-M7 engines unchanged.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import logging
import math
import re

from pci_analytics.api import (BASES, COMPANY_SCOPES, MARKET_LEVELS, PRODUCT_SCOPES, SEGMENTS, THERAPY_LEVELS,
                               AnalyticsError)
from pci_analytics.engine import ENTITY_TYPES
from pci_analytics.opportunity import LEVELS as OPP_LEVELS, MARKET_FILTER_LEVELS, PRODUCT_FILTER_LEVELS
from pci_analytics.opportunity_config import DEFAULT_CONFIG as OPP_CONFIG
from pci_analytics.scenario import METHODOLOGY_VERSION as SCN_VERSION, SCENARIO_TYPES

log = logging.getLogger("pci_app")
TOOL_API_VERSION = "TOOLS-1.0.0"

# ---------------------------------------------------------------- schema fragments
DATE = {"type": "string", "format": "date", "description": "ISO date; any day in the month (normalised to the 1st). "
                                                           "Data range 2021-06..2024-05."}
BASIS = {"type": "string", "enum": list(BASES), "description": "MONTH | YTD (calendar Jan..anchor) | MAT (12 months)"}
# Row caps (P1): no endpoint returns an unbounded list. null/absent top_n means "up to the safe maximum", never "all".
MAX_ROWS = 2000              # dimension lists (markets, therapy levels, companies, market-level opportunity)
MAX_PRODUCT_ROWS = 500       # product-grain lists (products, product-level opportunity)
TOP_N = {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "nullable": True,
         "description": f"rows to return; null or absent = the safe maximum ({MAX_ROWS}; product lists "
                        f"{MAX_PRODUCT_ROWS}). total_rows always reports the full count."}
KEY = {"type": "string", "minLength": 1, "maxLength": 200}
PERIOD_NOTE = ("Growth compares with the same window 12 months earlier; MONTH from 2021-06 (growth 2022-06), "
               "YTD from 2022-01 (growth 2023-01), MAT from 2022-05 (growth 2023-05).")
UNITS = "value_cr = INR crore (confirmed); units_k = '000 packs (inferred); qty_k = '000 counting units (inferred)"
ANALYTICS_ENVELOPE = ["function", "filters", "period", "units", "row_count", "total_rows", "rows", "evidence", "caveats"]
PERF_FIELDS = ["entity_type", "entity_key", "entity_label", "value_cur", "value_prior", "value_abs_chg",
               "value_growth_pct", "value_growth_status", "units_cur", "units_prior", "units_growth_pct",
               "qty_cur", "qty_prior", "qty_growth_pct", "value_share_pct", "value_share_prior_pct",
               "value_share_chg_pp", "units_share_pct", "contribution_to_growth_pp", "evolution_index",
               "rank_value", "rank_growth", "n_entities"]
TREND_FIELDS = ["period", "value_cr", "value_cr_prior", "value_growth_pct", "units_k", "value_ytd",
                "value_ytd_growth_pct", "value_mat", "value_mat_growth_pct", "units_mat", "units_mat_growth_pct"]
COMMON_LIMITS = ["National data only (no geography); SSA/HSA/DSA and channel unsupported.",
                 "Market = source therapy SUBGROUP hierarchy (analytical definition).",
                 "Units/qty '000 scale inferred."]


def _schema(props: dict, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def _perf_output(extra=()):
    return {"envelope": ANALYTICS_ENVELOPE, "row_fields": PERF_FIELDS + list(extra), "units": UNITS,
            "period_semantics": PERIOD_NOTE}


TOOLS = {
    "get_application_metadata": {
        "purpose": "Valid values for every selector (periods, bases, levels, segments, scenario types) and "
                   "methodology versions. No analytics.",
        "input_schema": _schema({}), "output": {"fields": ["periods", "bases", "market_levels", "..."]},
        "limitations": [], "method": None},
    "find_products": {
        "purpose": "Resolve a brand-name fragment to analytical product keys (prod_code). Brand names are not unique.",
        "input_schema": _schema({"name_contains": {**KEY, "minLength": 2},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["name_contains"]),
        "output": {"envelope": ANALYTICS_ENVELOPE,
                   "row_fields": ["prod_code", "brand", "company", "manufacturer_code", "prod_launch_month"]},
        "limitations": ["Literal, case-insensitive substring match on brand name."], "method": "find_products"},
    "get_market_performance": {
        "purpose": "All markets at one level with value/units, growth, share, contribution, evolution index, ranks.",
        "input_schema": _schema({"level": {"type": "string", "enum": list(MARKET_LEVELS)}, "anchor": DATE,
                                 "basis": BASIS, "top_n": TOP_N}),
        "output": _perf_output(), "limitations": COMMON_LIMITS, "method": "get_market_performance"},
    "get_market_trends": {
        "purpose": "Dense 36-month series for one market with month-YoY, calendar-YTD and MAT (+ growth).",
        "input_schema": _schema({"level": {"type": "string", "enum": list(MARKET_LEVELS)}, "key": KEY}),
        "output": {"envelope": ANALYTICS_ENVELOPE, "row_fields": TREND_FIELDS, "units": UNITS,
                   "period_semantics": PERIOD_NOTE}, "limitations": COMMON_LIMITS, "method": "get_market_trends"},
    "get_brand_performance": {
        "purpose": "Products (key prod_code; brand = label) ranked within a market/company/segment scope.",
        "input_schema": _schema({"anchor": DATE, "basis": BASIS,
                                 "market_level": {"type": "string", "enum": list(PRODUCT_SCOPES)},
                                 "market_key": KEY, "company": KEY, "top_n": TOP_N}),
        "output": _perf_output(["brand", "company", "manufacturer_code", "prod_launch_month"]),
        "limitations": COMMON_LIMITS + ["A product can appear in several subgroup markets; shares are per scope."],
        "method": "get_brand_performance"},
    "get_brand_growth": {
        "purpose": "36-month trend for one product (optionally only its packs in one market).",
        "input_schema": _schema({"prod_code": KEY, "market_level": {"type": "string", "enum": list(PRODUCT_SCOPES)},
                                 "market_key": KEY}, ["prod_code"]),
        "output": {"envelope": ANALYTICS_ENVELOPE, "row_fields": TREND_FIELDS, "units": UNITS},
        "limitations": COMMON_LIMITS, "method": "get_brand_growth"},
    "get_brand_share": {
        "purpose": "One product's share, share change, evolution index and rank within a market.",
        "input_schema": _schema({"prod_code": KEY, "market_level": {"type": "string", "enum": list(PRODUCT_SCOPES)},
                                 "market_key": KEY, "anchor": DATE, "basis": BASIS},
                                ["prod_code", "market_level", "market_key"]),
        "output": _perf_output(["brand", "company"]), "limitations": COMMON_LIMITS, "method": "get_brand_share"},
    "get_company_performance": {
        "purpose": "Companies (source COMPANY) ranked within a scope.",
        "input_schema": _schema({"anchor": DATE, "basis": BASIS,
                                 "market_level": {"type": "string", "enum": list(COMPANY_SCOPES)},
                                 "market_key": KEY, "top_n": TOP_N}),
        "output": _perf_output(["indian_mnc"]), "limitations": COMMON_LIMITS, "method": "get_company_performance"},
    "get_therapy_performance": {
        "purpose": "Therapy area / group / subgroup performance with hierarchy attributes.",
        "input_schema": _schema({"level": {"type": "string", "enum": list(THERAPY_LEVELS)}, "anchor": DATE,
                                 "basis": BASIS, "within_supergroup": KEY, "top_n": TOP_N}),
        "output": _perf_output(["therapy_group", "supergroup", "acute_chronic"]),
        "limitations": COMMON_LIMITS + ["therapy_group is not nested in supergroup (2 source exceptions)."],
        "method": "get_therapy_performance"},
    "get_segment_analysis": {
        "purpose": "Split of a scope by one source-confirmed segment.",
        "input_schema": _schema({"segment": {"type": "string", "enum": list(SEGMENTS)}, "anchor": DATE, "basis": BASIS,
                                 "market_level": {"type": "string", "enum": list(MARKET_LEVELS) + ["company"]},
                                 "market_key": KEY}, ["segment"]),
        "output": _perf_output(), "limitations": COMMON_LIMITS, "method": "get_segment_analysis"},
    "get_opportunity_scores": {
        "purpose": "DESCRIPTIVE opportunity score (0-100, OPP methodology) for products-in-market or markets. "
                   "Not a forecast, probability, prediction or causal estimate.",
        "input_schema": _schema({"level": {"type": "string", "enum": list(OPP_LEVELS)}, "anchor": DATE,
                                 "basis": {"type": "string", "enum": list(OPP_CONFIG.allowed_bases)},
                                 "market_level": {"type": "string",
                                                  "enum": sorted(set(PRODUCT_FILTER_LEVELS) | set(MARKET_FILTER_LEVELS))},
                                 "market_key": KEY, "company": KEY, "include_insufficient": {"type": "boolean"},
                                 "top_n": TOP_N}),
        "output": {"envelope": ["function", "filters", "period", "methodology", "national_value_growth_pct",
                                "status_counts", "row_count", "total_rows", "rows", "evidence", "caveats"],
                   "row_fields": ["entity_key", "entity_label", "score", "score_status", "insufficient_reason",
                                  "opportunity_rank", "rank_in_selection", "methodology_version", "components",
                                  "positive_drivers", "constraints", "matrix_quadrant"]},
        "limitations": ["Relative percentile-based evidence ranking; weights are methodology assumptions."],
        "method": "get_opportunity_scores"},
    "get_opportunity_detail": {
        "purpose": "Full component breakdown for one opportunity entity (incl. insufficient-evidence reason).",
        "input_schema": _schema({"level": {"type": "string", "enum": list(OPP_LEVELS)}, "entity_key": KEY,
                                 "anchor": DATE, "basis": {"type": "string", "enum": list(OPP_CONFIG.allowed_bases)}},
                                ["level", "entity_key"]),
        "output": {"envelope": ["function", "filters", "period", "methodology", "rows", "evidence", "caveats"]},
        "limitations": ["Product level uses the product-in-subgroup key (source INDEX)."],
        "method": "get_opportunity_detail"},
    "run_scenario": {
        "purpose": "Deterministic WHAT-IF arithmetic on an observed baseline under explicit assumptions. NOT a forecast.",
        "input_schema": _schema({
            "scenario_type": {"type": "string", "enum": list(SCENARIO_TYPES)},
            "entity_type": {"type": "string", "enum": list(ENTITY_TYPES)}, "entity_key": KEY,
            "assumptions": {"type": "object", "properties": {
                "price_change_pct": {"type": "number"}, "volume_change_pct": {"type": "number"},
                "market_growth_pct": {"type": "number"}, "target_share_pct": {"type": "number"}},
                "additionalProperties": False},
            "anchor": DATE, "basis": BASIS,
            "market_level": {"type": "string", "enum": list(MARKET_LEVELS) + ["company"]}, "market_key": KEY},
            ["scenario_type", "entity_type", "entity_key", "assumptions"]),
        "output": {"envelope": ["status", "scenario_id", "scenario_type", "methodology_version", "entity", "period",
                                "baseline", "assumptions", "scenario_result", "absolute_change", "percentage_change",
                                "units", "calculation_basis", "assumptions_and_limitations", "evidence"]},
        "limitations": ["No elasticity, competitor reaction, seasonality or forecasting.",
                        "Assumption ranges: price (-100, 1000], volume/growth [-100, 1000], share [0, 100]."],
        "method": "run_scenario"},
    "get_scenario_baseline": {
        "purpose": "Observed baseline (value, units, qty, price) for one entity, optionally within a market.",
        "input_schema": _schema({"entity_type": {"type": "string", "enum": list(ENTITY_TYPES)}, "entity_key": KEY,
                                 "anchor": DATE, "basis": BASIS,
                                 "market_level": {"type": "string", "enum": list(MARKET_LEVELS) + ["company"]},
                                 "market_key": KEY}, ["entity_type", "entity_key"]),
        "output": {"envelope": ["status", "function", "methodology_version", "entity", "period", "baseline", "units"]},
        "limitations": ["Price is value/units (INR per pack); undefined when units are 0."],
        "method": "get_scenario_baseline"},
    "get_data_quality_status": {
        "purpose": "Deterministic structural data-quality checks of the active dataset (keys, grain, nulls, snapshot = "
                   "sum of months, hierarchy) with coverage. Counts only; no business metric.",
        "input_schema": _schema({}),
        "output": {"envelope": ANALYTICS_ENVELOPE + ["status", "coverage"],
                   "row_fields": ["check", "status", "observed_count", "expected_count"]},
        "limitations": ["National data only: no geography dimension exists, so geographic questions are unsupported."],
        "method": "get_data_quality_status"},
}

# ---------------------------------------------------------------- errors
ERROR_CATEGORIES = {
    "INVALID_INPUT": "Invalid input", "MISSING_PARAMETER": "Missing required parameter",
    "UNKNOWN_TOOL": "Unknown tool", "UNSUPPORTED_ANALYSIS": "Unsupported analysis",
    "UNSUPPORTED_GEOGRAPHY": "Unsupported geography (source is national only)",
    "UNSUPPORTED_CHANNEL": "Unsupported channel analysis", "UNSUPPORTED_SSA_HSA_DSA": "Unsupported SSA/HSA/DSA request",
    "INVALID_PERIOD": "Invalid or unavailable period", "INSUFFICIENT_EVIDENCE": "Insufficient evidence",
    "INVALID_SCENARIO": "Invalid scenario assumption", "ENTITY_NOT_FOUND": "Entity not found",
    "INTERNAL_ERROR": "Internal application error",
}
ENGINE_CODE_MAP = {"invalid_parameter": "INVALID_INPUT", "invalid_assumption": "INVALID_SCENARIO",
                   "period_unavailable": "INVALID_PERIOD", "not_found": "ENTITY_NOT_FOUND",
                   "insufficient_baseline": "INSUFFICIENT_EVIDENCE", "unsupported": "UNSUPPORTED_ANALYSIS"}
_UNSUPPORTED_TERMS = [(re.compile(r"\b(geograph\w*|region|state|zone|city)\b", re.I), "UNSUPPORTED_GEOGRAPHY"),
                      (re.compile(r"\b(ssa|hsa|dsa)\b", re.I), "UNSUPPORTED_SSA_HSA_DSA"),
                      (re.compile(r"\bchannel\b", re.I), "UNSUPPORTED_CHANNEL"),
                      (re.compile(r"forecast|elasticity|predict|prescriber|\bhcp\b|promotion", re.I),
                       "UNSUPPORTED_ANALYSIS")]
_PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\s\"']*)|(/(?:e|c|d|home|users|mnt)/[^\s\"']*)|([^\s\"']+\.(?:parquet|xlsx|duckdb))",
                      re.I)


class ToolError(Exception):
    def __init__(self, code: str, message: str, engine_code: str | None = None):
        super().__init__(message)
        self.code, self.message, self.engine_code = code, message, engine_code

    def to_dict(self):
        return {"ok": False, "error": {"code": self.code, "category": ERROR_CATEGORIES[self.code],
                                       "message": scrub(self.message), "engine_code": self.engine_code}}


def scrub(text: str) -> str:
    """Remove anything that looks like a filesystem path or private file name."""
    return _PATH_RE.sub("[redacted]", str(text))


def _scrub_obj(o):
    if isinstance(o, str):
        return scrub(o) if _PATH_RE.search(o) else o
    if isinstance(o, dict):
        return {k: _scrub_obj(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_scrub_obj(v) for v in o]
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- validation
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check(name, spec, value, path):
    if value is None:
        if spec.get("nullable"):
            return
        raise ToolError("INVALID_INPUT", f"{path} must not be null")
    t = spec["type"]
    ok = {"string": isinstance(value, str), "boolean": isinstance(value, bool), "object": isinstance(value, dict),
          "integer": isinstance(value, int) and not isinstance(value, bool),
          "number": isinstance(value, (int, float)) and not isinstance(value, bool)}[t]
    if not ok:
        raise ToolError("INVALID_INPUT", f"{path} must be of type {t}")
    if t == "string":
        if "enum" in spec and value not in spec["enum"]:
            for rx, code in _UNSUPPORTED_TERMS:
                if rx.search(value):
                    raise ToolError(code, f"{path}={value!r} is not supported by the source data")
            raise ToolError("INVALID_INPUT", f"{path} must be one of {spec['enum']}")
        if spec.get("format") == "date":
            if not _DATE_RE.match(value):
                raise ToolError("INVALID_INPUT", f"{path} must be an ISO date YYYY-MM-DD")
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                raise ToolError("INVALID_INPUT", f"{path} is not a valid date") from None
        if len(value) < spec.get("minLength", 0) or len(value) > spec.get("maxLength", 10_000):
            raise ToolError("INVALID_INPUT", f"{path} has invalid length")
    if t in ("integer", "number"):
        if t == "number" and not math.isfinite(value):
            raise ToolError("INVALID_INPUT", f"{path} must be finite")
        if value < spec.get("minimum", -math.inf) or value > spec.get("maximum", math.inf):
            raise ToolError("INVALID_INPUT", f"{path} out of range")
    if t == "object" and "properties" in spec:
        validate(spec, value, path)


def validate(schema: dict, params, path="params"):
    if not isinstance(params, dict):
        raise ToolError("INVALID_INPUT", f"{path} must be a JSON object")
    extra = set(params) - set(schema["properties"])
    if extra and schema.get("additionalProperties", True) is False:
        raise ToolError("INVALID_INPUT", f"unexpected parameter(s): {sorted(extra)}")
    for r in schema.get("required", []):
        if r not in params:
            raise ToolError("MISSING_PARAMETER", f"missing required parameter: {r}")
    for k, v in params.items():
        _check(k, schema["properties"][k], v, f"{path}.{k}" if path != "params" else k)


# ---------------------------------------------------------------- registry
def contracts() -> dict:
    """Machine-readable tool contracts (JSON-serialisable)."""
    return {"tool_api_version": TOOL_API_VERSION,
            "tools": [{"name": n, "purpose": t["purpose"], "input_schema": t["input_schema"],
                       "output": t["output"], "limitations": t["limitations"],
                       "errors": sorted(ERROR_CATEGORIES)} for n, t in TOOLS.items()],
            "error_codes": ERROR_CATEGORIES}


class ToolRegistry:
    """Executes whitelisted tools against one CommercialAnalytics instance."""

    def __init__(self, api):
        self.api = api

    def metadata(self) -> dict:
        periods = [r[0].isoformat() for r in self.api.con.execute("SELECT period FROM dim_period ORDER BY 1").fetchall()]
        return {"periods": periods, "default_anchor": periods[-1], "bases": list(BASES),
                "basis_labels": {"MONTH": "Month", "YTD": "Calendar YTD", "MAT": "MAT (moving annual total)"},
                "market_levels": list(MARKET_LEVELS), "therapy_levels": list(THERAPY_LEVELS),
                "segments": list(SEGMENTS), "product_scopes": list(PRODUCT_SCOPES),
                "company_scopes": list(COMPANY_SCOPES), "entity_types": list(ENTITY_TYPES),
                "scenario_types": list(SCENARIO_TYPES), "scenario_methodology_version": SCN_VERSION,
                "opportunity_levels": list(OPP_LEVELS), "opportunity_bases": list(OPP_CONFIG.allowed_bases),
                "opportunity_methodology_version": OPP_CONFIG.version,
                "opportunity_fingerprint": OPP_CONFIG.fingerprint(), "tool_api_version": TOOL_API_VERSION,
                "dataset": getattr(self.api, "dataset", "ims"), "max_rows": MAX_ROWS,
                "max_product_rows": MAX_PRODUCT_ROWS}

    @staticmethod
    def row_cap(name: str, params: dict) -> int | None:
        """Safe maximum rows for a tool call (None: the tool has no top_n)."""
        if "top_n" not in TOOLS[name]["input_schema"]["properties"]:
            return None
        product = name == "get_brand_performance" or (name == "get_opportunity_scores"
                                                      and params.get("level", "product") == "product")
        return MAX_PRODUCT_ROWS if product else MAX_ROWS

    def _apply_row_cap(self, name: str, params: dict) -> tuple[dict, int | None]:
        cap = self.row_cap(name, params)
        if cap is None:
            return params, None
        if "top_n" not in params:                  # absent: the engine default if it is bounded, else the cap
            p = inspect.signature(getattr(self.api, TOOLS[name]["method"])).parameters.get("top_n")
            default = p.default if p is not None else None
            if isinstance(default, int) and 1 <= default <= cap:
                return params, None
            return {**params, "top_n": cap}, cap
        n = params["top_n"]
        if n is None:                              # null: the safe maximum, never "all rows"
            return {**params, "top_n": cap}, cap
        if n > cap:
            raise ToolError("INVALID_INPUT", f"top_n must be at most {cap} for {name} (safe maximum)")
        return params, None

    def invoke(self, name: str, params: dict | None = None) -> dict:
        """Always returns a JSON-serialisable dict: {"ok": true, "tool", "result"} or {"ok": false, "error"}."""
        try:
            if name not in TOOLS:
                raise ToolError("UNKNOWN_TOOL", f"unknown tool {name!r}; see /api/tools")
            params = {} if params is None else params
            validate(TOOLS[name]["input_schema"], params)
            params, capped = self._apply_row_cap(name, params)
            if name == "get_application_metadata":
                result = self.metadata()
            else:
                result = getattr(self.api, TOOLS[name]["method"])(**params)
            if capped and isinstance(result, dict) and (result.get("total_rows") or 0) > (result.get("row_count") or 0):
                result = {**result, "caveats": list(result.get("caveats") or []) + [
                    f"Result limited to the safe maximum of {capped} rows; total_rows gives the full count."]}
            return {"ok": True, "tool": name, "result": _scrub_obj(result)}
        except ToolError as e:
            return e.to_dict()
        except AnalyticsError as e:
            code = ENGINE_CODE_MAP.get(e.code, "INVALID_INPUT")
            if code == "UNSUPPORTED_ANALYSIS":
                blob = json.dumps(params, default=str) + " " + e.message
                code = next((c for rx, c in _UNSUPPORTED_TERMS if rx.search(blob)), code)
            return ToolError(code, e.message, e.code).to_dict()
        except Exception:  # never leak internals
            log.exception("internal error in tool %s", name)
            return ToolError("INTERNAL_ERROR", "The request could not be completed due to an internal error.").to_dict()
