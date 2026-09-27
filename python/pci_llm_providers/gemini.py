"""Optional Gemini provider for the governed agent layer (Phase 3). Opt-in: LLM_PROVIDER=GEMINI.

Guarantees (tested in tests/test_gemini_provider_phase3.py with a mocked model):
  * SYNTHETIC ONLY - construction fails unless PCI_DATASET=synthetic, so the licensed data can never be used
    with an external model; the app then falls back to LLM_PROVIDER=NONE and says so;
  * the key comes from the environment only and travels in a header, never in a URL, file or log;
  * the model sees ONLY the user's question and the static intent catalogue - no data, no tool result;
  * the model only ROUTES: it returns {intent, params}; the orchestrator validates both against the allow-lists
    and permissioned agents call the deterministic tools;
  * deterministic safety refusals (geography, channel, forecasts, code/SQL/raw data, instruction overrides) are
    applied BEFORE the model is asked, and the model's own words are never used as the answer;
  * the answer text is composed deterministically from tool findings, and QA checks every number against the
    tool results. The model cannot introduce a metric.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from pci_agents import parser
from pci_agents import schemas as S
from pci_agents.orchestrator import INTENT_PARAMS, INTENTS
from pci_agents.providers import DeterministicDemoProvider, LLMProvider, ProviderNotConfigured

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LABEL = "GEMINI ROUTING (synthetic data) - the model selects the analysis; numbers and text come from deterministic tools"
_DETERMINISTIC_REFUSALS = {S.UNSAFE_REQUEST, "UNSUPPORTED_GEOGRAPHY", "UNSUPPORTED_SSA_HSA_DSA", "UNSUPPORTED_CHANNEL",
                           "UNSUPPORTED_ANALYSIS", "UNSUPPORTED_REQUEST"}
RESPONSE_SCHEMA = {"type": "OBJECT", "required": ["intent", "params_json"],
                   "properties": {"intent": {"type": "STRING", "enum": list(INTENTS) + ["NONE"]},
                                  "params_json": {"type": "STRING"}}}


def _catalogue() -> str:
    lines = [f"- {i}: allowed params {sorted(INTENT_PARAMS[i]) or '(none)'}" for i in INTENTS]
    return ("Map the user's question to ONE intent of a pharma sales-analytics tool and its parameters. Do not answer "
            "the question and do not produce numbers. Return JSON {\"intent\": <intent or NONE>, \"params_json\": "
            "<JSON object as a string>}. Use only the allowed parameter names; omit anything not stated. Dates are "
            "YYYY-MM-01; basis MONTH|YTD|MAT; levels total|supergroup|therapy_group|subgroup|molecule (therapy area = "
            "supergroup). Scenario assumptions go in params.assumptions (price_change_pct, volume_change_pct, "
            "market_growth_pct, target_share_pct). Use NONE for anything outside this catalogue.\nIntents:\n"
            + "\n".join(lines))


class GeminiProvider(LLMProvider):
    name = "GEMINI"
    is_llm = True

    def __init__(self, model: str | None = None, key: str | None = None):
        from pci_data.schema import DATASET
        if DATASET != "synthetic":
            raise ProviderNotConfigured("LLM_PROVIDER=GEMINI requires PCI_DATASET=synthetic: an external model is never "
                                        "used with the licensed dataset")
        self._key = key or os.environ.get("GEMINI_API_KEY")
        if not self._key:
            raise ProviderNotConfigured("LLM_PROVIDER=GEMINI requires GEMINI_API_KEY in the environment")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # network boundary (mocked in tests)
    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(API.format(model=self.model), json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "x-goog-api-key": self._key})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def plan(self, text, tool_contracts):
        guard = parser.parse(text)
        if "refusal" in guard and guard["refusal"] in _DETERMINISTIC_REFUSALS:
            return guard                                          # never sent to the model
        t = " ".join(str(text or "").split())[:500]
        try:
            resp = self._post({"system_instruction": {"parts": [{"text": _catalogue()}]},
                               "contents": [{"role": "user", "parts": [{"text": t}]}],
                               "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                                    "responseSchema": RESPONSE_SCHEMA}})
            out = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
            intent = out.get("intent")
            params = json.loads(out.get("params_json") or "{}")
        except (urllib.error.URLError, KeyError, IndexError, ValueError, TypeError):
            return {"refusal": S.PROVIDER_UNAVAILABLE, "message": "The language model could not be reached or returned "
                    "an invalid plan; no answer was produced."}
        if intent in (None, "NONE") or not isinstance(params, dict):
            return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "The request could not be mapped to a supported "
                    "analysis.", "supported": parser.SUPPORTED_FORMS}
        return {"intent": intent, "params": params}               # validated by the orchestrator allow-lists

    def compose(self, response):
        return DeterministicDemoProvider.compose(self, response)  # deterministic template; uses self.label

    label = LABEL

    def metadata(self):
        return {"provider": self.name, "is_llm": True, "network": True, "api_key_required": True, "label": LABEL,
                "model": self.model, "description": "Gemini routes requests (intent + parameters) on synthetic data; "
                "deterministic tools compute every number and templates write the answer."}
