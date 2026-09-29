"""Phase 2 — Gemini agent-contract package: offline tests (no network, no API key).

Live Gemini validation is run manually (python -m pci_llm_eval.gemini_eval --live, synthetic only);
these tests prove the parts that must hold regardless of the model.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pci_llm_eval import contracts as C
from pci_llm_eval import gemini_eval as G

ROOT = Path(__file__).resolve().parents[1]


def _env(**kv):
    e = {k: v for k, v in os.environ.items() if k not in ("PCI_DATASET", "GEMINI_API_KEY")}
    e.update(kv)
    return e


def test_declarations_generated_from_registry_and_gemini_compatible():
    from pci_app.tools import TOOLS
    decls = C.function_declarations()
    names = [d["name"] for d in decls]
    assert set(TOOLS) <= set(names) and {"get_geography_performance", "get_data_quality_status"} <= set(names)
    assert len(names) == len(set(names)) == len(TOOLS) + 1           # + geography (always UNSUPPORTED)
    text = json.dumps(decls)
    assert "additionalProperties" not in text and '"type": "object"' not in text   # Gemini Schema subset
    for agent, tools in C.AGENTS.items():
        assert set(tools) <= set(names), agent
    owned = [t for ts in C.AGENTS.values() for t in ts]
    assert len(owned) == len(set(owned))                                            # each tool owned by one agent


def test_output_contract_fields():
    assert C.OUTPUT_CONTRACT["required"] == ["intent", "selected_agent", "tool_calls", "filters", "evidence", "answer",
                                             "limitations", "verification_status"]


def test_eval_set_size_and_coverage():
    cases = json.loads((ROOT / "python" / "pci_llm_eval" / "eval_cases.json").read_text(encoding="utf-8"))["cases"]
    cats = {c["category"].split("/")[0] for c in cases}
    assert len(cases) >= 20 and {"routing", "insufficient-data", "unsupported", "adversarial"} <= cats
    assert sum(1 for c in cases if c["category"].startswith("adversarial")) >= 3
    assert sum(1 for c in cases if c.get("expect_refusal")) >= 6


def test_grounding_rejects_invented_numbers():
    results = [{"ok": True, "result": {"rows": [{"value_growth_pct": 12.8431, "value_cur": 7532.9}]}}]
    assert G.grounded("Growth was 12.8% on 7,532.90 crore.", "q", results) == []
    assert G.grounded("Growth was 45% last year.", "q", results) == ["45"]
    assert G.grounded("It is 13.5% of the market.", "q", results) == ["13.5"]


def test_qa_refusal_and_routing():
    case = {"question": "Which region sells most?", "expect_refusal": True}
    out = {k: None for k in C.OUTPUT_CONTRACT["required"]}
    out.update(verification_status="REFUSED", answer="Geography is not available in this data.")
    assert G.qa(case, out, [])["passed"]
    assert not G.qa(case, out, [{"tool": "get_market_performance", "result": {}}])["passed"]     # analytics called
    out2 = dict(out, verification_status="UNVERIFIED", answer="North is about 30%.")
    assert not G.qa(case, out2, [])["passed"]


def test_runner_refuses_private_data(tmp_path):
    """IMS mode (explicit, with a valid external data directory) must be refused before anything is read or sent."""
    ims = tmp_path / "external_ims"
    ims.mkdir()
    r = subprocess.run([sys.executable, "-m", "pci_llm_eval.gemini_eval", "--dry-run"], cwd=ROOT / "python",
                       env=_env(PCI_DATASET="ims", PCI_IMS_DATA_DIR=str(ims)), capture_output=True, text=True,
                       timeout=300)
    assert r.returncode != 0 and "REFUSED" in (r.stderr + r.stdout)


def test_no_key_in_code_and_key_sent_in_header_only():
    src = (ROOT / "python" / "pci_llm_eval" / "gemini_eval.py").read_text(encoding="utf-8")
    assert "x-goog-api-key" in src and "?key=" not in src and "AIza" not in src


class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, status="X", details=None, message="m"):
    import io
    import urllib.error
    body = json.dumps({"error": {"code": code, "status": status, "message": message, "details": details or []}})
    return urllib.error.HTTPError("https://example.invalid", code, status, {}, io.BytesIO(body.encode()))


def _fake_urlopen(monkeypatch, outcomes):
    """Each outcome is an exception (raised) or a dict (returned as the JSON body); records every request."""
    seen = []

    def fake(req, timeout=None):
        seen.append(req)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return _Resp(o)
    monkeypatch.setattr(G.urllib.request, "urlopen", fake)
    return seen


def test_post_retries_transient_429_503_then_succeeds(monkeypatch):
    seen = _fake_urlopen(monkeypatch, [_http_error(503, "UNAVAILABLE"), _http_error(429, "RESOURCE_EXHAUSTED"),
                                       {"ok": 1}])
    stats, waits = G.new_stats(), []
    assert G._post("m", "k", {}, stats, sleep=waits.append) == {"ok": 1}
    assert stats["http_503"] == stats["http_429"] == 1 and stats["retries"] == 2 and len(seen) == 3
    assert all(0 < w <= G.BACKOFF_CAP_S for w in waits) and stats["backoff_s"] == pytest.approx(sum(waits), abs=0.02)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_post_never_retries_client_errors_and_keeps_google_message(monkeypatch, code):
    _fake_urlopen(monkeypatch, [_http_error(code, "NOT_FOUND", message="models/x is not found for API version v1beta")])
    stats, waits = G.new_stats(), []
    with pytest.raises(G.GeminiAPIError) as ei:
        G._post("m", "k", {}, stats, sleep=waits.append)
    assert ei.value.info == {"http_code": code, "status": "NOT_FOUND", "attempts": 1,
                             "message": "models/x is not found for API version v1beta"}
    assert waits == [] and stats["retries"] == 0


def test_post_retries_are_bounded_and_per_day_quota_is_not_retried(monkeypatch):
    _fake_urlopen(monkeypatch, [_http_error(503)] * (G.MAX_RETRIES + 1))
    stats, waits = G.new_stats(), []
    with pytest.raises(G.GeminiAPIError) as ei:
        G._post("m", "k", {}, stats, sleep=waits.append)
    assert ei.value.info["attempts"] == G.MAX_RETRIES + 1 and len(waits) == G.MAX_RETRIES
    assert sum(waits) <= G.MAX_WAIT_PER_REQUEST_S
    daily = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
              "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]
    _fake_urlopen(monkeypatch, [_http_error(429, "RESOURCE_EXHAUSTED", daily)])
    with pytest.raises(G.GeminiAPIError) as ei:
        G._post("m", "k", {}, G.new_stats(), sleep=waits.append)
    assert ei.value.info["attempts"] == 1


def test_post_honours_retry_hint_and_redacts_key(monkeypatch):
    hint = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]
    _fake_urlopen(monkeypatch, [_http_error(429, details=hint), _http_error(400, message="bad key SECRET123 rejected")])
    waits = []
    with pytest.raises(G.GeminiAPIError) as ei:
        G._post("m", "SECRET123", {}, G.new_stats(), sleep=waits.append)
    assert waits and waits[0] >= 7 and "SECRET123" not in json.dumps(ei.value.info)


def test_dry_run_on_synthetic(tmp_path):
    if not (ROOT / "data" / "synthetic" / "pack.parquet").exists():
        subprocess.run([sys.executable, "-m", "pci_synthetic.generate"], cwd=ROOT / "python", check=True, timeout=300)
    r = subprocess.run([sys.executable, "-m", "pci_llm_eval.gemini_eval", "--dry-run", "--bundle", str(tmp_path)],
                       cwd=ROOT / "python", env=_env(PCI_DATASET="synthetic"), capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-500:]
    s = json.loads(r.stdout[r.stdout.index("{"):])
    assert s["contract_problems"] == [] and s["dry_run_passed"] == s["dry_run_calls"] > 15
    assert (tmp_path / "function_declarations.json").exists() and (tmp_path / "system_prompt.md").exists()
