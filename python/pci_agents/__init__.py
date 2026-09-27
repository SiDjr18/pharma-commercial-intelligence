"""M9 governed, provider-agnostic agentic layer over the M8 ToolRegistry.

Agents reach analytics ONLY through pci_agents.gateway.ToolGateway -> pci_app.tools.ToolRegistry.
This package must never import the analytics engines, DuckDB, pyarrow, the filesystem or the network.
"""
from .orchestrator import INTENTS, Orchestrator
from .providers import DeterministicDemoProvider, LLMProvider, ProviderNotConfigured, get_provider

__all__ = ["Orchestrator", "INTENTS", "LLMProvider", "DeterministicDemoProvider", "ProviderNotConfigured",
           "get_provider"]
