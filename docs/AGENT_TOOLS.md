# AGENT TOOL PERMISSIONS (M9)

Source of truth: `python/pci_agents/gateway.py::AGENT_TOOLS`. Access is deny by default: a call to any tool not listed for the calling agent raises `ToolNotPermitted`, and is recorded as `DENIED`. Any denial makes the response fail QA (`tool_provenance`).

| Agent | Permitted M8 tools | Purpose |
|---|---|---|
| MarketTrendAgent | `get_market_performance`, `get_market_trends` | market size, growth, share, ranking, trends |
| BrandProductAgent | `find_products`, `get_brand_performance`, `get_brand_growth`, `get_brand_share` | product search (with disambiguation), product performance, growth, share |
| CompanySegmentAgent | `get_company_performance`, `get_therapy_performance`, `get_segment_analysis` | company, therapy and segment views |
| OpportunityAgent | `get_opportunity_scores`, `get_opportunity_detail` | DESCRIPTIVE OPPORTUNITY SCORING (methodology preserved) |
| ScenarioAgent | `get_scenario_baseline`, `run_scenario` | what-if analysis: NOT A FORECAST |
| Orchestrator | none | invokes agents only |
| InsightQAAgent | none | reads recorded results only; never mutates |

Each of the 13 analytical tools belongs to exactly one agent. `get_application_metadata` (UI selectors) is not exposed to agents.

## Call path and records
`agent.tool(name, params)` → `ToolGateway.call(ctx, agent, name, params)` → permission check → `ToolRegistry.invoke(name, params)` (M8 schema validation and structured errors) → execution record:

```json
{"call": 1, "order": 1, "agent": "MarketTrendAgent", "tool": "get_market_performance",
 "input": {"level": "supergroup", "top_n": 3}, "status": "OK", "error_code": null, "row_count": 3, "total_rows": 25,
 "elapsed_ms": 290.1}
```
Full tool results are kept only in the in-memory request context, for the QA engine. They are never logged or persisted.

## Parameters
- **Structured requests:** each intent has an allow-list (`orchestrator.INTENT_PARAMS`). Unknown parameters return `INVALID_INPUT` and are never silently ignored.
- **Tool parameters:** agents forward only named fields. The M8 schemas then validate types, enums, ranges and dates again.

## Blocked by design
| Attempt | Blocked by | Result |
|---|---|---|
| Tool names like `execute_sql`, `__import__`, `read_file` | the gateway (not in any list) | denied |
| Injection strings in keys | the engines (bound parameters) | `ENTITY_NOT_FOUND`; tables intact |
| Path-like input | M8 sanitiser | redacted in all responses |
| SQL, code, file paths, URLs, credentials, instruction overrides in text | parser | `UNSAFE_REQUEST` before any tool call |
