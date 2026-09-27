"""Negative controls for M10: hallucinating providers, disclaimer-dropping providers, fault-injecting
registries, rogue agents and simulated tool failures. Every control must be BLOCKED (QA_FAILED) or fail
safe (INTERNAL_ERROR / error passthrough) — never returned as a successful answer.
All controls are local test doubles; none call a network or an LLM.
"""
from __future__ import annotations

import contextlib
import copy
import re

from pci_agents import agents as A
from pci_agents.orchestrator import Orchestrator
from pci_agents.providers import DeterministicDemoProvider


# ---------------------------------------------------------------- providers (text-level faults)
class _Append(DeterministicDemoProvider):
    extra = ""
    is_llm = True

    def compose(self, response):
        return super().compose(response) + "\n" + self.extra


def appending(name, extra):
    return type(name, (_Append,), {"name": name, "extra": extra})


class AlterFirstNumber(DeterministicDemoProvider):
    """Changes the first finding value in the text: a known tool number replaced by a different one."""
    name, is_llm = "ALTER_NUMBER", True

    def compose(self, response):
        text = super().compose(response)
        if not response["findings"] or not response["findings"][0]["values"]:
            return text + "\n7777.77"
        d = next(v["display"] for v in response["findings"][0]["values"] if v["display"] != "n/a")
        digits = re.sub(r"\d(?=\D*$)", lambda m: str((int(m.group(0)) + 3) % 10), d)
        return text.replace(d, digits + "9", 1)


class DropLines(DeterministicDemoProvider):
    """Removes banner/limitation lines containing `needle`."""
    name, is_llm, needle = "DROP", True, ""

    def compose(self, response):
        return "\n".join(ln for ln in super().compose(response).splitlines() if self.needle not in ln)


def dropping(name, needle):
    return type(name, (DropLines,), {"name": name, "needle": needle})


class ReplaceWords(DeterministicDemoProvider):
    name, is_llm, pairs = "REPLACE", True, ()

    def compose(self, response):
        t = super().compose(response)
        for a, b in self.pairs:
            t = t.replace(a, b)
        return t


# ---------------------------------------------------------------- registries (tool-result faults)
class FaultyRegistry:
    """Wraps the real ToolRegistry and mutates the successful result of one tool."""

    def __init__(self, registry, tool, mutate):
        self._r, self._tool, self._mutate = registry, tool, mutate

    def invoke(self, name, params=None):
        out = self._r.invoke(name, params)
        if name == self._tool and out.get("ok"):
            out = copy.deepcopy(out)
            self._mutate(out["result"])
        return out


class FailingRegistry:
    """Simulates a tool failure with a given structured error code."""

    def __init__(self, registry, tool, code):
        self._r, self._tool, self._code = registry, tool, code

    def invoke(self, name, params=None):
        if name == self._tool:
            return {"ok": False, "error": {"code": self._code, "category": "simulated", "engine_code": None,
                                           "message": f"simulated {self._code} for evaluation"}}
        return self._r.invoke(name, params)


def _set(path, value):
    def m(res):
        o = res
        for p in path[:-1]:
            o = o[p]
        o[path[-1]] = value
    return m


def _delete(key):
    return lambda res: res.pop(key, None)


def _strip_scn_disclaimer(res):
    res["assumptions_and_limitations"] = [x for x in res["assumptions_and_limitations"]
                                          if "not a forecast" not in x.lower()]


@contextlib.contextmanager
def patched(obj, attr, value):
    old = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, old)


_ORIG_MARKET_PERFORMANCE = A.MarketTrendAgent.performance


def _unexpected_tool(self, ctx, p):             # permitted for the agent but not part of the intent's workflow
    out = _ORIG_MARKET_PERFORMANCE(self, ctx, p)
    self.tool(ctx, "get_market_trends", {"level": "total", "key": "TOTAL"})
    return out


def _unauthorized_tool(self, ctx, p):
    self.tool(ctx, "run_scenario", {"scenario_type": "PRICE_CHANGE", "entity_type": "total", "entity_key": "TOTAL",
                                    "assumptions": {"price_change_pct": 1}})


MARKET = {"text": "market performance by therapy area MAT for 2024-05 top 3"}
OPP = {"text": "product opportunities top 3"}
SCEN = {"text": 'what if price +5% for therapy area "CARDIAC"'}
BRAND = {"text": "product @top_product performance"}


def negative_controls():
    """(id, category, description, request, builder, expected_status, expected_failed_check)
    builder(registry) -> (orchestrator, patch-context-manager)"""
    P = lambda prov: (lambda reg: (Orchestrator(reg, prov()), contextlib.nullcontext()))   # noqa: E731
    R = lambda tool, mut: (lambda reg: (Orchestrator(FaultyRegistry(reg, tool, mut)), contextlib.nullcontext()))  # noqa: E731
    return [
        # --- numerical hallucination (text level)
        ("NC01", "hallucination", "known tool number replaced by a different number", MARKET,
         P(AlterFirstNumber), "QA_FAILED", "text_numbers_traceable"),
        ("NC02", "hallucination", "invented percentage", MARKET,
         P(appending("INV_PCT", "Market growth was 17.389% last year.")), "QA_FAILED", "text_numbers_traceable"),
        ("NC03", "hallucination", "invented market size", MARKET,
         P(appending("INV_SIZE", "Total market size is 98,765.43 INR crore.")), "QA_FAILED", "text_numbers_traceable"),
        ("NC04", "hallucination", "invented ranking", MARKET,
         P(appending("INV_RANK", "It ranks #9173 nationally.")), "QA_FAILED", "text_numbers_traceable"),
        ("NC05", "hallucination", "invented growth", BRAND,
         P(appending("INV_GROWTH", "Brand growth reached 23.917% in the period.")), "QA_FAILED", "text_numbers_traceable"),
        ("NC06", "hallucination", "invented price", SCEN,
         P(appending("INV_PRICE", "The implied price is 4,321.09 INR per pack.")), "QA_FAILED", "text_numbers_traceable"),
        ("NC07", "hallucination", "invented scenario result", SCEN,
         P(appending("INV_SCN", "Scenario value would be 55,555.55 INR crore.")), "QA_FAILED", "text_numbers_traceable"),
        # --- disclaimer / methodology transformation
        ("NC08", "disclaimer", "opportunity score described as a probability", OPP,
         P(appending("OPP_PROB", "These are the products most likely to succeed.")), "QA_FAILED", "no_unsupported_claims"),
        ("NC09", "disclaimer", "scenario converted into a forecast", SCEN,
         P(appending("SCN_FC", "This scenario forecasts that sales will grow.")), "QA_FAILED", "no_unsupported_claims"),
        ("NC10", "disclaimer", "scenario banner dropped", SCEN,
         P(dropping("DROP_BANNER", "NOT A FORECAST")), "QA_FAILED", "scenario_not_forecast"),
        ("NC11", "disclaimer", "opportunity methodology dropped", OPP,
         P(dropping("DROP_METHOD", "OPP-")), "QA_FAILED", "methodology_preserved"),
        ("NC12", "disclaimer", "market-definition note dropped", MARKET,
         P(dropping("DROP_MKT", "Market = source therapy SUBGROUP")), "QA_FAILED", "market_definition"),
        ("NC13", "disclaimer", "opportunity label rewritten as prediction", OPP,
         P(type("PRED", (ReplaceWords,), {"name": "PRED", "pairs": (("descriptive score", "predicted score"),)})),
         "QA_FAILED", "no_unsupported_claims"),
        # --- tool-result faults (QA)
        ("NC14", "fault", "wrong unit declared by tool", MARKET,
         R("get_market_performance", _set(["units"], {"value": "INR lakh"})), "QA_FAILED", "unit_consistency"),
        ("NC15", "fault", "wrong period returned by tool", MARKET,
         R("get_market_performance", _set(["period", "anchor"], "2023-05-01")), "QA_FAILED", "period_entity_consistency"),
        ("NC16", "fault", "wrong entity returned by tool", BRAND,
         R("get_brand_share", _set(["rows", 0, "entity_key"], "0")), "QA_FAILED", "requested_entity"),
        ("NC17", "fault", "opportunity methodology version altered", OPP,
         R("get_opportunity_scores", _set(["methodology", "version"], "OPP-0.9.0")), "QA_FAILED", "published_methodology"),
        ("NC18", "fault", "scenario result without not-a-forecast disclaimer", SCEN,
         R("run_scenario", _strip_scn_disclaimer), "QA_FAILED", "published_methodology"),
        ("NC19", "fault", "incomplete evidence (engine evidence missing)", MARKET,
         R("get_market_performance", _delete("evidence")), "QA_FAILED", "evidence_complete"),
        ("NC20", "fault", "unexpected (permitted) tool used", MARKET,
         lambda reg: (Orchestrator(reg), patched(A.MarketTrendAgent, "performance", _unexpected_tool)),
         "QA_FAILED", "tool_scope"),
        ("NC21", "fault", "unauthorized tool attempted", MARKET,
         lambda reg: (Orchestrator(reg), patched(A.MarketTrendAgent, "performance", _unauthorized_tool)),
         "QA_FAILED", "tool_provenance"),
        # --- malformed results: the agent fails safe (INTERNAL_ERROR) and QA names the defect
        ("NC22", "fault", "malformed tool result (rows not a list)", MARKET,
         R("get_market_performance", _set(["rows"], "garbage")), "QA_FAILED", "result_well_formed"),
        ("NC23", "fault", "missing number (value field absent)", MARKET,
         R("get_market_performance", lambda res: res["rows"][0].pop("value_cur")), "QA_FAILED", "result_well_formed"),
        ("NC24", "fault", "missing methodology block", OPP,
         R("get_opportunity_scores", _delete("methodology")), "QA_FAILED", "evidence_complete"),
    ]


TOOL_FAILURES = [  # (code, request, failing tool)
    ("ENTITY_NOT_FOUND", MARKET, "get_market_performance"),
    ("INVALID_PERIOD", MARKET, "get_market_performance"),
    ("INSUFFICIENT_EVIDENCE", SCEN, "run_scenario"),
    ("UNSUPPORTED_ANALYSIS", MARKET, "get_market_performance"),
    ("INVALID_SCENARIO", SCEN, "run_scenario"),
    ("INTERNAL_ERROR", OPP, "get_opportunity_scores"),
]
