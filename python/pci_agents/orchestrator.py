"""Orchestrator: REQUEST -> INTENT -> SPECIALIST AGENT(S) -> TOOL CALL(S) -> QA -> FINAL RESPONSE.

The orchestrator routes and assembles; it never calls tools itself and never calculates metrics.
Only the minimum per-request context is held in memory; nothing is persisted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid

from pci_app.tools import _scrub_obj

from . import agents as A
from . import qa
from . import schemas as S
from .gateway import RequestContext, ToolGateway, ToolNotPermitted
from .providers import LLMProvider, get_provider

# intent -> ordered workflow steps (agent class, method). Multi-step only where justified.
ROUTES = {
    "MARKET_PERFORMANCE": [(A.MarketTrendAgent, "performance")],
    "MARKET_TREND": [(A.MarketTrendAgent, "trend")],
    "BRAND_PERFORMANCE": [(A.BrandProductAgent, "performance")],
    "TOP_PRODUCTS": [(A.BrandProductAgent, "top_products")],
    "COMPANY_PERFORMANCE": [(A.CompanySegmentAgent, "company")],
    "THERAPY_PERFORMANCE": [(A.CompanySegmentAgent, "therapy")],
    "SEGMENT_ANALYSIS": [(A.CompanySegmentAgent, "segment")],
    "OPPORTUNITY": [(A.OpportunityAgent, "scores")],
    "OPPORTUNITY_DETAIL": [(A.OpportunityAgent, "detail")],
    "SCENARIO": [(A.ScenarioAgent, "run")],
    "SCENARIO_BASELINE": [(A.ScenarioAgent, "baseline")],
    "GROWTH_AND_MOMENTUM": [(A.MarketTrendAgent, "growth_leaders"), (A.OpportunityAgent, "scores")],
    "DATA_QUALITY": [(A.DataQualityAgent, "status")],
}
INTENTS = tuple(ROUTES)
_P = ("anchor", "basis")
# allowed request parameters per intent: anything else is rejected (never silently ignored)
INTENT_PARAMS = {
    "MARKET_PERFORMANCE": {"level", "top_n", *_P}, "MARKET_TREND": {"level", "key"},
    "BRAND_PERFORMANCE": {"name", "prod_code", "market_level", "market_key", *_P},
    "TOP_PRODUCTS": {"market_level", "market_key", "top_n", *_P},
    "COMPANY_PERFORMANCE": {"market_level", "market_key", "top_n", *_P},
    "THERAPY_PERFORMANCE": {"level", "within_supergroup", "top_n", *_P},
    "SEGMENT_ANALYSIS": {"segment", "market_level", "market_key", *_P},
    "OPPORTUNITY": {"level", "market_level", "market_key", "company", "top_n", *_P},
    "OPPORTUNITY_DETAIL": {"level", "entity_key", *_P},
    "SCENARIO": {"scenario_type", "entity_type", "entity_key", "assumptions", "market_level", "market_key", *_P},
    "SCENARIO_BASELINE": {"entity_type", "entity_key", "market_level", "market_key", *_P},
    "GROWTH_AND_MOMENTUM": {"top_n", *_P},
    "DATA_QUALITY": set(),
}


class Orchestrator:
    name = "Orchestrator"

    def __init__(self, registry, provider: LLMProvider | None = None):
        self.provider = provider or get_provider()
        self._gateway = ToolGateway(registry)
        self._secret = secrets.token_bytes(32)     # per-process key for clarification tokens (memory only)
        self._contracts = None
        try:
            from pci_app.tools import contracts
            self._contracts = contracts()
        except Exception:  # contracts are informational for providers
            self._contracts = {}

    def status(self) -> dict:
        md = self.provider.metadata()
        return {"provider": md, "mode_label": md.get("label", self.provider.name), "llm_connected": md["is_llm"],
                "intents": list(INTENTS), "agents": sorted({a.name for steps in ROUTES.values() for a, _ in steps}),
                "network_required": False, "api_key_required": False}

    # ------------------------------------------------------------------ main entry
    def handle(self, request: dict) -> dict:
        """request = {"text": "..."} (provider plans) or {"intent": ..., "params": {...}} (structured)."""
        t0 = time.perf_counter()
        ctx = RequestContext(request_id=uuid.uuid4().hex[:12], request=request if isinstance(request, dict) else {})
        base = {"request_id": ctx.request_id, "mode": self.status()["provider"], "intent": None, "route": [],
                "request_params": {}, "tool_calls": ctx.records, "evidence": [], "findings": [], "limitations": [],
                "banners": [], "clarification": None, "message": None}
        if not isinstance(request, dict):
            return self._finish(ctx, {**base, "status": S.UNRECOGNIZED_REQUEST, "message": "request must be an object"}, t0)
        if "continuation" in request:                      # M10: turn 2 of a clarification (request-scoped only)
            plan = self._resume(request)
            if "reclarify" in plan:
                resp = {**base, "intent": plan["intent"], "status": S.AMBIGUOUS_ENTITY, "message": plan["message"],
                        "clarification": plan["reclarify"], "continuation": request["continuation"]}
                return self._finish(ctx, resp, t0)
        elif "intent" in request:
            plan = {"intent": request.get("intent"), "params": request.get("params") or {}}
        else:
            plan = self.provider.plan(str(request.get("text", "")), self._contracts)
        if "refusal" in plan:
            resp = {**base, "status": plan["refusal"], "message": plan["message"]}
            if plan.get("supported"):
                resp["supported_requests"] = plan["supported"]
            return self._finish(ctx, resp, t0)
        intent, params = plan.get("intent"), plan.get("params") or {}
        if intent not in ROUTES or not isinstance(params, dict):
            return self._finish(ctx, {**base, "status": S.UNRECOGNIZED_REQUEST,
                                      "message": f"unknown intent {intent!r}", "supported_intents": list(INTENTS)}, t0)
        unknown = sorted(set(params) - INTENT_PARAMS[intent])
        missing = intent == "BRAND_PERFORMANCE" and not (params.get("name") or params.get("prod_code"))
        if unknown or missing:
            return self._finish(ctx, {**base, "intent": intent, "status": "INVALID_INPUT",
                                      "message": f"unexpected parameter(s) for {intent}: {unknown}" if unknown
                                      else "BRAND_PERFORMANCE needs 'name' or 'prod_code'"}, t0)
        resp = {**base, "intent": intent, "request_params": params}
        outcomes = []
        for agent_cls, method in ROUTES[intent]:
            agent = agent_cls(self._gateway)
            resp["route"].append(agent.name)
            step_params = dict(params)
            if intent == "GROWTH_AND_MOMENTUM" and agent_cls is A.OpportunityAgent:
                step_params.update(level="product", basis=params.get("basis") if params.get("basis") != "MONTH" else "MAT")
            try:
                out = getattr(agent, method)(ctx, step_params)
            except ToolNotPermitted as e:
                out = A.AgentOutcome(status=S.TOOL_NOT_PERMITTED, message=str(e))
            except Exception:           # malformed/unexpected tool output: fail safe, never improvise an answer
                out = A.AgentOutcome(status="INTERNAL_ERROR", message="A tool result could not be interpreted; "
                                                                      "no answer was produced.")
            outcomes.append(out)
            if out.status != S.OK:
                break
        resp["route"].append("InsightQAAgent")
        final = next((o for o in outcomes if o.status != S.OK), None)
        resp["status"] = final.status if final else S.OK
        for o in outcomes:
            resp["findings"] += o.findings
            resp["banners"] += [b for b in o.banners if b not in resp["banners"]]
            resp["limitations"] += [x for x in o.limitations if x not in resp["limitations"]]
            resp["clarification"] = resp["clarification"] or o.clarification
        resp["message"] = final.message if final else None
        resp["evidence"] = self._evidence(ctx)
        if resp["status"] == S.AMBIGUOUS_ENTITY and resp["clarification"]:
            resp["continuation"] = self._token(intent, params, resp["clarification"])
        return self._finish(ctx, resp, t0)

    # ------------------------------------------------------------------ multi-turn clarification (M10)
    # Stateless and tamper-evident: the clarification state travels with the client as an HMAC-signed token
    # (per-process in-memory key, never persisted). It carries only intent, original params and the options.
    def _token(self, intent, params, clarification) -> str:
        payload = {"v": 1, "intent": intent, "params": {k: v for k, v in params.items() if k != "name"},
                   "message": clarification["message"],
                   "options": [{k: o[k] for k in ("prod_code", "brand", "company", "label")} for o in clarification["options"]]}
        body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        sig = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def _resume(self, request) -> dict:
        tok = request.get("continuation")
        try:
            body, sig = str(tok).rsplit(".", 1)
            if not hmac.compare_digest(sig, hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()):
                raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(body.encode()))
        except (ValueError, TypeError, json.JSONDecodeError):
            return {"refusal": "INVALID_INPUT", "message": "The clarification token is invalid or has been altered."}
        if set(request) - {"continuation", "select", "text"}:
            return {"refusal": "INVALID_INPUT", "message": "A clarification reply may only carry the selection."}
        sel = request.get("select")
        if sel is None:
            m = re.search(r"\b(\d{1,12})\b", str(request.get("text", "")))
            sel = m.group(1) if m else None
        codes = [o["prod_code"] for o in payload["options"]]
        if sel is None or str(sel) not in codes:
            return {"intent": payload["intent"], "reclarify": {"message": payload["message"],
                                                               "options": payload["options"]},
                    "message": "The selection is not one of the listed product codes; automatic selection is not "
                               "permitted. Please choose one of the product codes listed."}
        return {"intent": payload["intent"], "params": {**payload["params"], "prod_code": str(sel)}}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _evidence(ctx):
        ev = []
        for rec in ctx.records:
            res = ctx.results.get(rec["call"], {})
            if rec["status"] != "OK":
                continue
            item = {"call": rec["call"], "tool": rec["tool"], "agent": rec["agent"]}
            for k in ("period", "filters", "units", "methodology", "evidence", "methodology_version", "scenario_id"):
                if k in res:
                    item[k] = res[k]
            ev.append(item)
        return ev

    def _finish(self, ctx, resp, t0):
        text = self.provider.compose(resp)
        tq = time.perf_counter()
        try:
            resp["qa"] = qa.validate(ctx, resp, text)
        except Exception:                                 # QA must never be bypassed: an unvalidatable draft fails
            resp["qa"] = {"passed": False, "checks": [], "engine": "deterministic QA (no LLM)",
                          "failures": [{"check": "qa_engine", "passed": False,
                                        "detail": "the draft could not be validated"}]}
        qa_ms = (time.perf_counter() - tq) * 1000
        if not resp["qa"]["passed"]:
            resp["withheld_findings"] = len(resp["findings"])
            resp.update(status=S.QA_FAILED, findings=[], clarification=None, continuation=None,
                        final_response="The draft response failed deterministic QA checks and was withheld. "
                                       "See qa.failures.")
        else:
            resp["final_response"] = text
            rec = {r["call"]: r for r in ctx.records}
            # claim -> tool -> result field -> value (no result rows are exposed)
            resp["provenance"] = [{"claim": i, "value": v["display"], "unit": v["unit"],
                                   "tool": rec[v["source"]["call"]]["tool"], "call": v["source"]["call"],
                                   "field": ".".join(f"[{p}]" if isinstance(p, int) else str(p)
                                                     for p in v["source"]["path"]).replace(".[", "[")}
                                  for i, f in enumerate(resp["findings"]) for v in f["values"]]
        resp["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        resp["timing"] = {"tools_ms": round(sum(r.get("elapsed_ms") or 0 for r in ctx.records), 1),
                          "qa_ms": round(qa_ms, 2), "total_ms": resp["elapsed_ms"]}
        return _scrub_obj(resp)       # M8 sanitiser: no paths (even echoed user input), no non-finite numbers
