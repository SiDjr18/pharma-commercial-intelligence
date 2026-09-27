"""M11: premium UI/UX — information architecture, route and element integrity, tool/field contracts,
states, security/privacy invariants, accessibility and responsive rules, and HTTP end-to-end.

The UI is vanilla JS served by the stdlib server; these tests check it statically (HTML/CSS/JS source)
and through the real HTTP server + ToolRegistry. No browser or Node dependency is required
(a JS syntax check runs only if Node happens to be installed)."""
import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from pci_agents import parser as P
from pci_agents import schemas as S
from pci_analytics import CommercialAnalytics
from pci_analytics.api import BASES, SEGMENTS
from pci_analytics.engine import ENTITY_TYPES
from pci_analytics.opportunity import QUADRANTS
from pci_analytics.scenario import SCENARIO_TYPES
from pci_app import tools as T
from pci_app.server import SECURITY_HEADERS, STATIC, WEB_DIR, create_server
from pci_data.schema import SOURCE_PATH

HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
AHTML = (WEB_DIR / "agent.html").read_text(encoding="utf-8")
JS = (WEB_DIR / "app.js").read_text(encoding="utf-8")
AJS = (WEB_DIR / "agent.js").read_text(encoding="utf-8")
CSS = (WEB_DIR / "style.css").read_text(encoding="utf-8")
PAGES = ["overview", "market", "therapy", "product", "company", "opportunity", "scenario", "nl", "method"]
PRIMARY = ["Executive Overview", "Market Intelligence", "Brand &amp; Portfolio", "Company Intelligence",
           "Opportunity Intelligence", "Scenario Planning", "AI Analyst", "Methodology &amp; Data Quality"]


def _block(src: str, start: str) -> str:
    """Source of a `const NAME = {...};` object literal (balanced braces)."""
    i = src.index(start)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[j:k + 1]
    raise AssertionError(start)


# ================================================================== A. information architecture & navigation
def test_primary_navigation_is_the_product_ia():
    nav = HTML[HTML.index('id="tabs"'):HTML.index("</nav>", HTML.index('id="tabs"'))]
    labels = re.findall(r'</span>([^<]+)</a>', nav)
    assert labels == PRIMARY
    assert re.findall(r'data-page="([a-z]+)"', nav) == ["overview", "market", "therapy", "product", "company",
                                                         "opportunity", "scenario", "nl", "method"]
    assert 'class="sub">Segments &amp; mix' in nav                    # secondary item under Market Intelligence


def test_every_route_has_a_section_title_and_renderer():
    titles = _block(JS, "const PAGE_TITLE")
    renderers = _block(JS, "const PAGES =")
    for p in PAGES:
        assert f'id="page-{p}"' in HTML and f'data-page="{p}"' in HTML, p
        assert re.search(rf"\b{p}:", titles) and re.search(rf"\b{p}:", renderers), p
    for p in PAGES:                                                   # exactly one h1 per page section
        sec = HTML[HTML.index(f'id="page-{p}"'):]
        sec = sec[:sec.index("</section>\n\n") if "</section>\n\n" in sec else len(sec)]
        assert sec.count("<h1") == 1, p


def test_agent_page_shares_the_shell_and_navigation():
    def nav(h):
        n = h[h.index('id="tabs"'):h.index("</nav>", h.index('id="tabs"'))]
        return re.findall(r'data-page="([a-z]+)"[^>]*>(?:<span[^>]*>\d+</span>)?([^<]+)</a>', n)
    assert [p for p, _ in nav(AHTML)] == [p for p, _ in nav(HTML)]
    assert [t for _, t in nav(AHTML)] == [t for _, t in nav(HTML)]
    assert re.search(r'href="/agent" data-page="nl" aria-current="page"', AHTML)
    for p in ("overview", "market", "product", "company", "opportunity", "scenario", "method"):
        assert f'href="/#/{p}"' in AHTML                              # links back into the SPA routes
    assert "/static/style.css" in AHTML and "AGENTIC ANALYTICS" in AHTML


def test_internal_links_and_drilldowns_target_known_routes():
    for h in re.findall(r'href="#/([a-z]+)', HTML):
        assert h in PAGES, h
    for page in re.findall(r'\b(?:href|go)\("([a-z]+)"', JS):          # every drill-down / navigation call
        assert page in PAGES, page
    for sec in re.findall(r'#/method\?s=([a-z-]+)', HTML):             # methodology table of contents
        assert f'id="{sec}"' in HTML, sec


def test_every_element_id_used_by_the_scripts_exists():
    def check(js, html):
        created = set(re.findall(r'id="([A-Za-z0-9_-]+)"', js))       # ids created by templates at run time
        present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html)) | created
        used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js))
        dynamic = {u for u in used if u.startswith(("sa-", "sr-"))}   # per-assumption inputs (template ids)
        missing = used - present - dynamic
        assert not missing, missing
    check(JS, HTML)
    check(AJS, AHTML)
    for f in ("price_change_pct", "volume_change_pct", "market_growth_pct", "target_share_pct"):
        assert f in _block(JS, "const SC_FIELD")


# ================================================================== B. tool integration & field contracts
def test_ui_calls_only_whitelisted_tools_and_local_endpoints():
    used = set(re.findall(r'tool\("([a-z_]+)"', JS))
    assert used <= set(T.TOOLS) and len(used) >= 12, used - set(T.TOOLS)
    assert set(re.findall(r"fetch\(\s*[`'\"]([^`'\"$]+)", JS)) <= {"/api/tools/", "/api/tools"}
    assert set(re.findall(r"fetch\(\s*[`'\"]([^`'\"$]+)", AJS)) <= {"/api/agent", "/api/agent/status"}
    assert "tool(" not in AJS and "/api/tools" not in AJS            # the AI Analyst never bypasses the agents
    for js in (JS, AJS):
        for bad in ("XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts", "eval(",
                    "new Function", "document.write", "postMessage"):
            assert bad not in js, bad


def test_error_codes_and_agent_statuses_have_human_states():
    err = _block(JS, "const ERR =")
    for code in T.ERROR_CATEGORIES:
        assert re.search(rf"\b{code}:", err), code
    assert re.search(r"\bNETWORK:", err)
    st = _block(AJS, "const STATUS =")
    for code in (S.OK, S.AMBIGUOUS_ENTITY, S.UNSAFE_REQUEST, S.UNRECOGNIZED_REQUEST, S.QA_FAILED, S.TOOL_NOT_PERMITTED):
        assert re.search(rf"\b{code}:", st), code
    assert 'startsWith("UNSUPPORTED")' in AJS and 'startsWith("UNSUPPORTED")' in JS


def test_selector_vocabularies_match_the_engines():
    labels = _block(JS, "const LEVEL_LABEL")
    for k in list(ENTITY_TYPES) + list(SEGMENTS):
        assert re.search(rf"\b{k}:", labels), k
    basis = _block(JS, "const BASIS_SHORT")
    assert all(re.search(rf"\b{b}:", basis) for b in BASES)
    types = _block(JS, "const SC_TYPES")
    assert set(re.findall(r"\b([A-Z_]{6,}):", types)) == set(SCENARIO_TYPES)
    props = T.TOOLS["run_scenario"]["input_schema"]["properties"]["assumptions"]["properties"]
    assert set(re.findall(r"\b([a-z_]+_pct):", _block(JS, "const SC_FIELD"))) == set(props)
    for q in QUADRANTS.values():                                     # opportunity matrix wording = engine labels
        assert f'"{q}"' in JS, q


# UI parameter shapes (as built in app.js) must pass the ToolRegistry contracts, and every field the UI reads
# must exist in the engine output — otherwise a panel would silently show "n/a".
PERF_READ = {"entity_key", "entity_label", "value_cur", "value_abs_chg", "value_growth_pct", "value_growth_status",
             "value_share_pct", "value_share_prior_pct", "value_share_chg_pp", "evolution_index",
             "contribution_to_growth_pp", "units_cur", "units_growth_pct", "rank_value", "n_entities"}
TREND_READ = {"period", "value_cr", "value_cr_prior", "value_mat", "value_mat_prior", "value_mat_growth_pct",
              "value_growth_pct", "units_k", "units_k_prior"}
OPP_PRODUCT_READ = {"entity_key", "entity_label", "prod_code", "brand", "company", "subgroup", "supergroup", "score",
                    "score_status", "insufficient_reason", "opportunity_rank", "components", "positive_drivers",
                    "constraints", "matrix_quadrant", "evolution_index", "market_value_growth_pct",
                    "value_share_in_market_pct", "value_cur", "value_growth_pct"}
OPP_MARKET_READ = {"entity_key", "entity_label", "score", "score_status", "value_growth_pct",
                   "value_share_of_total_pct", "value_cur", "supergroup"}


@pytest.fixture(scope="module")
def reg(con, py_engine):
    api = CommercialAnalytics(con)
    api._opp_engine = py_engine
    return T.ToolRegistry(api)


def _ok(reg, name, params):
    out = reg.invoke(name, params)
    assert out["ok"], (name, params, out.get("error"))
    assert name in JS                                                # the UI really uses this tool
    return out["result"]


def test_ui_parameter_shapes_and_fields_match_contracts(reg):
    p = {"anchor": "2024-05-01", "basis": "MAT"}
    for f in PERF_READ | TREND_READ | OPP_PRODUCT_READ | OPP_MARKET_READ:
        assert f in JS, f                                            # the UI reads these fields
    tot = _ok(reg, "get_market_performance", {"level": "total", **p})
    assert PERF_READ <= set(tot["rows"][0]) and {"cur_start", "cur_end", "prior_start", "prior_end", "basis_label"} <= set(tot["period"])
    assert TREND_READ <= set(_ok(reg, "get_market_trends", {"level": "total", "key": "TOTAL"})["rows"][0])
    area = _ok(reg, "get_therapy_performance", {"level": "supergroup", **p})["rows"][0]["entity_key"]
    th = _ok(reg, "get_therapy_performance", {"level": "subgroup", **p, "within_supergroup": area, "top_n": None})
    assert PERF_READ | {"supergroup"} <= set(th["rows"][0])
    sg = th["rows"][0]["entity_key"]
    prods = _ok(reg, "get_brand_performance", {**p, "market_level": "subgroup", "market_key": sg, "top_n": 15})
    assert PERF_READ | {"brand", "company"} <= set(prods["rows"][0])
    co = _ok(reg, "get_company_performance", {**p, "market_level": "subgroup", "market_key": sg, "top_n": 10})["rows"][0]
    assert PERF_READ | {"indian_mnc"} <= set(co)
    code = prods["rows"][0]["entity_key"]
    _ok(reg, "get_brand_share", {"prod_code": code, "market_level": "subgroup", "market_key": sg, **p})
    assert TREND_READ <= set(_ok(reg, "get_brand_growth", {"prod_code": code, "market_level": "subgroup", "market_key": sg})["rows"][0])
    _ok(reg, "get_segment_analysis", {"segment": "acute_chronic", **p, "market_level": "company", "market_key": co["entity_key"]})
    opp = _ok(reg, "get_opportunity_scores", {"level": "product", "anchor": p["anchor"], "basis": "MAT",
                                              "company": co["entity_key"], "include_insufficient": True, "top_n": None})
    assert OPP_PRODUCT_READ <= set(opp["rows"][0])
    assert {"version", "fingerprint", "components", "thresholds", "population_size", "normalization"} <= set(opp["methodology"])
    mk = _ok(reg, "get_opportunity_scores", {"level": "market", "anchor": p["anchor"], "basis": "MAT", "include_insufficient": True, "top_n": 50})
    assert OPP_MARKET_READ <= set(mk["rows"][0]) and {"SCORED", "INSUFFICIENT_EVIDENCE"} <= set(mk["status_counts"])
    det = _ok(reg, "get_opportunity_detail", {"level": "product", "entity_key": opp["rows"][0]["entity_key"], **p})
    assert det["rows"][0]["components"]
    found = _ok(reg, "find_products", {"name_contains": prods["rows"][0]["brand"][:4], "limit": 100})
    assert {"prod_code", "brand", "company", "prod_launch_month"} <= set(found["rows"][0])


@pytest.mark.parametrize("stype,etype,assump,scope", [
    ("PRICE_CHANGE", "product", {"price_change_pct": 5}, None),
    ("VOLUME_CHANGE", "supergroup", {"volume_change_pct": -2}, None),
    ("PRICE_VOLUME_CHANGE", "total", {"price_change_pct": 5, "volume_change_pct": -2}, None),
    ("MARKET_GROWTH", "subgroup", {"market_growth_pct": 8}, None),
    ("MARKET_SHARE", "company", {"target_share_pct": 6}, ("total", "TOTAL")),
])
def test_scenario_builder_shapes_and_observed_assumed_calculated(reg, stype, etype, assump, scope):
    p = {"anchor": "2024-05-01", "basis": "MAT"}
    key = {"total": "TOTAL",
           "product": lambda: reg.invoke("get_brand_performance", {**p, "top_n": 1})["result"]["rows"][0]["entity_key"],
           "company": lambda: reg.invoke("get_company_performance", {**p, "top_n": 1})["result"]["rows"][0]["entity_key"],
           }.get(etype) or reg.invoke("get_market_performance", {"level": etype, **p, "top_n": 1})["result"]["rows"][0]["entity_key"]
    key = key() if callable(key) else key
    args = {"scenario_type": stype, "entity_type": etype, "entity_key": key, "assumptions": assump, **p}
    if scope:
        args.update(market_level=scope[0], market_key=scope[1])
    r = _ok(reg, "run_scenario", args)
    assert r["baseline"]["kind"] == "OBSERVED" and r["assumptions"]["kind"] == "ASSUMED"
    assert r["scenario_result"]["kind"] == "CALCULATED" and r["absolute_change"]["kind"] == "CALCULATED"
    for k in r["scenario_result"]:                                  # every calculated measure has a UI row
        if k != "kind":
            assert f'"{k}"' in _block(JS, "function scenarioResult"), k


def test_invalid_assumptions_are_rejected_not_corrected(reg):
    out = reg.invoke("run_scenario", {"scenario_type": "PRICE_CHANGE", "entity_type": "total", "entity_key": "TOTAL",
                                      "assumptions": {"price_change_pct": -150}})
    assert not out["ok"] and out["error"]["code"] == "INVALID_SCENARIO"
    assert "rejected, never corrected" in JS
    # the UI passes the typed number unchanged: sliders never clamp what is sent
    run = JS[JS.index("async function runScenario"):JS.index("function scenarioResult")]
    assert "assumptions[f] = v;" in run and "clamp(" not in run


# ================================================================== C. states, disclaimers, data integrity
def test_disclaimers_and_methodology_labels_are_always_visible():
    for s in ("DESCRIPTIVE OPPORTUNITY SCORE", "NOT A FORECAST", "NOT A PROBABILITY", "NOT A GUARANTEE",
              S.SCENARIO_BANNER, "Scenario analysis — not a forecast.", "Invalid assumptions are rejected, never corrected."):
        assert s in HTML, s
    assert "Scenario analysis — not a forecast." in JS               # repeated on every scenario result
    assert "Descriptive — not a forecast" in JS and "not a score of zero" in JS and "never zero" in JS
    assert S.DEMO_MODE_LABEL in AHTML and "LLM connected" in AJS


def test_ambiguity_is_never_resolved_automatically():
    assert "never picks one for you" in JS and "Clarification needed" in JS
    assert "none is selected automatically" in JS
    assert "the analyst will not pick one for you" in AJS and "continuation: r.continuation" in AJS
    search = JS[JS.index("function productSearch"):JS.index("function productBoard")]
    assert "onSelect" in search and "setParams({ code:" in search     # a product is chosen only by the user
    assert "rows[0].prod_code" not in search and "exact[0]" not in search


def test_missing_values_render_as_na_never_zero():
    assert "const na = " in JS and "NA_REASON" in JS
    for f in ("pct", "share", "pp", "cr", "ei"):
        assert re.search(rf'\b{f}: \(v[^)]*\) => \(isNum\(v\) \? .* : "n/a"\)', JS), f
    assert "prior_unavailable" in JS and "adp(" in JS                 # tiny non-zero values never display as 0


def test_ui_contains_no_hardcoded_business_figures_or_client_side_metrics():
    for src in (HTML, AHTML):
        src = re.sub(r"placeholder='[^']*'|placeholder=\"[^\"]*\"", "", src)   # example requests are not figures
        assert not re.search(r"₹\s?\d", src)
        assert set(re.findall(r"\d+(?:\.\d+)?\s?%", src)) <= {"0%"}, re.findall(r"\d+(?:\.\d+)?\s?%", src)
    for js in (JS, AJS):
        assert not re.search(r"₹\s?\d", js)
        for rx in (r"value_cur\s*[-/]\s*\w*\.?value_prior", r"value_cur\s*/\s*\w*\.?scope_value", r"value_mat\s*/\s*12",
                   r"units_cur\s*[-/]\s*\w*\.?units_prior", r"value_share_pct\s*-\s*\w*\.?value_share_prior"):
            assert not re.search(rx, js), rx                         # growth/share/EI are never re-derived here
    for raw in ("pfc", "pack_desc", "source_row", "PACK_DESC", SOURCE_PATH.stem):
        assert raw not in JS and raw not in AJS and raw not in HTML


# ================================================================== D. security / privacy / runtime
def test_no_external_assets_inline_code_or_tracking():
    for name, src in (("index.html", HTML), ("agent.html", AHTML), ("app.js", JS), ("agent.js", AJS), ("style.css", CSS)):
        assert not re.search(r"https?://", src), name
        assert "@import" not in src and "cdn" not in src.lower() and "url(" not in src.replace("URLSearchParams", ""), name
        for bad in ("gtag", "google-analytics", "sentry", "mixpanel", "segment.io", "hotjar", "telemetry.send"):
            assert bad not in src.lower(), (name, bad)
    for src in (HTML, AHTML):
        assert "<script>" not in src and not re.search(r"\son[a-z]+=", src) and 'style="' not in src
    assert 'style="' not in JS and 'style="' not in AJS
    assert set(re.findall(r'<script[^>]*src="([^"]+)"', AHTML)) == {"/static/agent.js"}
    stored = set(re.findall(r'store\.set\("([a-z]+)"', JS))
    assert stored == {"anchor", "basis", "trend"}                   # UI preferences only; never data
    assert "localStorage" not in AJS and "sessionStorage" not in JS + AJS and "indexedDB" not in JS + AJS
    assert set(STATIC) == {"/", "/static/app.js", "/static/style.css", "/agent", "/static/agent.js"}


def test_payload_budget():
    total = sum((WEB_DIR / f).stat().st_size for f in ("index.html", "agent.html", "app.js", "agent.js", "style.css"))
    assert total < 300_000, total                                     # no framework, no bundled assets


def test_javascript_parses_when_node_is_available():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed (optional syntax check)")
    for f in ("app.js", "agent.js"):
        r = subprocess.run([node, "--check", str(WEB_DIR / f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr


# ================================================================== E. accessibility & responsive rules
def _controls(html):
    return re.findall(r"<(input|select|textarea)\b([^>]*)>", html)


@pytest.mark.parametrize("html", [HTML, AHTML], ids=["index", "agent"])
def test_form_controls_are_labelled(html):
    for tag, attrs in _controls(html):
        if 'type="hidden"' in attrs:
            continue
        m = re.search(r'\bid="([^"]+)"', attrs)
        assert m, (tag, attrs)
        cid = m.group(1)
        labelled = f'for="{cid}"' in html or "aria-label" in attrs or "aria-labelledby" in attrs
        wrapped = re.search(rf"<label[^>]*>(?:(?!</label>).)*id=\"{re.escape(cid)}\"", html, re.S)
        assert labelled or wrapped, cid


@pytest.mark.parametrize("html", [HTML, AHTML], ids=["index", "agent"])
def test_landmarks_skip_link_and_live_regions(html):
    assert '<html lang="en">' in html and 'name="viewport"' in html
    assert 'class="skip" href="#main"' in html and '<main id="main"' in html
    assert 'role="alert"' in html and "aria-live" in html
    for b in re.findall(r"<button\b([^>]*)>(.*?)</button>", html, re.S):
        assert "aria-label" in b[0] or re.sub(r"<[^>]+>", "", b[1]).strip(), b
    for svg in re.findall(r"<svg\b[^>]*>", html):
        assert 'aria-hidden="true"' in svg


def test_css_design_system_accessibility_and_breakpoints():
    root = CSS[CSS.index(":root {"):CSS.index("}", CSS.index(":root {"))]
    for token in ("--ink", "--accent", "--pos", "--neg", "--warn", "--serif", "--sans", "--mono", "--s-4", "--radius"):
        assert token in root, token
    assert ":focus-visible" in CSS and "prefers-reduced-motion" in CSS
    for bp in ("1280px", "1180px", "980px", "720px"):
        assert f"@media (max-width: {bp})" in CSS, bp
    assert re.search(r"\.tw \{[^}]*overflow: auto", CSS)             # tables scroll inside their panel, not the page
    assert ".delta .arr" in CSS                                       # arrows: colour is never the only signal
    js_classes = set(re.findall(r'class="([a-z0-9 -]+)"', JS + AJS + HTML))
    defined = set(re.findall(r"\.([a-z][a-z0-9-]*)", CSS))
    missing = {c for cls in js_classes for c in cls.split() if c not in defined} - {"hov", "answer"}   # JS hooks, not styles
    assert not missing, missing


def test_charts_and_tables_are_keyboard_operable():
    assert 'role="slider"' in JS and "ArrowLeft" in JS                 # line charts: read each month with arrows
    assert 'tabindex="0" role="button"' in JS                         # bar rows and matrix points
    assert "ArrowDown" in JS and 'e.key === "Enter" || e.key === " "' in JS
    assert "aria-sort" in JS and 'aria-modal="true"' in HTML and "Escape" in JS


# ================================================================== F. HTTP end-to-end
@pytest.fixture(scope="module")
def server(reg):
    srv = create_server(reg.api, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_pages_assets_and_headers(server):
    for path, marker in (("/", b"Commercial pulse"), ("/agent", b"Ask the governed analyst"),
                         ("/static/app.js", b"function pageOverview"), ("/static/agent.js", b"function pipeline"),
                         ("/static/style.css", b"--accent")):
        s, h, b = _get(server + path)
        assert s == 200 and marker in b, path
        assert h["Content-Security-Policy"] == SECURITY_HEADERS["Content-Security-Policy"]
        assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store"
    s, _, b = _get(server + "/")
    for label in PRIMARY:
        assert label.encode() in b, label
    assert _get(server + "/favicon.ico")[0] == 204
    for path in ("/static/index.html", "/static/../app/web/app.js", "/app.js", "/agent.html"):
        assert _get(server + path)[0] == 404, path


def test_http_agent_status_exposes_grammar_without_network(server):
    s, _, b = _get(server + "/api/agent/status")
    st = json.loads(b)
    assert s == 200 and st["provider"]["provider"] == "NONE" and st["llm_connected"] is False
    assert st["network_required"] is False and st["api_key_required"] is False
    assert st["supported_requests"] == P.SUPPORTED_FORMS


def test_http_ui_error_path_is_structured_and_safe(server):
    req = urllib.request.Request(server + "/api/tools/get_market_performance",
                                 data=json.dumps({"level": "subgroup", "anchor": "2021-07-01", "basis": "MAT"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=60)
        raise AssertionError("expected a structured error")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
    assert body["error"]["code"] == "INVALID_PERIOD" and "INVALID_PERIOD" in _block(JS, "const ERR =")
    assert "Traceback" not in json.dumps(body) and not re.search(r"[A-Za-z]:[\\/]", json.dumps(body))
