"""M8: tool API contracts, structured errors, security/privacy controls, HTTP end-to-end, UI integration."""
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pci_analytics import CommercialAnalytics
from pci_analytics.opportunity_config import DEFAULT_CONFIG as OPP
from pci_analytics.scenario import DISCLAIMER, METHODOLOGY_VERSION as SCN
from pci_app import tools as T
from pci_app.server import SECURITY_HEADERS, WEB_DIR, create_server
from pci_data.schema import SOURCE_PATH

ROOT = Path(__file__).resolve().parents[1]
PATH_RX = re.compile(r"[A-Za-z]:[\\/]|/(?:e|c|d|home|users|mnt)/|\.parquet|\.xlsx|\.duckdb|" + re.escape(SOURCE_PATH.stem), re.I)
RAW_KEYS = {"pfc", "pack_desc", "source_row", "pack_launch_yyyymm", "index_desc_raw", "PFC", "PACK_DESC"}


@pytest.fixture(scope="module")
def reg(con, py_engine):
    api = CommercialAnalytics(con)
    api._opp_engine = py_engine
    return T.ToolRegistry(api)


def _keys(o, acc=None):
    acc = set() if acc is None else acc
    if isinstance(o, dict):
        for k, v in o.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(o, list):
        for v in o:
            _keys(v, acc)
    return acc


def _assert_safe(out):
    text = json.dumps(out, default=str)
    assert not PATH_RX.search(text), PATH_RX.search(text).group(0)
    assert not (_keys(out) & RAW_KEYS)
    assert "Traceback" not in text


# ================================================================== A. contracts
def test_contracts_cover_all_tools_and_api_methods():
    c = T.contracts()
    json.dumps(c)
    names = [t["name"] for t in c["tools"]]
    assert names == list(T.TOOLS) and len(names) == 15              # Phase 3: + get_data_quality_status
    for t in c["tools"]:
        s = t["input_schema"]
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert set(s["required"]) <= set(s["properties"])
        assert t["purpose"] and "output" in t and "errors" in t
    api_methods = {m for m in dir(CommercialAnalytics) if not m.startswith("_")}
    for n, t in T.TOOLS.items():
        if t["method"]:
            assert t["method"] in api_methods, n
    # every public analytical API function is exposed as a tool
    assert {t["method"] for t in T.TOOLS.values() if t["method"]} == api_methods


def test_contract_enums_match_engine_definitions():
    from pci_analytics.api import MARKET_LEVELS, SEGMENTS
    from pci_analytics.scenario import SCENARIO_TYPES
    p = T.TOOLS["get_market_performance"]["input_schema"]["properties"]
    assert p["level"]["enum"] == list(MARKET_LEVELS)
    assert T.TOOLS["get_segment_analysis"]["input_schema"]["properties"]["segment"]["enum"] == list(SEGMENTS)
    assert T.TOOLS["run_scenario"]["input_schema"]["properties"]["scenario_type"]["enum"] == list(SCENARIO_TYPES)


# ================================================================== B-G. valid requests (engine outputs unchanged)
def test_market_request_equals_engine(reg):
    out = reg.invoke("get_market_performance", {"level": "supergroup", "anchor": "2024-05-01", "basis": "MAT", "top_n": 5})
    assert out["ok"] and out["result"]["row_count"] == 5
    direct = reg.api.get_market_performance("supergroup", "2024-05-01", "MAT", top_n=5)
    assert [r["entity_key"] for r in out["result"]["rows"]] == [r["entity_key"] for r in direct["rows"]]
    assert out["result"]["rows"][0]["value_cur"] == pytest.approx(direct["rows"][0]["value_cur"], rel=1e-12)
    assert out["result"]["caveats"] == direct["caveats"] and "evidence" in out["result"]
    _assert_safe(out)


@pytest.mark.parametrize("name,params", [
    ("get_application_metadata", {}),
    ("find_products", {"name_contains": "cal", "limit": 5}),
    ("get_market_trends", {"level": "total", "key": "TOTAL"}),
    ("get_brand_performance", {"basis": "YTD", "top_n": 5}),
    ("get_company_performance", {"top_n": 5}),
    pytest.param(*("get_therapy_performance", {"level": "subgroup", "within_supergroup": "CARDIAC", "top_n": 5}), marks=pytest.mark.ims),
    ("get_segment_analysis", {"segment": "dosage_form", "basis": "MONTH"}),
    ("get_opportunity_scores", {"level": "market", "top_n": 5}),
    pytest.param(*("get_scenario_baseline", {"entity_type": "supergroup", "entity_key": "CARDIAC"}), marks=pytest.mark.ims),
])
def test_valid_requests(reg, name, params):
    out = reg.invoke(name, params)
    assert out["ok"], out
    _assert_safe(out)


@pytest.mark.ims
def test_product_request_chain(reg):
    p = reg.invoke("find_products", {"name_contains": "cal", "limit": 1})["result"]["rows"][0]
    code = str(p["prod_code"])
    share = reg.invoke("get_brand_share", {"prod_code": code, "market_level": "total", "market_key": "TOTAL"})
    growth = reg.invoke("get_brand_growth", {"prod_code": code})
    assert share["ok"] and growth["ok"] and share["result"]["rows"][0]["entity_key"] == code
    assert len(growth["result"]["rows"]) == 36


def test_opportunity_preserves_m6_methodology(reg):
    out = reg.invoke("get_opportunity_scores", {"level": "product", "top_n": 3})
    m = out["result"]["methodology"]
    assert (m["version"], m["fingerprint"]) == (OPP.version, OPP.fingerprint())
    assert all(r["methodology_version"] == OPP.version for r in out["result"]["rows"])
    assert any("not a forecast" in c for c in out["result"]["caveats"])
    d = reg.invoke("get_opportunity_detail", {"level": "product", "entity_key": out["result"]["rows"][0]["entity_key"]})
    assert d["ok"] and d["result"]["rows"][0]["score"] == out["result"]["rows"][0]["score"]


@pytest.mark.ims
def test_scenario_preserves_m7_methodology(reg):
    out = reg.invoke("run_scenario", {"scenario_type": "PRICE_CHANGE", "entity_type": "supergroup", "entity_key": "CARDIAC",
                                      "assumptions": {"price_change_pct": 5}})
    r = out["result"]
    assert out["ok"] and r["methodology_version"] == SCN and r["assumptions_and_limitations"][0] == DISCLAIMER
    assert (r["baseline"]["kind"], r["assumptions"]["kind"], r["scenario_result"]["kind"]) == ("OBSERVED", "ASSUMED", "CALCULATED")
    assert r["percentage_change"]["value_pct"] == pytest.approx(5.0)
    direct = reg.api.run_scenario("PRICE_CHANGE", "supergroup", "CARDIAC", {"price_change_pct": 5})
    assert r["scenario_id"] == direct["scenario_id"]


# ================================================================== H-J. structured errors
@pytest.mark.parametrize("name,params,code", [
    ("get_market_performance", {"level": "geography"}, "UNSUPPORTED_GEOGRAPHY"),
    ("get_market_performance", {"level": "state"}, "UNSUPPORTED_GEOGRAPHY"),
    ("get_segment_analysis", {"segment": "channel"}, "UNSUPPORTED_CHANNEL"),
    ("get_segment_analysis", {"segment": "ssa"}, "UNSUPPORTED_SSA_HSA_DSA"),
    ("run_scenario", {"scenario_type": "FORECAST", "entity_type": "total", "entity_key": "TOTAL", "assumptions": {}},
     "UNSUPPORTED_ANALYSIS"),
    ("run_scenario", {"scenario_type": "PRICE_ELASTICITY", "entity_type": "total", "entity_key": "TOTAL",
                      "assumptions": {}}, "UNSUPPORTED_ANALYSIS"),
    ("get_market_performance", {"level": "company"}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "basis": "FY"}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "anchor": "2024-13-01"}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "anchor": 20240501}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "top_n": True}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "top_n": 0}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "sql": "select 1"}, "INVALID_INPUT"),
    ("get_market_performance", {"level": "subgroup", "anchor": "2025-01-01"}, "INVALID_PERIOD"),
    ("get_market_performance", {"level": "subgroup", "anchor": "2022-01-01"}, "INVALID_PERIOD"),
    ("get_brand_share", {"prod_code": "1"}, "MISSING_PARAMETER"),
    ("get_market_trends", {"level": "subgroup", "key": "NO SUCH MARKET"}, "ENTITY_NOT_FOUND"),
    ("run_scenario", {"scenario_type": "MARKET_SHARE", "entity_type": "company", "entity_key": "X",
                      "assumptions": {"target_share_pct": 150}, "market_level": "total"}, "INVALID_SCENARIO"),
    ("run_scenario", {"scenario_type": "PRICE_CHANGE", "entity_type": "total", "entity_key": "TOTAL",
                      "assumptions": {"price_change_pct": -150}}, "INVALID_SCENARIO"),
    ("run_scenario", {"scenario_type": "PRICE_CHANGE", "entity_type": "total", "entity_key": "TOTAL",
                      "assumptions": {"elasticity": -1.2}}, "INVALID_INPUT"),
    ("get_opportunity_scores", {"basis": "MONTH"}, "INVALID_INPUT"),
    ("get_opportunity_scores", {"market_level": "geography", "market_key": "X"}, "UNSUPPORTED_GEOGRAPHY"),
    ("execute_sql", {"query": "select * from pack"}, "UNKNOWN_TOOL"),
    ("__import__", {}, "UNKNOWN_TOOL"),
])
def test_structured_errors(reg, name, params, code):
    out = reg.invoke(name, params)
    assert out["ok"] is False and out["error"]["code"] == code, out
    assert set(out["error"]) == {"code", "category", "message", "engine_code"}
    _assert_safe(out)


def test_insufficient_evidence_error(reg, py_engine):
    from pci_analytics import metrics
    import datetime as dt
    dead = next(r["entity_key"] for r in metrics.entity_period(py_engine, "product", dt.date(2024, 5, 1), "MONTH")
                if r["units_cur"] == 0)
    out = reg.invoke("run_scenario", {"scenario_type": "PRICE_CHANGE", "entity_type": "product", "entity_key": dead,
                                      "assumptions": {"price_change_pct": 5}, "basis": "MONTH"})
    assert out["error"]["code"] == "INSUFFICIENT_EVIDENCE" and out["error"]["engine_code"] == "insufficient_baseline"


def test_internal_error_is_sanitised(reg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(r"secret failure at X:\project\data\processed\pack.parquet")
    monkeypatch.setattr(reg.api, "get_market_performance", boom)
    out = reg.invoke("get_market_performance", {"level": "total"})
    assert out["error"]["code"] == "INTERNAL_ERROR" and "secret" not in out["error"]["message"]
    _assert_safe(out)


def test_path_scrubbing():
    assert T.scrub(r"see E:\x\y.parquet and /e/Pharma/z") == "see [redacted] and [redacted]"
    assert T._scrub_obj({"a": ["C:/Users/x", 1.5, float("nan")]}) == {"a": ["[redacted]", 1.5, None]}


# ================================================================== K-N. security / privacy
@pytest.mark.ims
def test_no_sql_injection_or_execution(reg, con):
    payloads = ["'; DROP TABLE pack; --", "x' OR '1'='1", "subgroup; SELECT * FROM fact_pack_month"]
    for p in payloads:
        out = reg.invoke("get_market_trends", {"level": "subgroup", "key": p})
        assert out["error"]["code"] == "ENTITY_NOT_FOUND"
        out = reg.invoke("find_products", {"name_contains": p})
        assert out["ok"] and out["result"]["row_count"] == 0
    assert con.execute("SELECT count(*) FROM pack").fetchone()[0] == 105_317


def test_tool_layer_has_no_generic_execution_surface():
    src = (ROOT / "python" / "pci_app" / "tools.py").read_text(encoding="utf-8") + \
          (ROOT / "python" / "pci_app" / "server.py").read_text(encoding="utf-8")
    for bad in ("eval(", "exec(", "subprocess", "os.system", "con.execute(params", "open(params", "__import__("):
        assert bad not in src, bad
    assert all(n.startswith(("get_", "find_", "run_")) for n in T.TOOLS)


def test_no_external_network_or_ai_dependency():
    for f in (ROOT / "python" / "pci_app").glob("*.py"):
        s = f.read_text(encoding="utf-8")
        for bad in ("import requests", "urllib.request", "http.client", "import socket", "httpx", "aiohttp",
                    "openai", "anthropic", "gemini", "googleapis"):
            assert bad not in s.lower(), (f.name, bad)
        assert not re.search(r"https?://(?!127\.0\.0\.1)", s), f.name
    for f in WEB_DIR.glob("*"):
        s = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://", s), f.name                 # UI loads nothing from outside
        assert "@import" not in s and "cdn" not in s.lower()
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert set(re.findall(r"fetch\(\s*[`'\"]([^`'\"$]+)", js)) <= {"/api/tools/", "/api/tools"}


# ================================================================== O. HTTP end-to-end + UI integration
@pytest.fixture(scope="module")
def server(reg):
    srv = create_server(reg.api, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _req(url, body=None, method=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_ui_and_headers(server):
    s, h, b = _req(server + "/")
    assert s == 200 and b"Pharma Commercial Intelligence" in b and b"NATURAL LANGUAGE ANALYTICS" in b
    assert h["Content-Security-Policy"] == SECURITY_HEADERS["Content-Security-Policy"]
    for path in ("/static/app.js", "/static/style.css", "/api/health", "/api/tools"):
        assert _req(server + path)[0] == 200, path


@pytest.mark.parametrize("path", ["/static/../run_app.py", "/static/..%2f..%2frun_app.py", "/data/processed/pack.parquet",
                                  "/api/sql", "/api/eval", "/etc/passwd", "/static/../../private_workbook.xlsx"])
def test_http_no_file_or_code_access(server, path):
    s, _, b = _req(server + path.replace(" ", "%20").replace("'", "%27"))
    assert s == 404 and b"NOT_FOUND" in b


def test_http_rejects_other_methods_and_bad_bodies(server):
    assert _req(server + "/api/tools/get_market_performance", b"{}", "PUT")[0] == 404
    s, _, b = _req(server + "/api/tools/get_market_performance", b"not json")
    assert s == 400 and json.loads(b)["error"]["code"] == "INVALID_INPUT"
    s, _, b = _req(server + "/api/tools/get_market_performance", b"[1,2]")
    assert s == 400 and json.loads(b)["error"]["code"] == "INVALID_INPUT"
    s, _, b = _req(server + "/api/tools/get_market_performance", b"{" + b" " * 70_000 + b"}")
    assert s == 413


def test_e2e_market_product_opportunity_scenario(server):
    s, _, b = _req(server + "/api/tools/get_market_performance", {"level": "subgroup", "top_n": 3})
    out = json.loads(b)
    assert s == 200 and out["ok"] and out["result"]["row_count"] == 3 and "elapsed_ms" in out
    s, _, b = _req(server + "/api/tools/get_brand_performance", {"top_n": 3})
    assert s == 200 and json.loads(b)["result"]["rows"][0]["brand"]
    s, _, b = _req(server + "/api/tools/get_opportunity_scores", {"level": "product", "top_n": 3})
    assert s == 200 and json.loads(b)["result"]["methodology"]["version"] == OPP.version
    s, _, b = _req(server + "/api/tools/run_scenario", {"scenario_type": "MARKET_GROWTH", "entity_type": "total",
                                                         "entity_key": "TOTAL", "assumptions": {"market_growth_pct": 3}})
    r = json.loads(b)["result"]
    assert s == 200 and r["scenario_result"]["kind"] == "CALCULATED" and r["percentage_change"]["value_pct"] == pytest.approx(3.0)
    s, _, b = _req(server + "/api/tools/run_scenario", {"scenario_type": "MARKET_GROWTH", "entity_type": "product",
                                                         "entity_key": "1", "assumptions": {"market_growth_pct": 3}})
    assert s == 400 and json.loads(b)["error"]["code"] == "INVALID_INPUT"
    s, _, b = _req(server + "/api/tools/does_not_exist", {})
    assert s == 404 and json.loads(b)["error"]["code"] == "UNKNOWN_TOOL"
    for body in (b,):
        assert not PATH_RX.search(body.decode())


def test_ui_uses_only_registered_tools_and_local_assets():
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'tool\("([a-z_]+)"', js))
    assert used and used <= set(T.TOOLS), used - set(T.TOOLS)
    # loaded assets: exactly the two local files (M11: navigation links are checked separately below)
    assets = set(re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)) | set(re.findall(r'<link[^>]*\bhref="([^"]+)"', html))
    assert assets == {"/static/app.js", "/static/style.css"}
    links = set(re.findall(r'<a[^>]*\bhref="([^"]+)"', html))
    assert links and all(h.startswith("#/") or h == "/agent" or h == "#main" for h in links), links   # in-app routes only
    assert set(re.findall(r'(?:src|href)="([^"]+)"', html)) == assets | links
    assert 'style="' not in js and 'style="' not in html            # CSP-compatible (no inline styles)
    assert "<script>" not in html                                    # no inline script
    for page in ("overview", "market", "product", "company", "therapy", "opportunity", "scenario", "method", "nl"):
        assert f'id="page-{page}"' in html and f'data-page="{page}"' in html
    assert "DESCRIPTIVE OPPORTUNITY SCORE" in html and "SCENARIO / WHAT-IF ANALYSIS — NOT A FORECAST" in html
