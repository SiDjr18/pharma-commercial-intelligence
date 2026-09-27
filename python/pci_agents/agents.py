"""Specialist agents. Each agent:
- calls ONLY its permitted tools through the ToolGateway (never engines, SQL, files or network),
- turns tool results into findings that *reference* result fields (no arithmetic, no estimation),
- passes tool errors through unchanged and never picks an entity on the user's behalf.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import schemas as S
from .gateway import RequestContext, ToolGateway

MAX_FINDINGS = 20


@dataclass
class AgentOutcome:
    status: str = S.OK
    findings: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    banners: list = field(default_factory=list)
    clarification: dict | None = None
    message: str | None = None


class BaseAgent:
    name = "BaseAgent"

    def __init__(self, gateway: ToolGateway):
        self._gw = gateway

    def tool(self, ctx: RequestContext, name: str, params: dict) -> dict:
        return self._gw.call(ctx, self.name, name, {k: v for k, v in params.items() if v is not None})

    @staticmethod
    def error(out: dict, extra_limits=()) -> AgentOutcome:
        e = out["error"]
        return AgentOutcome(status=e["code"], message=e["message"], limitations=list(extra_limits))

    @staticmethod
    def caveats(res: dict) -> list:
        return list(res.get("caveats", []))

    @staticmethod
    def period_refs(ctx, call):
        return [S.make_ref(ctx, call, ["period", "basis_label"], "basis"),
                S.make_ref(ctx, call, ["period", "cur_end"], "window_end")]

    def perf_findings(self, ctx, call, rows_idx, label_tpl="{label}"):
        """Standard value / growth / share finding per row (all numbers referenced, none computed)."""
        out = []
        for i in rows_idx[:MAX_FINDINGS]:
            vals = [S.make_value(ctx, call, ["rows", i, "rank_value"], "rank_value", "rank"),
                    S.make_value(ctx, call, ["rows", i, "value_cur"], "value_cur", "value"),
                    S.make_value(ctx, call, ["rows", i, "value_growth_pct"], "value_growth_pct", "growth"),
                    S.make_value(ctx, call, ["rows", i, "value_share_pct"], "value_share_pct", "share")]
            refs = [S.make_ref(ctx, call, ["rows", i, "entity_label"], "label"),
                    S.make_ref(ctx, call, ["rows", i, "entity_key"], "key")] + self.period_refs(ctx, call)
            out.append(S.make_finding(self.name, "#{rank} " + label_tpl + ": value {value}, growth {growth}, "
                                      "share {share} ({basis} to {window_end})", vals, refs))
        return out


class MarketTrendAgent(BaseAgent):
    name = "MarketTrendAgent"

    def performance(self, ctx, p):
        out = self.tool(ctx, "get_market_performance", {"level": p.get("level", "subgroup"), "anchor": p.get("anchor"),
                                                        "basis": p.get("basis"), "top_n": p.get("top_n", 5)})
        if not out["ok"]:
            return self.error(out)
        res = out["result"]
        return AgentOutcome(findings=self.perf_findings(ctx, out["call"], list(range(len(res["rows"])))),
                            limitations=[S.MARKET_DEFINITION_NOTE] + self.caveats(res))

    def trend(self, ctx, p):
        out = self.tool(ctx, "get_market_trends", {"level": p.get("level", "total"), "key": p.get("key", "TOTAL")})
        if not out["ok"]:
            return self.error(out)
        c, rows = out["call"], out["result"]["rows"]
        last = len(rows) - 1
        vals = [S.make_value(ctx, c, ["rows", last, "value_cr"], "value_cr", "month_value"),
                S.make_value(ctx, c, ["rows", last, "value_growth_pct"], "value_growth_pct", "month_growth"),
                S.make_value(ctx, c, ["rows", last, "value_mat"], "value_mat", "mat"),
                S.make_value(ctx, c, ["rows", last, "value_mat_growth_pct"], "value_mat_growth_pct", "mat_growth")]
        refs = [S.make_ref(ctx, c, ["rows", last, "entity_key"], "key"),
                S.make_ref(ctx, c, ["rows", last, "period"], "month")]
        f = S.make_finding(self.name, "{key} in {month}: month value {month_value} (YoY {month_growth}); "
                           "MAT {mat} (MAT growth {mat_growth})", vals, refs)
        return AgentOutcome(findings=[f], limitations=[S.MARKET_DEFINITION_NOTE] + self.caveats(out["result"]))

    def growth_leaders(self, ctx, p):
        """Markets ranked by the TOOL's rank_growth (no re-ranking here)."""
        out = self.tool(ctx, "get_market_performance", {"level": "subgroup", "anchor": p.get("anchor"),
                                                        "basis": p.get("basis"), "top_n": None})
        if not out["ok"]:
            return self.error(out)
        rows = out["result"]["rows"]
        n = p.get("top_n", 5)
        idx = sorted((i for i, r in enumerate(rows) if r["rank_growth"] <= n), key=lambda i: rows[i]["rank_growth"])
        c = out["call"]
        fs = []
        for i in idx:
            vals = [S.make_value(ctx, c, ["rows", i, "rank_growth"], "rank_growth", "rank"),
                    S.make_value(ctx, c, ["rows", i, "value_growth_pct"], "value_growth_pct", "growth"),
                    S.make_value(ctx, c, ["rows", i, "value_cur"], "value_cur", "value")]
            refs = [S.make_ref(ctx, c, ["rows", i, "entity_label"], "label")] + self.period_refs(ctx, c)
            fs.append(S.make_finding(self.name, "Growth rank #{rank}: {label} grew {growth} to {value} "
                                     "({basis} to {window_end})", vals, refs))
        return AgentOutcome(findings=fs, limitations=[S.MARKET_DEFINITION_NOTE,
                                                      "Growth ranks include small markets; see market value."]
                            + self.caveats(out["result"]))


class BrandProductAgent(BaseAgent):
    name = "BrandProductAgent"

    def resolve(self, ctx, name):
        """Return (prod_code, None) or (None, AgentOutcome). NEVER picks one of several candidates."""
        out = self.tool(ctx, "find_products", {"name_contains": name, "limit": 500})
        if not out["ok"]:
            return None, self.error(out)
        rows = out["result"]["rows"]
        exact = [i for i, r in enumerate(rows) if r["brand"].lower() == name.lower()]
        cand = exact or list(range(len(rows)))
        codes = {str(rows[i]["prod_code"]) for i in cand}
        if not codes:
            return None, AgentOutcome(status="ENTITY_NOT_FOUND", message=f'No product matches "{name}".')
        if len(codes) > 1:
            c = out["call"]
            opts = [{"prod_code": str(rows[i]["prod_code"]), "brand": rows[i]["brand"], "company": rows[i]["company"],
                     "label": f'{rows[i]["brand"]} — {rows[i]["company"]} (product code {rows[i]["prod_code"]})',
                     "source": {"call": c, "path": ["rows", i]}} for i in cand[:25]]
            return None, AgentOutcome(
                status=S.AMBIGUOUS_ENTITY, clarification={
                    "message": f'"{name}" matches more than one product (brand names are not unique). '
                               "Please specify the product code:", "options": opts,
                    "truncated": len(cand) > 25})
        return codes.pop(), None

    def performance(self, ctx, p):
        code = p.get("prod_code")
        if code is None:
            code, fail = self.resolve(ctx, p["name"])
            if fail:
                return fail
        scope = {"market_level": p.get("market_level", "total"), "market_key": p.get("market_key", "TOTAL")}
        sh = self.tool(ctx, "get_brand_share", {"prod_code": str(code), **scope, "anchor": p.get("anchor"),
                                                "basis": p.get("basis")})
        if not sh["ok"]:
            return self.error(sh)
        gr = self.tool(ctx, "get_brand_growth", {"prod_code": str(code), **scope})
        if not gr["ok"]:
            return self.error(gr)
        c = sh["call"]
        vals = [S.make_value(ctx, c, ["rows", 0, "value_cur"], "value_cur", "value"),
                S.make_value(ctx, c, ["rows", 0, "value_growth_pct"], "value_growth_pct", "growth"),
                S.make_value(ctx, c, ["rows", 0, "value_share_pct"], "value_share_pct", "share"),
                S.make_value(ctx, c, ["rows", 0, "evolution_index"], "evolution_index", "ei"),
                S.make_value(ctx, c, ["rows", 0, "rank_value"], "rank_value", "rank")]
        refs = [S.make_ref(ctx, c, ["rows", 0, "brand"], "brand"), S.make_ref(ctx, c, ["rows", 0, "company"], "company"),
                S.make_ref(ctx, c, ["rows", 0, "entity_key"], "key"),
                S.make_ref(ctx, c, ["rows", 0, "scope_key"], "scope")] + self.period_refs(ctx, c)
        f1 = S.make_finding(self.name, "{brand} ({company}, product code {key}) in {scope}: value {value}, "
                            "growth {growth}, share {share}, evolution index {ei}, rank {rank} "
                            "({basis} to {window_end})", vals, refs)
        g = gr["call"]
        last = len(gr["result"]["rows"]) - 1
        f2 = S.make_finding(self.name, "Latest month {month}: MAT {mat}, MAT growth {mat_growth}",
                            [S.make_value(ctx, g, ["rows", last, "value_mat"], "value_mat", "mat"),
                             S.make_value(ctx, g, ["rows", last, "value_mat_growth_pct"], "value_mat_growth_pct",
                                          "mat_growth")],
                            [S.make_ref(ctx, g, ["rows", last, "period"], "month")])
        return AgentOutcome(findings=[f1, f2], limitations=self.caveats(sh["result"]))

    def top_products(self, ctx, p):
        out = self.tool(ctx, "get_brand_performance", {k: p.get(k) for k in ("anchor", "basis", "market_level",
                                                                                "market_key")} | {"top_n": p.get("top_n", 5)})
        if not out["ok"]:
            return self.error(out)
        rows = out["result"]["rows"]
        return AgentOutcome(findings=self.perf_findings(ctx, out["call"], list(range(len(rows))),
                                                        "{label} [product code {key}]"),
                            limitations=self.caveats(out["result"]))


class CompanySegmentAgent(BaseAgent):
    name = "CompanySegmentAgent"

    def company(self, ctx, p):
        out = self.tool(ctx, "get_company_performance", {k: p.get(k) for k in ("anchor", "basis", "market_level",
                                                                                  "market_key")} | {"top_n": p.get("top_n", 5)})
        if not out["ok"]:
            return self.error(out)
        return AgentOutcome(findings=self.perf_findings(ctx, out["call"], list(range(len(out["result"]["rows"])))),
                            limitations=self.caveats(out["result"]))

    def therapy(self, ctx, p):
        out = self.tool(ctx, "get_therapy_performance", {k: p.get(k) for k in ("level", "anchor", "basis",
                                                                                  "within_supergroup")} | {"top_n": p.get("top_n", 5)})
        if not out["ok"]:
            return self.error(out)
        return AgentOutcome(findings=self.perf_findings(ctx, out["call"], list(range(len(out["result"]["rows"])))),
                            limitations=[S.MARKET_DEFINITION_NOTE] + self.caveats(out["result"]))

    def segment(self, ctx, p):
        out = self.tool(ctx, "get_segment_analysis", {k: p.get(k) for k in ("segment", "anchor", "basis",
                                                                               "market_level", "market_key")})
        if not out["ok"]:
            return self.error(out)
        return AgentOutcome(findings=self.perf_findings(ctx, out["call"], list(range(len(out["result"]["rows"])))),
                            limitations=self.caveats(out["result"]))


class OpportunityAgent(BaseAgent):
    name = "OpportunityAgent"

    def _limits(self, res):
        m = res["methodology"]
        return [f"{S.OPPORTUNITY_LABEL}: methodology {m['version']} (fingerprint {m['fingerprint']}); "
                "a relative ranking of observed evidence — not a probability, forecast, prediction or guarantee."] \
            + self.caveats(res)

    def scores(self, ctx, p):
        out = self.tool(ctx, "get_opportunity_scores", {k: p.get(k) for k in ("level", "anchor", "basis", "market_level",
                                                                                 "market_key", "company")}
                        | {"top_n": p.get("top_n", 5)})
        if not out["ok"]:
            return self.error(out)
        c, res = out["call"], out["result"]
        fs = []
        for i, r in enumerate(res["rows"][:MAX_FINDINGS]):
            refs = [S.make_ref(ctx, c, ["rows", i, "entity_label"], "label"),
                    S.make_ref(ctx, c, ["rows", i, "score_status"], "status"),
                    S.make_ref(ctx, c, ["rows", i, "methodology_version"], "version")]
            if r["score_status"] != "SCORED":        # insufficient evidence: no score is stated
                refs.append(S.make_ref(ctx, c, ["rows", i, "insufficient_reason"], "reason"))
                fs.append(S.make_finding(self.name, "{label}: {status} ({reason}) — not scored", [], refs))
                continue
            vals = [S.make_value(ctx, c, ["rows", i, "opportunity_rank"], "opportunity_rank", "rank"),
                    S.make_value(ctx, c, ["rows", i, "score"], "score", "score")]
            tpl = "#{rank} {label}: descriptive score {score} ({version})"
            if res["filters"]["level"] == "product":
                vals.append(S.make_value(ctx, c, ["rows", i, "evolution_index"], "evolution_index", "ei"))
                refs.append(S.make_ref(ctx, c, ["rows", i, "matrix_quadrant"], "quadrant"))
                tpl += "; relative momentum (evolution index) {ei}; {quadrant}"
            fs.append(S.make_finding(self.name, tpl, vals, refs))
        if not res["period"]["prior_complete"]:
            return AgentOutcome(status="INSUFFICIENT_EVIDENCE", findings=fs, limitations=self._limits(res),
                                banners=[S.OPPORTUNITY_LABEL], message="No entity can be scored for this period.")
        return AgentOutcome(findings=fs, limitations=self._limits(res), banners=[S.OPPORTUNITY_LABEL])

    def detail(self, ctx, p):
        out = self.tool(ctx, "get_opportunity_detail", {k: p.get(k) for k in ("level", "entity_key", "anchor", "basis")})
        if not out["ok"]:
            return self.error(out)
        c, row = out["call"], out["result"]["rows"][0]
        refs = [S.make_ref(ctx, c, ["rows", 0, "entity_label"], "label"),
                S.make_ref(ctx, c, ["rows", 0, "score_status"], "status"),
                S.make_ref(ctx, c, ["rows", 0, "methodology_version"], "version")]
        if row["score_status"] != "SCORED":
            refs.append(S.make_ref(ctx, c, ["rows", 0, "insufficient_reason"], "reason"))
            return AgentOutcome(status="INSUFFICIENT_EVIDENCE", findings=[
                S.make_finding(self.name, "{label}: {status} ({reason}) — not scored ({version})", [], refs)],
                limitations=self._limits(out["result"]), banners=[S.OPPORTUNITY_LABEL])
        fs = [S.make_finding(self.name, "{label}: descriptive score {score} ({version})",
                             [S.make_value(ctx, c, ["rows", 0, "score"], "score", "score")], refs)]
        for j, _ in enumerate(row["components"]):
            base = ["rows", 0, "components", j]
            fs.append(S.make_finding(self.name, "Component {name}: weight {weight}, contributes {pts}",
                                     [S.make_value(ctx, c, base + ["weight"], "weight", "weight"),
                                      S.make_value(ctx, c, base + ["weighted_contribution"], "weighted_contribution", "pts")],
                                     [S.make_ref(ctx, c, base + ["label"], "name")], kind="component"))
        return AgentOutcome(findings=fs, limitations=self._limits(out["result"]), banners=[S.OPPORTUNITY_LABEL])


class ScenarioAgent(BaseAgent):
    name = "ScenarioAgent"

    def run(self, ctx, p):
        out = self.tool(ctx, "run_scenario", {k: p.get(k) for k in ("scenario_type", "entity_type", "entity_key",
                                                                       "assumptions", "anchor", "basis", "market_level",
                                                                       "market_key")})
        if not out["ok"]:
            return self.error(out, [S.SCENARIO_BANNER])
        c, r = out["call"], out["result"]
        refs = [S.make_ref(ctx, c, ["scenario_type"], "type"), S.make_ref(ctx, c, ["entity", "entity_label"], "label"),
                S.make_ref(ctx, c, ["period", "basis_label"], "basis"), S.make_ref(ctx, c, ["period", "window_end"], "window_end"),
                S.make_ref(ctx, c, ["methodology_version"], "version")]
        assumptions = [S.make_value(ctx, c, ["assumptions", k], "assumption_pct", k)
                       for k in ("price_change_pct", "volume_change_pct", "market_growth_pct", "target_share_pct")
                       if k in r["assumptions"]]
        fs = [S.make_finding(self.name, "{type} for {label} ({basis} to {window_end}, {version}); assumptions: "
                             + ", ".join(f"{a['name']} {{{a['name']}}}" for a in assumptions), assumptions, refs,
                             kind="assumption"),
              S.make_finding(self.name, "OBSERVED baseline value {base}; CALCULATED scenario value {scen}; "
                             "change {chg} ({pct})",
                             [S.make_value(ctx, c, ["baseline", "value_cr"], "value_cr", "base"),
                              S.make_value(ctx, c, ["scenario_result", "value_cr"], "value_cr", "scen"),
                              S.make_value(ctx, c, ["absolute_change", "value_cr"], "value_abs_chg", "chg"),
                              S.make_value(ctx, c, ["percentage_change", "value_pct"], "value_pct", "pct")], [])]
        return AgentOutcome(findings=fs, banners=[S.SCENARIO_BANNER], limitations=list(r["assumptions_and_limitations"]))

    def baseline(self, ctx, p):
        out = self.tool(ctx, "get_scenario_baseline", {k: p.get(k) for k in ("entity_type", "entity_key", "anchor",
                                                                                "basis", "market_level", "market_key")})
        if not out["ok"]:
            return self.error(out)
        c = out["call"]
        return AgentOutcome(findings=[S.make_finding(
            self.name, "OBSERVED baseline for {label}: value {value}, price per pack {price}",
            [S.make_value(ctx, c, ["baseline", "value_cr"], "value_cr", "value"),
             S.make_value(ctx, c, ["baseline", "price_rs_per_pack"], "price_rs_per_pack", "price")],
            [S.make_ref(ctx, c, ["entity", "entity_label"], "label")])],
            limitations=list(out["result"]["assumptions_and_limitations"]))


class DataQualityAgent(BaseAgent):
    """Reports the deterministic data-quality status of the active dataset (Phase 3). Counts only."""
    name = "DataQualityAgent"

    def status(self, ctx, p):
        out = self.tool(ctx, "get_data_quality_status", {})
        if not out["ok"]:
            return self.error(out)
        c, r = out["call"], out["result"]
        fs = [S.make_finding(self.name, "Data-quality status {status}: {first} to {last}, {months} months, {packs} packs; "
                             "geography: {geo}",
                             [S.make_value(ctx, c, ["coverage", "months"], "count", "months"),
                              S.make_value(ctx, c, ["coverage", "packs"], "count", "packs")],
                             [S.make_ref(ctx, c, ["status"], "status"), S.make_ref(ctx, c, ["coverage", "first_month"], "first"),
                              S.make_ref(ctx, c, ["coverage", "last_month"], "last"),
                              S.make_ref(ctx, c, ["coverage", "geography"], "geo")], kind="context")]
        for i, row in enumerate(r["rows"]):
            fs.append(S.make_finding(self.name, "{check}: {state} ({observed} observed)",
                                     [S.make_value(ctx, c, ["rows", i, "observed_count"], "count", "observed")],
                                     [S.make_ref(ctx, c, ["rows", i, "check"], "check"),
                                      S.make_ref(ctx, c, ["rows", i, "status"], "state")], kind="context"))
        return AgentOutcome(findings=fs, limitations=self.caveats(r))
