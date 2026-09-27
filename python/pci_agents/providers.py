"""LLM provider boundary (provider-agnostic). M9 ships NO external provider.

LLM_PROVIDER=NONE (default) -> DeterministicDemoProvider: a rule-based request grammar plus
template composition. It is NOT an LLM and says so ("DETERMINISTIC DEMO MODE").
A future provider implements the same two methods; whatever it produces is still governed by
agent tool permissions, the ToolRegistry and the QA engine (numbers must trace to tool results).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from . import parser
from . import schemas as S


class ProviderNotConfigured(Exception):
    pass


class LLMProvider(ABC):
    """Minimal contract every provider must satisfy."""

    name: str = "abstract"
    is_llm: bool = False

    @abstractmethod
    def plan(self, text: str, tool_contracts: dict) -> dict:
        """Structured tool-call request: {"intent", "params"} or {"refusal", "message"}."""

    @abstractmethod
    def compose(self, response: dict) -> str:
        """Final user-facing text built ONLY from findings/limitations in `response`."""

    def metadata(self) -> dict:
        return {"provider": self.name, "is_llm": self.is_llm, "network": False, "api_key_required": False}


class DeterministicDemoProvider(LLMProvider):
    name = "NONE"
    is_llm = False
    label = S.DEMO_MODE_LABEL

    def plan(self, text, tool_contracts):
        return parser.parse(text)

    def compose(self, response):
        lines = [f"[{self.label}] {response['intent'] or 'request'} — status {response['status']}"]
        if response.get("banners"):
            lines += response["banners"]
        lines += [f"- {f['statement']}" for f in response["findings"]]
        if response.get("clarification"):
            c = response["clarification"]
            lines.append(c["message"])
            lines += [f"  * {o['label']}" for o in c["options"]]
        if response.get("message"):
            lines.append(response["message"])
        if response["limitations"]:
            lines.append("Limitations:")
            lines += [f"  * {x}" for x in response["limitations"]]
        return "\n".join(lines)

    def metadata(self):
        return {**super().metadata(), "label": self.label,
                "description": "Rule-based request grammar and template text; no language model is running."}


_EXTERNAL = {"OPENAI", "CLAUDE", "ANTHROPIC", "AZURE", "VERTEX"}
_REGISTRY = {"NONE": DeterministicDemoProvider}
# opt-in providers (Phase 3): synthetic data only, key from the environment; see pci_llm_providers/
_OPTIONAL = {"GEMINI": ("pci_llm_providers.gemini", "GeminiProvider")}


def register_provider(name: str, cls):
    """Plug-in point for a future provider (M10+). Not used by the application in M9."""
    _REGISTRY[name.upper()] = cls


def get_provider(name: str | None = None) -> LLMProvider:
    n = (name if name is not None else os.environ.get("LLM_PROVIDER", "NONE")).strip().upper() or "NONE"
    if n in _REGISTRY:
        return _REGISTRY[n]()
    if n in _OPTIONAL:                     # Phase 3: opt-in provider, imported only when requested (network lives there)
        import importlib
        module, cls = _OPTIONAL[n]
        return getattr(importlib.import_module(module), cls)()     # raises ProviderNotConfigured if not allowed
    if n in _EXTERNAL:
        raise ProviderNotConfigured(f"LLM provider {n} is not implemented in M9 (no external AI is connected); "
                                    "use LLM_PROVIDER=NONE")
    raise ProviderNotConfigured(f"unknown LLM provider {n!r}")
