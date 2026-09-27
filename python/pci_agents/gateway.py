"""ToolGateway: the ONLY path from agents to analytics (-> M8 ToolRegistry.invoke).

- enforces per-agent tool permissions (deny by default),
- records a minimal, sanitised execution record per call (no raw rows),
- keeps full tool results only in the in-memory request context for QA (never persisted).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

AGENT_TOOLS = {
    "MarketTrendAgent": ("get_market_performance", "get_market_trends"),
    "BrandProductAgent": ("find_products", "get_brand_performance", "get_brand_growth", "get_brand_share"),
    "CompanySegmentAgent": ("get_company_performance", "get_therapy_performance", "get_segment_analysis"),
    "OpportunityAgent": ("get_opportunity_scores", "get_opportunity_detail"),
    "ScenarioAgent": ("get_scenario_baseline", "run_scenario"),
    "DataQualityAgent": ("get_data_quality_status",),
    "Orchestrator": (),           # invokes agents only
    "InsightQAAgent": (),         # reads recorded results only
}


# tools each intent may legitimately use (QA `tool_scope`: an unexpected but permitted tool is still a failure)
INTENT_TOOLS = {
    "MARKET_PERFORMANCE": {"get_market_performance"}, "MARKET_TREND": {"get_market_trends"},
    "BRAND_PERFORMANCE": {"find_products", "get_brand_share", "get_brand_growth"},
    "TOP_PRODUCTS": {"get_brand_performance"}, "COMPANY_PERFORMANCE": {"get_company_performance"},
    "THERAPY_PERFORMANCE": {"get_therapy_performance"}, "SEGMENT_ANALYSIS": {"get_segment_analysis"},
    "OPPORTUNITY": {"get_opportunity_scores"}, "OPPORTUNITY_DETAIL": {"get_opportunity_detail"},
    "SCENARIO": {"run_scenario"}, "SCENARIO_BASELINE": {"get_scenario_baseline"},
    "GROWTH_AND_MOMENTUM": {"get_market_performance", "get_opportunity_scores"},
    "DATA_QUALITY": {"get_data_quality_status"},
}
# keys every successful result of a tool must carry (QA `evidence_complete`)
REQUIRED_RESULT_KEYS = {
    "get_market_performance": {"period", "units", "evidence", "caveats", "rows"},
    "get_brand_performance": {"period", "units", "evidence", "caveats", "rows"},
    "get_brand_share": {"period", "units", "evidence", "caveats", "rows"},
    "get_company_performance": {"period", "units", "evidence", "caveats", "rows"},
    "get_therapy_performance": {"period", "units", "evidence", "caveats", "rows"},
    "get_segment_analysis": {"period", "units", "evidence", "caveats", "rows"},
    "get_market_trends": {"units", "evidence", "caveats", "rows"},
    "get_brand_growth": {"units", "evidence", "caveats", "rows"},
    "find_products": {"evidence", "rows"},
    "get_opportunity_scores": {"methodology", "period", "evidence", "caveats", "rows"},
    "get_opportunity_detail": {"methodology", "period", "evidence", "caveats", "rows"},
    "run_scenario": {"baseline", "assumptions", "scenario_result", "absolute_change", "percentage_change", "units",
                     "assumptions_and_limitations", "evidence", "methodology_version", "entity"},
    "get_scenario_baseline": {"baseline", "units", "evidence", "entity"},
    "get_data_quality_status": {"status", "coverage", "evidence", "rows"},
}


class ToolNotPermitted(Exception):
    def __init__(self, agent, tool):
        super().__init__(f"{agent} is not permitted to call {tool!r}")
        self.agent, self.tool = agent, tool


@dataclass
class RequestContext:
    """Minimum execution state for ONE request; discarded after the response is built."""
    request_id: str
    request: dict
    results: dict = field(default_factory=dict)       # call seq -> full tool result (in memory only)
    records: list = field(default_factory=list)       # sanitised execution log
    denied: list = field(default_factory=list)


class ToolGateway:
    def __init__(self, registry):
        self.__registry = registry        # agents receive the gateway, never the registry/engines

    def call(self, ctx: RequestContext, agent: str, tool: str, params: dict) -> dict:
        seq = len(ctx.records) + 1
        rec = {"call": seq, "agent": agent, "tool": tool, "input": dict(params), "order": seq}
        if tool not in AGENT_TOOLS.get(agent, ()):
            rec.update(status="DENIED", error_code="TOOL_NOT_PERMITTED", elapsed_ms=0)
            ctx.records.append(rec)
            ctx.denied.append((agent, tool))
            raise ToolNotPermitted(agent, tool)
        t = time.perf_counter()
        out = self.__registry.invoke(tool, params)
        rec["elapsed_ms"] = round((time.perf_counter() - t) * 1000, 1)
        if out["ok"]:
            res = out["result"]
            ctx.results[seq] = res
            rec.update(status="OK", error_code=None, row_count=res.get("row_count"), total_rows=res.get("total_rows"))
        else:
            ctx.results[seq] = {"error": out["error"]}
            rec.update(status="ERROR", error_code=out["error"]["code"], error_message=out["error"]["message"])
        ctx.records.append(rec)
        return {"call": seq, **out}
