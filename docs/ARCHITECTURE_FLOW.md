# Architecture flow

How a number travels from the data to the screen. Every box below maps to code in this repository. The system runs locally, with no cloud services. The public workflow uses the **synthetic dataset** only.

Diagram sources (Mermaid): [full architecture](ARCHITECTURE_FLOW.mmd) · [executive flow](EXECUTIVE_FLOW.mmd) · [agentic AI flow](AGENTIC_AI_FLOW.mmd).

## Full architecture

```mermaid
flowchart TB
    subgraph SRC["1 · Data sources"]
        direction LR
        PRIV["Private IMS Data<br/>licensed workbook, sheet DATA<br/>outside the repository · read-only"]
        SPEC["Synthetic spec<br/>pci_synthetic: spec, names, schema<br/>reads no data file"]
    end

    subgraph LAYER["2 · Processing / Local Data Layer"]
        direction LR
        BUILD["pci_data.build_processed<br/>streamed read-only extract · source controls ·<br/>sha256 before and after"]
        PPQ[("Local Processed Data Layer<br/>Parquet at the private data root<br/>git-ignored")]
        GEN["pci_synthetic.generate<br/>seeded · pinned fingerprint"]
        SPQ[("Synthetic Public Dataset<br/>data/synthetic Parquet")]
        SWITCH{"PCI_DATASET<br/>private (default) or synthetic"}
        DUCK["pci_data.db<br/>DuckDB views over Parquet · no data copy"]
    end

    subgraph AN["3 · Deterministic Analytics (numerical source of truth)"]
        direction LR
        SQL["SQL engine<br/>sql/*.sql macros · CommercialAnalytics API"]
        PY["Independent Python engine<br/>pyarrow · no SQL"]
        XV["Cross-validation<br/>SQL = Python, field by field"]
        OPP["Opportunity scoring OPP-1.0.0<br/>canonical Python · descriptive, not a forecast"]
        SCN["Scenario engine SCN-1.0.0<br/>what-if arithmetic on an observed baseline"]
    end

    subgraph TL["4 · Tool / Function Layer"]
        REG["pci_app.tools.ToolRegistry<br/>15 whitelisted tools · JSON-schema input validation ·<br/>structured, scrubbed errors · no raw rows, SQL or file access"]
    end

    subgraph AGT["5 · Agent / Orchestration"]
        direction LR
        PROV["LLMProvider<br/>NONE (default): deterministic grammar<br/>GEMINI (optional): routing only, synthetic only"]
        ORCH["Orchestrator"]
        AGS["6 specialist agents"]
        GW["ToolGateway<br/>per-agent permissions"]
    end

    subgraph GOV["6 · QA / Governance"]
        direction LR
        REF["Deterministic refusals<br/>before any tool call"]
        QA["Deterministic QA engine<br/>provenance and consistency checks<br/>failed drafts withheld"]
    end

    subgraph WEB["7 · Local Web App (127.0.0.1, Python standard library)"]
        direction LR
        PAGES["Analytics pages<br/>app.js → /api/tools/*"]
        ANALYST["AI Analyst<br/>agent.js → /api/agent"]
    end

    subgraph PBI["Power BI path (local, Desktop only)"]
        direction LR
        EXP["pci_powerbi.export<br/>copies canonical opportunity scores"]
        BLD["pci_powerbi.build<br/>generated PBIP: TMDL model, DAX mirrors sql/metrics.sql,<br/>Power Query folder parameters"]
        DESK["Power BI Desktop<br/>imports the Parquet layer"]
        REC["pci_powerbi.reconcile<br/>visual queries vs SQL and Python"]
    end

    PRIV --> BUILD --> PPQ
    SPEC --> GEN --> SPQ
    PPQ --> SWITCH
    SPQ --> SWITCH
    SWITCH --> DUCK
    DUCK --> SQL
    SWITCH -- "pyarrow" --> PY
    SQL --- XV --- PY
    PY --> OPP
    PY --> SCN
    SQL --> REG
    OPP --> REG
    SCN --> REG
    PROV --> ORCH --> AGS --> GW --> REG
    PROV -. "refusal" .-> REF
    ORCH --> QA
    REG --> PAGES
    QA --> ANALYST
    REF --> ANALYST
    OPP --> EXP --> DESK
    BLD --> DESK
    PPQ --> DESK
    DESK --> REC
    REC -. "compares with" .-> SQL
    REC -. "compares with" .-> PY
```

## Layers

| # | Layer | What it does | Code |
|---|---|---|---|
| 1 | Data sources | **Private IMS Data**: a licensed secondary-sales workbook. It stays outside the repository, is opened read-only and is never committed; its location is set locally with `PCI_SOURCE_PATH` (placeholder `<PRIVATE_WORKBOOK_PATH>`). **Synthetic Public Dataset**: fictional, structure-only data generated from a committed spec that reads no data file. | `python/pci_data/schema.py`, `python/pci_synthetic/` |
| 2 | Processing / Local Data Layer | A one-time streamed extract writes the private Parquet layer (git-ignored, under `<PRIVATE_DATA_ROOT>`) with source-side control totals and a source sha256 checked before and after. `PCI_DATASET` selects the private (default) or synthetic layer; DuckDB serves views over the Parquet files without copying data. | `python/pci_data/build_processed.py`, `python/pci_data/db.py`, `sql/views.sql` |
| 3 | Deterministic Analytics | The **numerical source of truth**. SQL macros compute every metric; an independent Python engine re-implements them without SQL, and a cross-validation framework checks the two field by field. Opportunity scoring and scenarios exist only in Python. The score is descriptive (not a forecast); scenarios are what-if arithmetic on an observed baseline. | `sql/`, `python/pci_analytics/` |
| 4 | Tool / Function Layer | `ToolRegistry`: 15 whitelisted tools with input contracts, validated before any engine call, and structured errors with paths scrubbed. There are no endpoints for raw rows, SQL, code execution or files. | `python/pci_app/tools.py` |
| 5 | Agent / Orchestration | The provider plans intent + parameters. The orchestrator routes to specialist agents, which reach tools only through the `ToolGateway` (per-agent permissions, deny by default). The orchestrator never calls tools or calculates. | `python/pci_agents/` |
| 6 | QA / Governance | Deterministic refusals happen before any tool call. The QA engine validates every draft (numeric provenance, units, period/entity consistency, tool scope, scenario-not-forecast, unsupported claims, disambiguation, …); a failing draft is withheld as `QA_FAILED`. | `python/pci_agents/parser.py`, `python/pci_agents/qa.py` |
| 7 | Web App | Local server on 127.0.0.1 (Python standard library). Analytics pages call only `/api/tools/*`; the AI Analyst calls only `/api/agent`. No external assets; synthetic-data badge. | `python/pci_app/server.py`, `app/web/` |
| — | Power BI | Canonical opportunity scores are exported to Parquet; a generator writes the PBIP (TMDL model whose DAX mirrors `sql/metrics.sql`, Power Query folder parameters, report). Power BI Desktop runs locally; `reconcile` compares its visual queries with the SQL and Python engines. Scores are imported, never recomputed in DAX. | `python/pci_powerbi/`, `dashboards/` |

## Agents and their tools

Permissions come from `python/pci_agents/gateway.py`. The orchestrator and the QA engine hold no tools.

| Agent | Tools it may call |
|---|---|
| MarketTrendAgent | `get_market_performance`, `get_market_trends` |
| BrandProductAgent | `find_products`, `get_brand_performance`, `get_brand_growth`, `get_brand_share` |
| CompanySegmentAgent | `get_company_performance`, `get_therapy_performance`, `get_segment_analysis` |
| OpportunityAgent | `get_opportunity_scores`, `get_opportunity_detail` |
| ScenarioAgent | `get_scenario_baseline`, `run_scenario` |
| DataQualityAgent | `get_data_quality_status` |

The web pages also call `get_application_metadata`, the 15th tool.

## Agentic AI flow

Deterministic, tool-grounded orchestration with refusal and guardrails. Source: [AGENTIC_AI_FLOW.mmd](AGENTIC_AI_FLOW.mmd).

```mermaid
flowchart TB
    Q["User question<br/>AI Analyst page → POST /api/agent"]

    subgraph PLAN["Planning (provider)"]
        direction TB
        GUARD{"Deterministic guardrails<br/>geography · channel · SSA/HSA/DSA ·<br/>unsupported analysis · unsafe request"}
        PROV["Provider plan: intent + parameters<br/>LLM_PROVIDER=NONE (default): rule-based grammar<br/>optional GEMINI: routing only, synthetic data only"]
    end

    REFUSE["Refusal with reason<br/>UNSUPPORTED_* · UNSAFE_REQUEST · UNRECOGNIZED_REQUEST<br/>no tool is called"]
    VALID{"Intent and parameters<br/>on the allow-lists?"}
    ORCH["Orchestrator<br/>routes and assembles · never calls tools · never calculates"]

    subgraph AG["Specialist agents"]
        direction LR
        A1["MarketTrendAgent"]
        A2["BrandProductAgent"]
        A3["CompanySegmentAgent"]
        A4["OpportunityAgent"]
        A5["ScenarioAgent"]
        A6["DataQualityAgent"]
    end

    GW{"ToolGateway<br/>per-agent permissions · deny by default"}
    DENY["TOOL_NOT_PERMITTED"]
    REG["ToolRegistry<br/>15 whitelisted tools · input schema validation · structured errors"]
    ENG["Deterministic analytics<br/>SQL engine · Python opportunity and scenario engines"]
    AMB{"Ambiguous entity?"}
    CLAR["Clarification<br/>AMBIGUOUS_ENTITY · the user chooses · never auto-selected"]
    COMP["Compose answer<br/>template text from tool result fields only"]
    QAE{"Deterministic QA engine<br/>numeric provenance · units · period and entity ·<br/>tool scope · scenario-not-forecast · unsupported claims"}
    OK["Answer with claim-level provenance<br/>claim → tool → result field → value"]
    FAIL["QA_FAILED<br/>draft withheld"]

    Q --> GUARD
    GUARD -- "refused" --> REFUSE
    GUARD -- "allowed" --> PROV
    PROV --> VALID
    VALID -- "no" --> REFUSE
    VALID -- "yes" --> ORCH
    ORCH --> AG
    AG --> GW
    GW -- "not permitted" --> DENY
    GW -- "permitted" --> REG
    REG --> ENG
    ENG --> AMB
    AMB -- "yes" --> CLAR
    AMB -- "no" --> COMP
    COMP --> QAE
    QAE -- "pass" --> OK
    QAE -- "fail" --> FAIL
```

**Guardrails in the code:**
- **Refused before any tool call:** geography, channel, SSA/HSA/DSA splits (their meaning is unconfirmed), unsupported analyses (forecasting, elasticity, prescribers, promotions) and unsafe requests (instruction overrides, code, SQL, file or raw-data access).
- **Parameters:** checked against per-intent allow-lists.
- **Tool access:** agents are limited to their own tools (above).
- **Ambiguous products:** the user must choose; nothing is auto-selected.
- **Answer text:** composed from tool fields.
- **QA:** checks every number against a tool result before release.

**Optional Gemini provider (`LLM_PROVIDER=GEMINI`):**
- **What it does:** proposes intent and parameters only; it never writes numbers or answer text.
- **Restrictions:** requires `PCI_DATASET=synthetic` and an API key from the environment. Deterministic refusals run before the model is asked.
- **Default:** `NONE` (deterministic demo mode, no network).
- **Live evaluation:** **partial** (2 of 26 cases completed on the free tier; see [GEMINI_VALIDATION.md](GEMINI_VALIDATION.md)).

## Private vs synthetic boundary

| | Private | Synthetic (public) |
|---|---|---|
| Source | Licensed workbook at `<PRIVATE_WORKBOOK_PATH>` (never in the repository) | Committed spec and generator; no data file read |
| Processed layer | Parquet under `<PRIVATE_DATA_ROOT>` (git-ignored) | `data/synthetic/` (regenerated, fingerprint pinned by tests) |
| Selected by | `PCI_DATASET` unset or `private` | `PCI_DATASET=synthetic` |
| External model (Gemini) | Not allowed: the provider refuses to start and the app falls back to deterministic mode | Allowed, routing only |
| Published | Schema, methodology, value-free quality metadata | Code, fingerprint, synthetic evaluation outputs |

## Related documents
[ARCHITECTURE.md](../ARCHITECTURE.md) · [AGENTIC_ARCHITECTURE.md](AGENTIC_ARCHITECTURE.md) · [TOOL_API.md](TOOL_API.md) · [DATA_PIPELINE.md](DATA_PIPELINE.md) · [SQL_ANALYTICS.md](SQL_ANALYTICS.md) · [PYTHON_ANALYTICS.md](PYTHON_ANALYTICS.md) · [OPPORTUNITY_SCORING.md](OPPORTUNITY_SCORING.md) · [SCENARIO_ENGINE.md](SCENARIO_ENGINE.md) · [POWER_BI_ARCHITECTURE.md](POWER_BI_ARCHITECTURE.md) · [SYNTHETIC_DATA.md](SYNTHETIC_DATA.md) · [GEMINI_VALIDATION.md](GEMINI_VALIDATION.md)
