"""Optional external LLM providers (Phase 3). Loaded lazily by pci_agents.providers.get_provider only when
LLM_PROVIDER names one; the default LLM_PROVIDER=NONE never imports this package (no network code runs)."""
