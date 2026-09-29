"""M10 evaluation runner: executes the dataset, negative controls, tool-failure simulations, repeatability
and latency benchmarks; computes metrics with explicit denominators; writes local reports.

Harness code (plays the user, resolves placeholders, audits provenance). It may use the ToolRegistry
directly; agents never do. Everything runs locally with no network or LLM.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import statistics
import time
from pathlib import Path

from pci_agents import schemas as S
from pci_agents.orchestrator import Orchestrator

from . import controls as CTL
from .cases import CASES, CATEGORIES, EvalCase

REFUSALS = {"UNSAFE_REQUEST", "UNSUPPORTED_GEOGRAPHY", "UNSUPPORTED_CHANNEL", "UNSUPPORTED_SSA_HSA_DSA",
            "UNSUPPORTED_ANALYSIS"}
from pci_data.schema import output_dir  # noqa: E402
# synthetic: evaluation/reports; IMS: <PCI_IMS_DATA_DIR>/reports (IMS-derived reports never enter the repository)
REPORT_DIR = output_dir("reports", Path(__file__).resolve().parents[2] / "evaluation" / "reports")


# ---------------------------------------------------------------- placeholders (harness only)
def resolve_placeholders(registry, orchestrator) -> dict:
    inv = registry.invoke
    # whole-population lookups go to the in-process analytics API (harness only): tool endpoints are row-capped (P1)
    api = registry.api
    rows = api.get_brand_performance(top_n=None)["rows"]
    by_brand = {}
    for r in rows:
        by_brand.setdefault(r["brand"], set()).add(r["entity_key"])
    shared = next(b for b in sorted(by_brand) if len(by_brand[b]) > 1 and len(b) >= 4 and b.isalpha()
                  and len({x["prod_code"] for x in inv("find_products", {"name_contains": b, "limit": 500})
                          ["result"]["rows"] if x["brand"].lower() == b.lower()}) > 1)
    unique = next(b for b in sorted(by_brand, key=lambda b: (-len(b), b)) if len(by_brand[b]) == 1 and b.isalpha()
                  and inv("find_products", {"name_contains": b, "limit": 500})["result"]["row_count"] == 1)
    month = api.get_brand_performance(basis="MONTH", top_n=None)["rows"]
    opp = api.get_opportunity_scores(include_insufficient=True, top_n=None)["rows"]
    safe = lambda s: all(ch not in s for ch in '"\\') and ".." not in s  # noqa: E731
    subgroups = inv("get_market_performance", {"level": "subgroup", "top_n": 50})["result"]["rows"]
    companies = inv("get_company_performance", {"top_n": 50})["result"]["rows"]
    return {
        "@top_product": rows[0]["entity_key"], "@shared_brand": shared, "@shared_brand_lower": shared.lower(),
        "@unique_brand": unique, "@dormant_product": next(r["entity_key"] for r in month if r["units_cur"] == 0),
        "@top_company_cardiac": inv("get_company_performance", {"market_level": "supergroup", "market_key": "CARDIAC",
                                                                "top_n": 1})["result"]["rows"][0]["entity_key"],
        "@top_company": next(r["entity_key"] for r in companies if safe(r["entity_key"])),
        "@top_subgroup": next(r["entity_key"] for r in subgroups if safe(r["entity_key"])),
        "@top_opp_key": next(r["entity_key"] for r in opp if r["score_status"] == "SCORED"),
        "@insufficient_opp_key": next(r["entity_key"] for r in opp if r["score_status"] != "SCORED"),
    }


def _sub(obj, ph):
    if isinstance(obj, str):
        for k in sorted(ph, key=len, reverse=True):
            obj = obj.replace(k, ph[k])
        return obj
    if isinstance(obj, dict):
        return {k: _sub(v, ph) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v, ph) for v in obj]
    return obj


def execute(orch, request, ph) -> tuple[dict, list]:
    """Run a single- or multi-turn request; returns (final response, all turn responses)."""
    if isinstance(request, dict) and "turns" in request:
        turns, prev = [], None
        for t in request["turns"]:
            p = dict(ph)
            if prev is not None:
                tok = prev.get("continuation") or ""
                p["@continuation"] = tok
                p["@tampered_continuation"] = (tok[:-1] + ("0" if tok[-1:] != "0" else "1")) if tok else "x.y"
                opts = (prev.get("clarification") or {}).get("options") or [{}]
                p["@option0"] = str(opts[0].get("prod_code", ""))
            prev = orch.handle(_sub(t, p))
            turns.append(prev)
        return prev, turns
    r = orch.handle(_sub(request, ph))
    return r, [r]


def view(r) -> tuple:
    """User-visible equivalence (raw floats may differ by ~1e-12 parallel-summation noise; displays may not)."""
    return (r["status"], r.get("intent"), tuple(r["route"]), tuple(c["tool"] for c in r["tool_calls"]),
            tuple(f["statement"] for f in r["findings"]), tuple(r["limitations"]), r["final_response"],
            tuple(sorted(c["check"] for c in r["qa"]["checks"])))


# ---------------------------------------------------------------- per-case evaluation
def route_label(status, agents) -> str:
    if status == "AMBIGUOUS_ENTITY":
        return "Clarification"
    if status in REFUSALS:
        return "Refusal"
    if status == "UNRECOGNIZED_REQUEST":
        return "Unrecognized"
    if not agents:
        return "InputRejected"
    return "MultiStep" if len(agents) > 1 else agents[0]


def audit_provenance(registry, r, cache) -> tuple[int, int]:
    """Independent re-execution of each recorded tool call; every numeric claim must equal the field it cites."""
    claims = traced = 0
    rec = {c["call"]: c for c in r["tool_calls"]}
    for f in r["findings"]:
        for v in f["values"]:
            claims += 1
            call = rec.get(v["source"]["call"])
            if not call or call["status"] != "OK":
                continue
            key = (call["tool"], json.dumps(call["input"], sort_keys=True, default=str))
            if key not in cache:
                cache[key] = registry.invoke(call["tool"], call["input"])
            out = cache[key]
            try:
                val = out["result"]
                for p in v["source"]["path"]:
                    val = val[p]
            except (KeyError, IndexError, TypeError):
                continue
            if S.fmt(val, v["metric"]) == v["display"]:
                traced += 1
    return claims, traced


def evaluate_case(case: EvalCase, orch, registry, ph, repeats: int, prov_cache) -> dict:
    t = time.perf_counter()
    r, turns = execute(orch, case.user_request, ph)
    ms = (time.perf_counter() - t) * 1000
    agents = [a for a in r["route"] if a != "InsightQAAgent"]
    called = [c["tool"] for c in r["tool_calls"]]
    chk = {
        "status": r["status"] == case.expected_status,
        "intent": r.get("intent") == case.expected_intent,
        "route": tuple(agents) == case.expected_agent,
        "tools": tuple(called) == case.expected_tools,
        "prohibited": not (set(called) & set(case.prohibited_tools)),
        "qa": r["qa"]["passed"] if case.expected_qa_behavior == "pass" else not r["qa"]["passed"],
    }
    clar = r.get("clarification") or {}
    if case.expected_disambiguation:
        chk["disambiguation"] = (r["status"] == "AMBIGUOUS_ENTITY" and len({o["prod_code"] for o in clar.get("options", [])}) > 1
                                 and not {"get_brand_share", "get_brand_growth"} & set(called) and not r["findings"]
                                 and bool(r.get("continuation")))
    else:
        chk["disambiguation"] = r["status"] != "AMBIGUOUS_ENTITY"
    ev = case.expected_evidence_behavior
    if ev == "findings_with_provenance":
        chk["evidence"] = bool(r["findings"]) and len(r.get("provenance", [])) == sum(len(f["values"]) for f in r["findings"])
    elif ev == "no_tool_calls":
        chk["evidence"] = not r["findings"] and (not case.expected_tools or r["status"] != "OK")
    elif ev == "clarification_only":
        chk["evidence"] = not r["findings"] and bool(clar.get("options"))
    elif ev == "tool_error_passed_through":
        chk["evidence"] = not r["findings"] and any(c["status"] == "ERROR" for c in r["tool_calls"]) and bool(r.get("message"))
    else:  # findings_with_limitation
        chk["evidence"] = bool(r["limitations"])
    text = r.get("final_response", "")
    if case.expected_methodology:
        need = [S.OPPORTUNITY_LABEL, "OPP-"] if case.expected_methodology == "OPP" else [S.SCENARIO_BANNER, "SCN-"]
        chk["methodology"] = all(n in text for n in need)
    if case.expected_disclaimer:
        chk["disclaimer"] = case.expected_disclaimer in text
    if case.expected_error_code:
        chk["error_code"] = case.expected_error_code in {c.get("error_code") for c in r["tool_calls"]} or \
            r["status"] == case.expected_error_code
    if len(turns) > 1:  # multi-turn context checks
        t1 = turns[0]
        chk["turn1_clarified"] = t1["status"] == "AMBIGUOUS_ENTITY" and bool(t1.get("continuation"))
        if case.expected_status == "OK":
            chk["context_preserved"] = (r["request_params"].get("prod_code") in {o["prod_code"] for o in t1["clarification"]["options"]}
                                        and "name" not in r["request_params"] and "find_products" not in called)
    # repeatability
    views = [view(r)] + [view(execute(orch, case.user_request, ph)[0]) for _ in range(repeats - 1)]
    deterministic = all(v == views[0] for v in views)
    claims, traced = audit_provenance(registry, r, prov_cache) if r["status"] in ("OK",) else (0, 0)
    return {"case_id": case.case_id, "category": case.category, "category_name": CATEGORIES[case.category],
            "security_class": case.security_class, "expected_status": case.expected_status, "status": r["status"],
            "expected_label": route_label(case.expected_status, list(case.expected_agent)),
            "actual_label": route_label(r["status"], agents), "tools": called, "checks": chk,
            "passed": all(chk.values()) and deterministic, "deterministic": deterministic,
            "tool_calls_attempted": len(r["tool_calls"]),
            "tool_calls_permitted": sum(c["status"] != "DENIED" for c in r["tool_calls"]),
            "claims": claims, "claims_traced": traced, "latency_ms": round(ms, 1), "timing": r.get("timing"),
            "failed_checks": [k for k, v in chk.items() if not v] + ([] if deterministic else ["determinism"])}


# ---------------------------------------------------------------- controls
def run_controls(registry, ph) -> list[dict]:
    out = []
    for cid, cat, desc, req, build, expected, check in CTL.negative_controls():
        orch, ctx = build(registry)
        with ctx:
            r = orch.handle(_sub(req, ph))
        failed = {f["check"] for f in r["qa"]["failures"]}
        ok = r["status"] == expected and (check is None or check in failed) and not r["findings"]
        out.append({"id": cid, "category": cat, "description": desc, "expected": expected, "status": r["status"],
                    "expected_check": check, "failed_checks": sorted(failed), "blocked": ok})
    return out


def run_tool_failures(registry) -> list[dict]:
    out = []
    for code, req, tool in CTL.TOOL_FAILURES:
        r = Orchestrator(CTL.FailingRegistry(registry, tool, code)).handle(req)
        ok = (r["status"] == code and not r["findings"] and bool(r.get("message")) and r["qa"]["passed"]
              and "status OK" not in r["final_response"])
        out.append({"code": code, "tool": tool, "status": r["status"], "message_present": bool(r.get("message")),
                    "findings": len(r["findings"]), "qa_passed": r["qa"]["passed"], "handled": ok})
    return out


# ---------------------------------------------------------------- latency
WORKFLOWS = [
    ("refusal", {"text": "sales by state"}),
    ("simple market", {"text": "market performance by therapy area MAT top 5"}),
    ("product", {"text": "product @top_product performance"}),
    ("brand (search + 2 tools)", {"text": 'brand performance for "@unique_brand"'}),
    ("opportunity", {"text": "product opportunities top 5"}),
    ("scenario", {"text": 'what if price +5% for therapy area "CARDIAC"'}),
    ("multi-tool workflow", {"text": "which markets are growing and which products have strong relative momentum"}),
    ("clarification (2 turns)", {"turns": [{"text": 'brand performance for "@shared_brand"'},
                                           {"continuation": "@continuation", "select": "@option0"}]}),
]


def run_latency(orch, ph, engine=None, warm_runs=5) -> list[dict]:
    rows = []
    for name, req in WORKFLOWS:
        if engine is not None:           # cold = first run after clearing the Python engine caches
            engine.__dict__.pop("_opportunity_cache", None)
            engine._window_cache.clear()
        t = time.perf_counter()
        r, turns = execute(orch, req, ph)
        cold = (time.perf_counter() - t) * 1000
        warm, tools, qa = [], [], []
        for _ in range(warm_runs):
            t = time.perf_counter()
            r, turns = execute(orch, req, ph)
            warm.append((time.perf_counter() - t) * 1000)
            tools.append(sum((x.get("timing") or {}).get("tools_ms", 0) for x in turns))
            qa.append(sum((x.get("timing") or {}).get("qa_ms", 0) for x in turns))
        rows.append({"workflow": name, "status": r["status"], "cold_ms": round(cold, 1),
                     "warm_median_ms": round(statistics.median(warm), 1), "warm_min_ms": round(min(warm), 1),
                     "warm_max_ms": round(max(warm), 1), "tool_ms_median": round(statistics.median(tools), 1),
                     "qa_ms_median": round(statistics.median(qa), 2), "turns": len(turns)})
    return rows


# ---------------------------------------------------------------- metrics
def _rate(num, den):
    return {"numerator": num, "denominator": den, "rate": (num / den) if den else None}


def _ambiguity(results):
    num = den = 0
    for r in results:
        if r["expected_status"] == "AMBIGUOUS_ENTITY":
            den += 1
            num += r["checks"]["disambiguation"]
        if "turn1_clarified" in r["checks"]:
            den += 1
            num += r["checks"]["turn1_clarified"]
        if r["security_class"] == "forced_selection":
            den += 1
            num += r["passed"]
    return num, den


def compute_metrics(results, controls, failures) -> dict:
    N = len(results)
    valid = [r for r in results if r["expected_status"] == "OK"]
    return {
        "routing_accuracy": _rate(sum(r["checks"]["intent"] and r["checks"]["route"] for r in results), N),
        "tool_selection_accuracy": _rate(sum(r["checks"]["tools"] and r["checks"]["prohibited"] for r in results), N),
        "tool_permission_compliance": _rate(sum(r["tool_calls_permitted"] for r in results),
                                            sum(r["tool_calls_attempted"] for r in results)),
        "qa_pass_rate": _rate(sum(r["checks"]["qa"] for r in results), N),
        "qa_block_rate": _rate(sum(c["blocked"] for c in controls if c["expected"] == "QA_FAILED"),
                               sum(c["expected"] == "QA_FAILED" for c in controls)),
        "numerical_provenance_rate": _rate(sum(r["claims_traced"] for r in results), sum(r["claims"] for r in results)),
        "methodology_preservation_rate": _rate(
            sum(r["checks"].get("methodology", True) and r["checks"].get("disclaimer", True) for r in results
                if "methodology" in r["checks"] or "disclaimer" in r["checks"]),
            sum("methodology" in r["checks"] or "disclaimer" in r["checks"] for r in results)),
        "unsupported_request_handling_rate": _rate(
            sum(r["status"] == r["expected_status"] and not r["tools"] for r in results
                if r["expected_status"].startswith("UNSUPPORTED")),
            sum(r["expected_status"].startswith("UNSUPPORTED") for r in results)),
        # ambiguity events: expected clarifications + first turns of multi-turn cases + refused forced selection
        "ambiguity_handling_rate": _rate(*_ambiguity(results)),
        "injection_resistance_rate": _rate(sum(r["passed"] for r in results if r["security_class"] != "none"),
                                           sum(r["security_class"] != "none" for r in results)),
        "determinism_rate": _rate(sum(r["deterministic"] for r in results), N),
        "end_to_end_success_rate": _rate(sum(r["passed"] for r in valid), len(valid)),
        "overall_case_pass_rate": _rate(sum(r["passed"] for r in results), N),
        "fault_injection_catch_rate": _rate(sum(c["blocked"] for c in controls), len(controls)),
        "tool_failure_handling_rate": _rate(sum(f["handled"] for f in failures), len(failures)),
    }


def confusion(results) -> dict:
    labels = ["MarketTrendAgent", "BrandProductAgent", "CompanySegmentAgent", "OpportunityAgent", "ScenarioAgent",
              "MultiStep", "Clarification", "Refusal", "Unrecognized", "InputRejected"]
    m = {e: {a: 0 for a in labels} for e in labels}
    errors = {"false_route": [], "false_refusal": [], "missed_ambiguity": [], "unsupported_accepted": []}
    agentish = set(labels[:6])
    for r in results:
        e, a = r["expected_label"], r["actual_label"]
        m[e][a] += 1
        if e == a:
            continue
        if e in agentish and a in agentish:
            errors["false_route"].append(r["case_id"])
        elif e in agentish and a in ("Refusal", "Unrecognized", "InputRejected"):
            errors["false_refusal"].append(r["case_id"])
        elif e == "Clarification":
            errors["missed_ambiguity"].append(r["case_id"])
        elif e == "Refusal" and a in agentish:
            errors["unsupported_accepted"].append(r["case_id"])
    return {"labels": labels, "matrix": m, "errors": errors}


# ---------------------------------------------------------------- orchestration + reports
def run_all(registry, orchestrator=None, repeats=3, engine=None, latency=True) -> dict:
    orch = orchestrator or Orchestrator(registry)
    ph = resolve_placeholders(registry, orch)
    # latency first, in a clean process state (measuring after the suite inflated timings ~2-3x through memory held
    # by the provenance-audit cache — found in M10, see docs/EVALUATION_METHODOLOGY.md)
    lat = run_latency(orch, ph, engine) if latency else []
    t = time.perf_counter()
    cache = {}
    results = [evaluate_case(c, orch, registry, ph, repeats, cache) for c in CASES]
    cache.clear()
    controls = run_controls(registry, ph)
    failures = run_tool_failures(registry)
    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "provider": orch.status()["provider"],
            "repeats": repeats, "runtime_s": round(time.perf_counter() - t, 1), "total_cases": len(results),
            "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results),
            "failed_case_ids": [r["case_id"] for r in results if not r["passed"]],
            "metrics": compute_metrics(results, controls, failures), "confusion": confusion(results),
            "cases": results, "negative_controls": controls, "tool_failures": failures, "latency": lat}


def _pct(m):
    return "n/a" if m["rate"] is None else f"{100 * m['rate']:.1f}% ({m['numerator']}/{m['denominator']})"


def markdown(report) -> str:
    L = [f"# Agent evaluation report (M10)", "",
         f"Generated {report['generated_at']} · provider **{report['provider'].get('label', report['provider']['provider'])}** "
         f"· repeats per case: {report['repeats']} · runtime {report['runtime_s']} s", "",
         f"**Cases:** {report['total_cases']} · passed {report['passed']} · failed {report['failed']}"
         + (f" ({', '.join(report['failed_case_ids'])})" if report["failed_case_ids"] else ""), "",
         "## Metrics (numerator / denominator)", "", "| Metric | Result |", "|---|---|"]
    L += [f"| {k.replace('_', ' ')} | {_pct(v)} |" for k, v in report["metrics"].items()]
    c = report["confusion"]
    L += ["", "## Routing error matrix (rows = expected, columns = actual)", "",
          "| expected \\ actual | " + " | ".join(c["labels"]) + " |", "|---" * (len(c["labels"]) + 1) + "|"]
    L += [f"| {e} | " + " | ".join(str(c["matrix"][e][a]) for a in c["labels"]) + " |" for e in c["labels"]]
    L += ["", "Error analysis: " + ", ".join(f"{k} = {len(v)}" + (f" {v}" if v else "") for k, v in c["errors"].items())]
    L += ["", "## Cases by category", "", "| Category | Cases | Passed |", "|---|---|---|"]
    for k, name in CATEGORIES.items():
        rs = [r for r in report["cases"] if r["category"] == k]
        L.append(f"| {k} — {name} | {len(rs)} | {sum(r['passed'] for r in rs)} |")
    L += ["", "## Negative controls (must be blocked / fail safe)", "", "| ID | Type | Fault | Expected | Result | QA check |",
          "|---|---|---|---|---|---|"]
    L += [f"| {x['id']} | {x['category']} | {x['description']} | {x['expected']} | "
          f"{'BLOCKED' if x['blocked'] else 'NOT BLOCKED'} ({x['status']}) | {x['expected_check'] or '—'} |"
          for x in report["negative_controls"]]
    L += ["", "## Tool-failure handling", "", "| Simulated error | Tool | Status returned | Findings | QA | Handled |",
          "|---|---|---|---|---|---|"]
    L += [f"| {f['code']} | {f['tool']} | {f['status']} | {f['findings']} | {'pass' if f['qa_passed'] else 'fail'} | "
          f"{'yes' if f['handled'] else 'NO'} |" for f in report["tool_failures"]]
    if report["latency"]:
        L += ["", "## Latency (ms; this laptop; deterministic mode)", "",
              "| Workflow | Cold | Warm median | Warm min–max | Tools (median) | QA (median) |", "|---|---|---|---|---|---|"]
        L += [f"| {x['workflow']} | {x['cold_ms']} | {x['warm_median_ms']} | {x['warm_min_ms']}–{x['warm_max_ms']} | "
              f"{x['tool_ms_median']} | {x['qa_ms_median']} |" for x in report["latency"]]
    L += ["", "_No IMS figures are included in this report; all values above are evaluation metrics._", ""]
    return "\n".join(L)


def write_reports(report, out_dir: Path = REPORT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "agent_eval_report.json", "csv": out_dir / "agent_eval_cases.csv",
             "md": out_dir / "AGENT_EVAL_REPORT.md"}
    paths["json"].write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["case_id", "category", "security_class", "expected_status", "status", "expected_label",
                "actual_label", "passed", "deterministic", "failed_checks", "tools", "claims", "claims_traced", "latency_ms"])
    for r in report["cases"]:
        w.writerow([r["case_id"], r["category"], r["security_class"], r["expected_status"], r["status"],
                    r["expected_label"], r["actual_label"], r["passed"], r["deterministic"], ";".join(r["failed_checks"]),
                    ";".join(r["tools"]), r["claims"], r["claims_traced"], r["latency_ms"]])
    paths["csv"].write_text(buf.getvalue(), encoding="utf-8")
    paths["md"].write_text(markdown(report), encoding="utf-8")
    return paths
