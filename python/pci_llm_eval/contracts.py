"""Function declarations, local tool execution and output contract for the Gemini validation (Phase 2).

Declarations are GENERATED from the live M8 ToolRegistry contracts (never hand-copied), plus one tool the brief
requires that the M8 registry deliberately does not expose:
  * get_geography_performance - the licensed data has NO geography: always a structured UNSUPPORTED_GEOGRAPHY
    result (never data), so a model that calls it is told explicitly that the question cannot be answered;
  * get_data_quality_status   - deterministic data-quality checks computed by SQL on the active dataset.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

AGENTS = {  # agent -> tools it may call (mirrors the M9 ownership; Segment/Insight names follow the brief)
    "MarketTrendAgent": ["get_market_performance", "get_market_trends", "get_therapy_performance"],
    "BrandPerformanceAgent": ["find_products", "get_brand_performance", "get_brand_growth", "get_brand_share"],
    "SegmentAgent": ["get_segment_analysis", "get_company_performance", "get_geography_performance"],
    "OpportunityAgent": ["get_opportunity_scores", "get_opportunity_detail"],
    "ScenarioAgent": ["run_scenario", "get_scenario_baseline"],
    "InsightQAAgent": ["get_data_quality_status", "get_application_metadata"],
}
EXTRA_TOOLS = [
    {"name": "get_geography_performance",
     "purpose": "Geographic performance. NOT AVAILABLE: the dataset is national with no geography field. Always "
                "returns UNSUPPORTED_GEOGRAPHY; never estimate or invent regional figures.",
     "input_schema": {"type": "object", "properties": {"region": {"type": "string"}}, "required": [],
                      "additionalProperties": False}}
]
OUTPUT_CONTRACT = {
    "type": "object",
    "required": ["intent", "selected_agent", "tool_calls", "filters", "evidence", "answer", "limitations",
                 "verification_status"],
    "properties": {
        "intent": {"type": "string"},
        "selected_agent": {"type": "string", "enum": sorted(AGENTS) + ["NONE"]},
        "tool_calls": {"type": "array", "items": {"type": "object"}},
        "filters": {"type": "object"},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "answer": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "verification_status": {"type": "string", "enum": ["UNVERIFIED", "VERIFIED", "QA_FAILED", "REFUSED"]},
    },
}
_KEEP = {"type", "description", "enum", "items", "properties", "required", "minimum", "maximum", "format",
         "nullable"}


def _to_gemini(schema: dict) -> dict:
    """JSON Schema -> Gemini Schema (OpenAPI subset: upper-case types; unsupported keywords dropped)."""
    out = {}
    for k, v in schema.items():
        if k not in _KEEP:
            continue
        if k == "type":
            t = v if isinstance(v, str) else next(x for x in v if x != "null")
            out["type"] = t.upper()
            if not isinstance(v, str) and "null" in v:
                out["nullable"] = True
        elif k == "properties":
            out["properties"] = {n: _to_gemini(s) for n, s in v.items()}
        elif k == "items":
            out["items"] = _to_gemini(v)
        else:
            out[k] = copy.deepcopy(v)
    if out.get("type") == "OBJECT" and not out.get("properties"):
        out.pop("properties", None)
    return out


def tool_contracts() -> list[dict]:
    from pci_app.tools import contracts
    return contracts()["tools"] + EXTRA_TOOLS


def function_declarations() -> list[dict]:
    decls = []
    for t in tool_contracts():
        d = {"name": t["name"], "description": t["purpose"]}
        params = _to_gemini(t["input_schema"])
        if params.get("properties"):
            d["parameters"] = params
        decls.append(d)
    return decls


class LocalTools:
    """Executes every declared tool locally on the ACTIVE dataset (the runner enforces synthetic)."""

    def __init__(self):
        from pci_app.tools import ToolRegistry
        from pci_analytics.api import CommercialAnalytics
        self.api = CommercialAnalytics()
        self.registry = ToolRegistry(self.api)

    def call(self, name: str, args: dict) -> dict:
        if name == "get_geography_performance":
            return {"ok": False, "tool": name, "error": {"code": "UNSUPPORTED_GEOGRAPHY",
                    "message": "The dataset is national: it has no geography field. Regional figures cannot be provided."}}
        return self.registry.invoke(name, args or {})


def write_bundle(out_dir: Path) -> list[Path]:
    """Files for Google AI Studio (paste/import) and for the runner."""
    out_dir.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).parent
    files = {"function_declarations.json": function_declarations(), "output_contract.schema.json": OUTPUT_CONTRACT,
             "agents.json": AGENTS}
    paths = []
    for name, obj in files.items():
        p = out_dir / name
        p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        paths.append(p)
    for name in ("system_prompt.md", "eval_cases.json"):
        p = out_dir / name
        p.write_text((here / name).read_text(encoding="utf-8"), encoding="utf-8")
        paths.append(p)
    return paths
