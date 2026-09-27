"""External-LLM agent-contract validation package (Phase 2: Google AI Studio / Gemini).

SYNTHETIC DATA ONLY. The runner refuses to start unless PCI_DATASET=synthetic, and only structured tool outputs
computed locally on the synthetic dataset are ever sent to the model. Tools execute locally (M8 ToolRegistry +
two local executors); the model only routes, calls functions and phrases answers; a local QA step verifies
that every number in the answer comes from a tool result. Nothing here is imported by the app or the agents.
"""
