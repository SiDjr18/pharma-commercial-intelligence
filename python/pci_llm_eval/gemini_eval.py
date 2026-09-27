"""Phase 2 runner: validate the agent contracts with Gemini on SYNTHETIC data only.

    set PCI_DATASET=synthetic
    python -m pci_llm_eval.gemini_eval --dry-run                 # offline: declarations + local tool execution + bundle
    set GEMINI_API_KEY=...   (never commit it; .env is git-ignored)
    python -m pci_llm_eval.gemini_eval --live [--model gemini-2.5-flash]

Live mode: the model receives the system prompt, the function declarations and ONE synthetic question; every
function call is executed LOCALLY and only its structured result is returned to the model. The final JSON
answer is checked by local QA (contract, routing, refusal, numeric grounding). No private data can be sent:
the runner exits unless PCI_DATASET=synthetic and the active manifest says dataset=synthetic.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import contracts as C

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
REPORTS = ROOT / "evaluation" / "reports"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANALYTICS_TOOLS = {t["name"] for t in C.tool_contracts()} - {"get_geography_performance", "get_application_metadata",
                                                              "get_data_quality_status"}


def require_synthetic() -> None:
    from pci_data.schema import DATASET, MANIFEST_PATH
    m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {}
    if DATASET != "synthetic" or m.get("dataset") != "synthetic":
        sys.exit("REFUSED: set PCI_DATASET=synthetic and generate data/synthetic first (python -m pci_synthetic.generate). "
                 "External model calls are allowed on the synthetic dataset only.")


def tokens(tools: C.LocalTools) -> dict:
    api = tools.api
    top = api.get_brand_performance("2024-05-01", "MAT", top_n=1)["rows"][0]
    own_sub = api.con.execute("SELECT subgroup FROM pack WHERE CAST(prod_code AS VARCHAR) = ? GROUP BY 1 "
                              "ORDER BY count(*) DESC, subgroup LIMIT 1", [top["entity_key"]]).fetchone()[0]
    return {"@TOP_PRODUCT_SUBGROUP": own_sub, "@TOP_PRODUCT": top["entity_key"], "@TOP_BRAND": top["brand"],
            "@TOP_SUBGROUP": api.get_market_performance("subgroup", "2024-05-01", "MAT", top_n=1)["rows"][0]["entity_key"],
            "@TOP_COMPANY": api.get_company_performance("2024-05-01", "MAT", top_n=1)["rows"][0]["entity_key"]}


def _sub(obj, tok):
    if isinstance(obj, str):
        for k, v in tok.items():
            obj = obj.replace(k, str(v))
        return obj
    if isinstance(obj, dict):
        return {k: _sub(v, tok) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v, tok) for v in obj]
    return obj


def load_cases(tok) -> list[dict]:
    return [_sub(c, tok) for c in json.loads((HERE / "eval_cases.json").read_text(encoding="utf-8"))["cases"]]


# ------------------------------------------------------------------------------------------ local QA
_NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    return [m.group(0) for m in _NUM.finditer(text or "")]


def _collect(obj, out: set):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for n in numbers_in(obj):
            try:
                out.add(float(n.replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)


def grounded(answer: str, question: str, results: list) -> list[str]:
    """Numbers in the answer that are neither in a tool result (at the answer's precision) nor in the question."""
    pool: set = set()
    _collect(results, pool)
    _collect(question, pool)
    bad = []
    for s in numbers_in(answer):
        v = float(s.replace(",", ""))
        dec = len(s.split(".")[1]) if "." in s else 0
        if not any(abs(round(p, dec) - v) <= 0.5 * 10 ** -dec + 1e-12 for p in pool) and v not in (0.0,):
            bad.append(s)
    return bad


def qa(case: dict, out: dict | None, calls: list[dict]) -> dict:
    checks = {}
    checks["contract"] = isinstance(out, dict) and all(k in out for k in C.OUTPUT_CONTRACT["required"])
    called = [c["tool"] for c in calls]
    if case.get("expect_refusal"):
        checks["refused"] = bool(out) and out.get("verification_status") == "REFUSED"
        checks["no_analytics_tool"] = not (set(called) & ANALYTICS_TOOLS)
    else:
        if case.get("expected_tools"):
            checks["expected_tool_called"] = bool(set(called) & set(case["expected_tools"]))
        if case.get("expected_agent") and out:
            checks["routing"] = out.get("selected_agent") == case["expected_agent"]
        if case.get("expect_error_or_null"):
            checks["insufficient_reported"] = any((not c["result"].get("ok")) or "null" in json.dumps(c["result"])
                                                  or '"growth_pct": null' in json.dumps(c["result"]) for c in calls)
    checks["numbers_grounded"] = bool(out) and not grounded(out.get("answer", ""), case["question"], [c["result"] for c in calls])
    return {"checks": checks, "passed": all(checks.values())}


# ------------------------------------------------------------------------------------------ dry run
def dry_run(out_dir: Path) -> dict:
    tools = C.LocalTools()
    tok = tokens(tools)
    decls = C.function_declarations()
    names = [d["name"] for d in decls]
    problems = []
    if len(names) != len(set(names)):
        problems.append("duplicate declaration names")
    for agent, ts in C.AGENTS.items():
        for t in ts:
            if t not in names:
                problems.append(f"{agent}: tool {t} not declared")
    if "additionalProperties" in json.dumps(decls):
        problems.append("unsupported JSON-schema keyword left in declarations")
    results = []
    for case in load_cases(tok):
        for name, args in case.get("dry_run", []):
            r = tools.call(name, args)
            code = None if r.get("ok") else (r.get("error") or {}).get("code")
            ok = r.get("ok", False)
            expected_fail = case.get("expect_refusal") or case.get("expect_error_or_null")
            growth_null = '"value_growth_pct": null' in json.dumps(r)
            passed = ok if not expected_fail else ((not ok) or growth_null)
            results.append({"case": case["id"], "tool": name, "ok": ok, "error": code, "passed": passed})
    paths = C.write_bundle(out_dir)
    summary = {"mode": "dry-run", "declarations": len(decls), "cases": len(load_cases(tok)),
               "dry_run_calls": len(results), "dry_run_passed": sum(r["passed"] for r in results),
               "contract_problems": problems, "bundle": [str(p) for p in paths]}
    return {"summary": summary, "calls": results}


# ------------------------------------------------------------------------------------------ live
# Transient failures only (429 rate limit, 503 overload): bounded retries with jittered exponential backoff.
# Per-day quota 429s and every other HTTP error (400/401/403/404...) fail at once with Google's code and message.
RETRY_CODES = {429, 503}
MAX_RETRIES = 5                 # per request
BACKOFF_BASE_S, BACKOFF_CAP_S = 2.0, 60.0
MAX_WAIT_PER_REQUEST_S = 240.0
MAX_WAIT_PER_RUN_S = 1800.0
CASE_DELAY_S = 4.0              # sequential cases, never back-to-back


class GeminiAPIError(Exception):
    """Final HTTP error (not retryable, or retries exhausted) carrying Google's code, status and message."""

    def __init__(self, http_code: int, status: str, message: str, attempts: int):
        super().__init__(f"HTTP {http_code} {status}: {message}")
        self.info = {"http_code": http_code, "status": status, "message": message, "attempts": attempts}


def new_stats() -> dict:
    return {"http_429": 0, "http_503": 0, "retries": 0, "backoff_s": 0.0}


def _google_error(e: urllib.error.HTTPError, key: str) -> tuple[str, str, float | None, bool]:
    """(status, message, server retry hint in s, per-day quota?) from Google's error body; the key is redacted."""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    try:
        err = json.loads(body).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    details = err.get("details") or []
    msg = str(err.get("message") or body or e.reason)
    if key:
        msg = msg.replace(key, "[REDACTED]")
    hint = None
    try:
        hint = float(e.headers.get("Retry-After")) if e.headers and e.headers.get("Retry-After") else None
    except ValueError:
        pass
    for d in details:
        if str(d.get("retryDelay", "")).endswith("s"):
            try:
                hint = max(hint or 0.0, float(d["retryDelay"][:-1]))
            except ValueError:
                pass
    per_day = any("perday" in str(v.get("quotaId", "")).lower() for d in details for v in d.get("violations") or [])
    return str(err.get("status") or e.reason), msg[:2000], hint, per_day


def _post(model: str, key: str, body: dict, stats: dict | None = None, sleep=time.sleep) -> dict:
    stats = new_stats() if stats is None else stats
    waited = 0.0
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(API.format(model=model), json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            status, msg, hint, per_day = _google_error(e, key)
            if e.code in RETRY_CODES:
                stats[f"http_{e.code}"] += 1
            step = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** attempt)
            delay = min(BACKOFF_CAP_S, max(hint or 0.0, step / 2 + random.uniform(0, step / 2)))
            if (e.code not in RETRY_CODES or per_day or attempt == MAX_RETRIES
                    or waited + delay > MAX_WAIT_PER_REQUEST_S or stats["backoff_s"] + delay > MAX_WAIT_PER_RUN_S):
                raise GeminiAPIError(e.code, status, msg, attempt + 1) from None
            stats["retries"] += 1
            stats["backoff_s"] = round(stats["backoff_s"] + delay, 2)
            waited += delay
            sleep(delay)
    raise AssertionError("unreachable")


def _parse_json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def live(model: str, key: str) -> dict:
    tools = C.LocalTools()
    tok = tokens(tools)
    system = (HERE / "system_prompt.md").read_text(encoding="utf-8")
    decls = C.function_declarations()
    cases = load_cases(tok)
    report, stats, t_run = [], new_stats(), time.time()
    for i, case in enumerate(cases):
        if i:
            time.sleep(CASE_DELAY_S)
        contents = [{"role": "user", "parts": [{"text": case["question"]}]}]
        calls, out, err, t0, r0 = [], None, None, time.time(), stats["retries"]
        try:
            for _ in range(6):
                resp = _post(model, key, {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                                          "tools": [{"function_declarations": decls}],
                                          "generationConfig": {"temperature": 0}}, stats)
                parts = resp["candidates"][0]["content"]["parts"]
                contents.append({"role": "model", "parts": parts})
                fcs = [p["functionCall"] for p in parts if "functionCall" in p]
                if not fcs:
                    out = _parse_json("".join(p.get("text", "") for p in parts))
                    break
                replies = []
                for fc in fcs:
                    r = tools.call(fc["name"], fc.get("args") or {})
                    calls.append({"tool": fc["name"], "args": fc.get("args") or {}, "result": r})
                    replies.append({"functionResponse": {"name": fc["name"], "response": {"content": r}}})
                contents.append({"role": "user", "parts": replies})
        except GeminiAPIError as e:
            err = e.info
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as e:
            err = str(e)[:300]
        verdict = qa(case, out, calls) if err is None else {"checks": {"api_error": False}, "passed": False}
        if out is not None:
            out["verification_status"] = "VERIFIED" if verdict["passed"] and out.get("verification_status") != "REFUSED" \
                else out.get("verification_status") if verdict["passed"] else "QA_FAILED"
        report.append({"case": case["id"], "category": case["category"], "question": case["question"],
                       "tools_called": [c["tool"] for c in calls], "output": out, "qa": verdict, "error": err,
                       "seconds": round(time.time() - t0, 2), "api_retries": stats["retries"] - r0})
    n = len(report)
    return {"summary": {"mode": "live", "model": model, "cases": n, "passed": sum(r["qa"]["passed"] for r in report),
                        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
                        "transport": {**stats, "api_errors": sum(r["error"] is not None for r in report),
                                      "runtime_s": round(time.time() - t_run, 1), "case_delay_s": CASE_DELAY_S,
                                      "max_retries": MAX_RETRIES}}, "cases": report}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    ap.add_argument("--bundle", type=Path, default=REPORTS / "gemini_bundle")
    a = ap.parse_args(argv)
    require_synthetic()
    if a.dry_run:
        res = dry_run(a.bundle)
    else:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            sys.exit("GEMINI_API_KEY is not set (keep it in .env / the environment; never in code, URLs or git).")
        res = live(a.model, key)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"gemini_{'dryrun' if a.dry_run else 'live'}.json"
    out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res["summary"], indent=2))
    return res


if __name__ == "__main__":
    main()
