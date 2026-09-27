"""Insight / QA engine (deterministic; works with LLM_PROVIDER=NONE).

Validates a draft response BEFORE it reaches the user. It never repairs anything: any failed
check turns the response into a structured QA_FAILED result.
"""
from __future__ import annotations

import re

from . import schemas as S
from pci_app.tools import OPP_CONFIG, SCN_VERSION   # published methodology (via the M8 boundary module)

from .gateway import AGENT_TOOLS, INTENT_TOOLS, REQUIRED_RESULT_KEYS

FORBIDDEN_CLAIMS = re.compile(r"\b(forecast\w*|predict\w*|probabilit\w*|guarantee\w*|will\s+(grow|increase|decline|"
                              r"reach)|expected\s+to|likely\s+to|state[- ]level|regional|by\s+region|by\s+state|"
                              r"channel\s+(split|mix)|elasticity)\b", re.I)
# sentences that legitimately contain those words (disclaimers) are removed before the scan
_DISCLAIMER_HINTS = ("not a forecast", "not a probability", "not a forecast, prediction", "is not a forecast",
                     "no forecasting", "not supported", "— not a")
MARKET_TOOLS = {"get_market_performance", "get_market_trends", "get_therapy_performance"}
_PERF = {"entity_key", "entity_label", "value_cur", "value_growth_pct", "value_share_pct", "rank_value"}
ROW_FIELDS = {   # fields each result row must carry (M10 `result_well_formed`)
    "get_market_performance": _PERF, "get_brand_performance": _PERF, "get_brand_share": _PERF,
    "get_company_performance": _PERF, "get_therapy_performance": _PERF, "get_segment_analysis": _PERF,
    "get_market_trends": {"period", "value_cr", "value_mat"}, "get_brand_growth": {"period", "value_cr", "value_mat"},
    "find_products": {"prod_code", "brand", "company"},
    "get_opportunity_scores": {"entity_key", "entity_label", "score_status", "score"},
    "get_opportunity_detail": {"entity_key", "entity_label", "score_status", "score", "components"},
}


def _check(name, passed, detail=""):
    return {"check": name, "passed": bool(passed), "detail": detail}


def _strip_disclaimers(text: str) -> str:
    keep = []
    for line in str(text).splitlines():
        if not any(h in line.lower() for h in _DISCLAIMER_HINTS) and S.SCENARIO_BANNER not in line:
            keep.append(line)
    return "\n".join(keep)


def validate(ctx, response: dict, final_text: str) -> dict:
    checks = []
    results, records = ctx.results, ctx.records
    rec_by_call = {r["call"]: r for r in records}
    findings = response["findings"]

    # 1 + 3. numeric provenance and units
    prov_fail, unit_fail = [], []
    for f in findings:
        for v in f["values"]:
            src = v["source"]
            try:
                actual = S.get_path(results[src["call"]], src["path"])
            except (KeyError, IndexError, TypeError):
                prov_fail.append(f"{v['name']}: source path missing")
                continue
            if actual != v["value"] or S.fmt(actual, v["metric"]) != v["display"]:
                prov_fail.append(f"{v['name']}: value/display does not match tool result")
            if v["unit"] != S.UNIT_OF.get(v["metric"]):
                unit_fail.append(f"{v['name']}: unit {v['unit']!r} is not the defined unit for {v['metric']}")
            if v["unit"] == "INR crore":
                units = results[src["call"]].get("units")
                if isinstance(units, dict) and not any("crore" in str(u).lower() for u in units.values()):
                    unit_fail.append(f"{v['name']}: tool result units do not declare INR crore")
    checks.append(_check("numeric_provenance", not prov_fail, "; ".join(prov_fail)))
    checks.append(_check("unit_consistency", not unit_fail, "; ".join(unit_fail)))

    # 2. every number in the text traces to a tool-sourced value or tool-provided text
    allowed = set()
    for f in findings:
        for v in f["values"]:
            allowed |= S.number_tokens(v["display"])
        for r in f["refs"]:
            allowed |= S.number_tokens(r["value"])
    for x in response["limitations"] + response.get("banners", []) + [response.get("message") or ""]:
        allowed |= S.number_tokens(x)
    if response.get("clarification"):
        for o in response["clarification"]["options"]:
            allowed |= S.number_tokens(o["label"])
        allowed |= S.number_tokens(response["clarification"]["message"])
    allowed |= S.number_tokens(response["intent"] or "")
    for u in S.UNIT_OF.values():            # fixed unit vocabulary, e.g. "0-100", "(100 = in line with market)"
        allowed |= S.number_tokens(u)
    allowed.add("100")                      # scores are rendered as x/100
    stray = sorted(S.number_tokens(final_text) - allowed)
    checks.append(_check("text_numbers_traceable", not stray, f"untraceable numbers: {stray}" if stray else ""))

    # 4 + 5. period / entity / context references match the tool results
    ref_fail = []
    for f in findings:
        for r in f["refs"]:
            try:
                if S.get_path(results[r["source"]["call"]], r["source"]["path"]) != r["value"]:
                    ref_fail.append(r["label"])
            except (KeyError, IndexError, TypeError):
                ref_fail.append(r["label"])
    req = response.get("request_params", {})
    for call, res in results.items():
        per = res.get("period") if isinstance(res, dict) else None
        if per and req.get("anchor") and per.get("anchor") != req["anchor"][:8] + "01":
            ref_fail.append(f"call {call} period anchor differs from request")
        if per and req.get("basis") and per.get("basis") != req["basis"]:
            ref_fail.append(f"call {call} basis differs from request")
    checks.append(_check("period_entity_consistency", not ref_fail, "; ".join(ref_fail)))

    # 6. tool provenance: every referenced call happened, succeeded and was permitted for its agent
    tp_fail = []
    for f in findings:
        for item in f["values"] + f["refs"]:
            rec = rec_by_call.get(item["source"]["call"])
            if not rec or rec["status"] != "OK" or rec["tool"] not in AGENT_TOOLS.get(rec["agent"], ()):
                tp_fail.append(str(item["source"]["call"]))
    if ctx.denied:
        tp_fail.append(f"denied tool attempts: {ctx.denied}")
    checks.append(_check("tool_provenance", not tp_fail, "; ".join(tp_fail)))

    # 7. market definition preserved
    answering = bool(findings)                    # presentation checks apply only when an answer is shown
    used_market = answering and any(r["tool"] in MARKET_TOOLS and r["status"] == "OK" for r in records)
    checks.append(_check("market_definition", not used_market or (S.MARKET_DEFINITION_NOTE in response["limitations"]
                                                                   and S.MARKET_DEFINITION_NOTE in final_text)))

    # 8. unsupported capability / forecast language
    governed = set(response["limitations"]) | set(response.get("banners", []))   # tool/governance text, verbatim
    if response.get("message"):                   # system/tool error messages are not analytical claims (M10)
        governed.add(response["message"])
    authored = "\n".join(ln for ln in str(final_text).splitlines() if ln.strip(" -*\t") not in governed)
    hits = sorted(set(m.group(0) for m in FORBIDDEN_CLAIMS.finditer(_strip_disclaimers(authored))))
    unsupported_ok = True
    if response["status"].startswith("UNSUPPORTED") or response["status"] == S.UNSAFE_REQUEST:
        unsupported_ok = not any(r["status"] == "OK" for r in records)
    checks.append(_check("no_unsupported_claims", not hits and unsupported_ok,
                         f"forbidden wording: {hits}" if hits else ""))

    # 9. insufficient evidence handled: tool said insufficient -> status says so, and no score stated
    ie_fail = []
    if any(r.get("error_code") == "INSUFFICIENT_EVIDENCE" for r in records) and response["status"] != "INSUFFICIENT_EVIDENCE":
        ie_fail.append("tool reported insufficient evidence but response status does not")
    for f in findings:
        if any(r["label"] == "status" and r["value"] == "INSUFFICIENT_EVIDENCE" for r in f["refs"]) and \
                any(v["metric"] == "score" for v in f["values"]):
            ie_fail.append("score stated for an insufficient-evidence entity")
    checks.append(_check("insufficient_evidence_handling", not ie_fail, "; ".join(ie_fail)))

    # 10. scenario disclaimer + OBSERVED/ASSUMED/CALCULATED preserved
    sc = [c for c, r in rec_by_call.items() if r["tool"] == "run_scenario" and r["status"] == "OK"] if answering else []
    sc_ok = True
    for c in sc:
        r = results[c]
        sc_ok &= S.SCENARIO_BANNER in final_text and (r["baseline"]["kind"], r["assumptions"]["kind"],
                                                      r["scenario_result"]["kind"]) == ("OBSERVED", "ASSUMED", "CALCULATED")
    checks.append(_check("scenario_not_forecast", sc_ok))

    # 11. methodology preserved (opportunity version + label; scenario version)
    m_ok = True
    for c, r in rec_by_call.items():
        if r["status"] != "OK" or not answering:
            continue
        if r["tool"] in ("get_opportunity_scores", "get_opportunity_detail"):
            m = results[c].get("methodology") or {}
            m_ok &= (S.OPPORTUNITY_LABEL in final_text and str(m.get("version")) in final_text
                     and str(m.get("fingerprint")) in final_text)
        if r["tool"] == "run_scenario":
            m_ok &= str(results[c].get("methodology_version")) in final_text
    checks.append(_check("methodology_preserved", m_ok))

    # 12. brand ambiguity handled (never silently chose one of several products)
    amb_fail = False
    for c, r in rec_by_call.items():
        if r["tool"] == "find_products" and r["status"] == "OK":
            name = r["input"]["name_contains"].lower()
            rows = results[c]["rows"]
            exact = [x for x in rows if x["brand"].lower() == name] or rows
            if len({str(x["prod_code"]) for x in exact}) > 1:
                later = [x for x in records if x["call"] > c and x["tool"] in ("get_brand_share", "get_brand_growth")]
                amb_fail = bool(later) or response["status"] != S.AMBIGUOUS_ENTITY
    checks.append(_check("disambiguation", not amb_fail))

    # ---------------- M10 reliability checks ----------------
    ok_calls = [(c, r) for c, r in rec_by_call.items() if r["status"] == "OK"]

    # 13. tool scope: every attempted tool must belong to the intent's workflow (permitted-but-unexpected fails)
    allowed_tools = INTENT_TOOLS.get(response.get("intent"), set())
    out_of_scope = sorted({r["tool"] for r in records if r["status"] != "DENIED" and r["tool"] not in allowed_tools})
    checks.append(_check("tool_scope", not out_of_scope, f"unexpected tools: {out_of_scope}" if out_of_scope else ""))

    # 14. requested entity: results describe the entity that was asked for (or the single resolved product)
    req = response.get("request_params") or {}
    ent_fail = []
    resolved = {str(req["prod_code"])} if req.get("prod_code") else None
    for c, r in ok_calls:
        res = results[c]
        try:
            if r["tool"] == "find_products":
                rows = res["rows"]
                exact = [x for x in rows if x["brand"].lower() == str(req.get("name", "")).lower()] or rows
                resolved = {str(x["prod_code"]) for x in exact}
            elif r["tool"] in ("get_brand_share", "get_brand_growth") and resolved:
                keys = {str(x["entity_key"]) for x in res["rows"]}
                if len(resolved) != 1 or keys != resolved:
                    ent_fail.append(f"{r['tool']} returned {sorted(keys)[:3]} for requested product")
            elif r["tool"] == "get_market_trends" and req.get("key"):
                if {str(x["entity_key"]) for x in res["rows"]} != {str(req["key"])}:
                    ent_fail.append("trend returned a different market")
            elif r["tool"] in ("run_scenario", "get_scenario_baseline") and req.get("entity_key"):
                if str(res["entity"]["entity_key"]) != str(req["entity_key"]):
                    ent_fail.append("scenario baseline is for a different entity")
            elif r["tool"] == "get_opportunity_detail" and req.get("entity_key"):
                if str(res["rows"][0]["entity_key"]) != str(req["entity_key"]):
                    ent_fail.append("opportunity detail is for a different entity")
        except (KeyError, IndexError, TypeError):
            ent_fail.append(f"{r['tool']}: entity fields missing")
    checks.append(_check("requested_entity", not ent_fail, "; ".join(ent_fail)))

    # 15. evidence completeness: every successful result carries its evidence envelope
    missing = [f"{r['tool']}: {sorted(REQUIRED_RESULT_KEYS[r['tool']] - set(results[c]))}"
               for c, r in ok_calls if r["tool"] in REQUIRED_RESULT_KEYS
               and not REQUIRED_RESULT_KEYS[r["tool"]] <= set(results[c])]
    checks.append(_check("evidence_complete", not missing, "; ".join(missing)))

    # 15b. result well-formed: row collections are lists of records carrying the fields agents rely on
    shape_fail = []
    for c, r in ok_calls:
        need = ROW_FIELDS.get(r["tool"])
        rows = results[c].get("rows") if isinstance(results[c], dict) else None
        if need is None:
            continue
        if not isinstance(rows, list) or not all(isinstance(x, dict) and need <= set(x) for x in rows):
            shape_fail.append(r["tool"])
    checks.append(_check("result_well_formed", not shape_fail, f"malformed results: {shape_fail}" if shape_fail else ""))

    # 16. published methodology: versions/fingerprint equal the published ones; scenario disclaimer intact
    pm_fail = []
    for c, r in ok_calls:
        res = results[c]
        if r["tool"] in ("get_opportunity_scores", "get_opportunity_detail"):
            m = res.get("methodology") or {}
            if (m.get("version"), m.get("fingerprint")) != (OPP_CONFIG.version, OPP_CONFIG.fingerprint()):
                pm_fail.append("opportunity methodology differs from the published version")
        if r["tool"] == "run_scenario":
            if res.get("methodology_version") != SCN_VERSION:
                pm_fail.append("scenario methodology differs from the published version")
            if not any("not a forecast" in str(x).lower() for x in res.get("assumptions_and_limitations", [])):
                pm_fail.append("scenario result lacks the not-a-forecast disclaimer")
    checks.append(_check("published_methodology", not pm_fail, "; ".join(pm_fail)))

    failures = [c for c in checks if not c["passed"]]
    return {"passed": not failures, "checks": checks, "failures": failures, "engine": "deterministic QA (no LLM)"}
