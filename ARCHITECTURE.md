# ARCHITECTURE (baseline — not yet implemented)

```
USER (chat UI / Power BI)
  │
  ▼
ORCHESTRATOR ── intent & scope detection ── guardrails (supported questions, data coverage)
  │
  ├── Market Trend Agent
  ├── Brand Performance Agent
  ├── Segment Agent
  ├── Scenario Agent
  └── Insight / QA Agent
  │        (function calling only)
  ▼
DETERMINISTIC TOOL LAYER  (API_SPEC.md)
  ├── SQL layer (DuckDB)          ├── Python analytics layer
  ├── Scenario engine             └── Data-quality layer
  ▼
STRUCTURED RESULTS (JSON: values + metadata + provenance)
  ▼
QA / RECONCILIATION  → pass / fail / caveats
  ▼
GROUNDED NATURAL-LANGUAGE RESPONSE (numbers quoted verbatim from results)
```

| # | Component | Responsibility | Status |
|---|---|---|---|
| 1 | User interface | Chat + result tables/charts; shows provenance and caveats | **Product UI (M11)**: `app/web` — design system, 8-area information architecture (Executive Overview, Market Intelligence + Segments, Brand & Portfolio, Company, Opportunity, Scenario, AI Analyst, Methodology & DQ), linked selections, drill-downs, SVG charts; served by `python/pci_app/server.py` (stdlib, 127.0.0.1). See docs/UI_UX_ARCHITECTURE.md |
| 1b | Tool API boundary | Whitelisted tools, JSON-Schema contracts, validation, structured errors, path scrubbing | **Implemented (M8)**: `python/pci_app/tools.py` (docs/TOOL_API.md); the interface future agents (M9) will call |
| 2 | Orchestrator | Classifies intent, resolves entities (company/brand/therapy/period) against dimension tables, rejects unsupported scope (e.g. geography), routes to one or more specialist agents | **Implemented (M9)**: `python/pci_agents/orchestrator.py`; provider-agnostic (`LLM_PROVIDER=NONE` → DETERMINISTIC DEMO MODE); docs/AGENTIC_ARCHITECTURE.md |
| 3 | Specialist agents | Select and parameterise tools, interpret structured outputs; no arithmetic on business metrics | **Implemented (M9)**: 5 specialists calling only the M8 ToolRegistry via a permissioned gateway (docs/AGENT_TOOLS.md) |
| 4 | Deterministic tools | Versioned, typed, tested functions (API_SPEC.md) returning structured JSON | **Descriptive analytics implemented (M4)**: `python/pci_analytics/api.py` (9 functions); scoring/scenario TBD (M6–M7) |
| 5 | SQL layer | DuckDB views over Parquet: `pack`, `fact_pack_month`, `pack_snapshot`, `pack_price_month` + dimension views (`sql/views.sql`, `docs/DATA_MODEL.md`) | **Data layer (M3) + metric layer (M4)**: `periods.sql`, `entities.sql`, `metrics.sql`, `domains.sql` (docs/SQL_ANALYTICS.md) |
| 6 | Python analytics layer | Growth, share, contribution, ranking, trend, segmentation, cross-checks vs SQL | **Implemented (M5)**: independent engine (`periods`, `engine`, `metrics`, `rankings`), parity API `PyCommercialAnalytics`, cross-validation framework `validation.py` (docs/PYTHON_ANALYTICS.md) |
| 7 | Data-quality layer | Re-runs baseline checks and reconciliation rules on each load; exposes `get_data_quality_status()` | **Automated as pytest suites (M3, 133 tests)**; runtime API TBD (M8) |
| 7b | Opportunity scoring | Explainable 0–100 score, statuses, matrix, sensitivity | **Implemented (M6)**: `opportunity.py` + `opportunity_config.py` (canonical, Python only; docs/OPPORTUNITY_SCORING.md) |
| 8 | Scenario engine | Deterministic what-if (price, volume, share shifts) with explicit assumptions | **Implemented (M7)**: `scenario.py` (canonical, Python only; SCN-1.0.0; docs/SCENARIO_ENGINE.md) |
| 9 | QA / reconciliation | Checks every agent answer: numbers in text ⊆ numbers in tool results; totals reconcile to market; period/filters echoed | **Deterministic QA implemented (M9)**: `python/pci_agents/qa.py` (11 checks; failures withhold the answer) |
| 10 | Evaluation layer | Golden question set, tool-routing accuracy, numeric-faithfulness, refusal correctness | **Initial suite (M9)**: 28 deterministic cases (docs/AGENT_EVALUATION.md); extended in M10 |
| 11 | Power BI layer | Star-schema semantic model over the same processed Parquet; DAX measures reproduce the SQL definitions; opportunity scores imported from the canonical engine; M7 scenario formulas | **Implemented (M12)**: generated PBIP (`python/pci_powerbi/`, `dashboards/PCI_Commercial_Intelligence.pbip`), reconciled field-by-field against the SQL/Python engines (docs/POWER_BI_ARCHITECTURE.md) |

## Data flow
`<PRIVATE_WORKBOOK_PATH>` (read-only) → one-time streamed extract (`python/pci_data/build_processed.py`) → `data/processed/*.parquet` + `_manifest.json` (git-ignored) → DuckDB in-memory views (`pci_data.db.connect()`) → tools → agents / Power BI. Details: `docs/DATA_PIPELINE.md`.
Public repo path (M13): `python -m pci_synthetic.generate` → `data/synthetic/` (same schema and grain, fictional, structure-only); `PCI_DATASET=synthetic` points every consumer at it (`pci_data.schema`). See `docs/SYNTHETIC_DATA.md`.

## Design constraints
- Single process, local, no Docker; one dev server.
- LLM never receives raw rows — only tool outputs (aggregates) and schema metadata.
- Every tool response includes: `metric`, `filters`, `period`, `unit`, `source_version`, `dq_status`.
