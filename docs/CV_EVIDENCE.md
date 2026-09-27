# CV EVIDENCE — claims mapped to proof

Each claim below can be verified in this repository. Claims that are **not** supported are listed at the end and must not be made.

| # | CV-safe claim | Evidence (file / test / report) |
|---|---|---|
| 1 | Built a validated analytical data layer from a 105k-pack × 203-column licensed secondary-sales audit (streamed, hash-verified, reconciled to source totals) | `python/pci_data/build_processed.py`, `tests/test_reconciliation.py` (49), `docs/DATA_PIPELINE.md` |
| 2 | Implemented pharma commercial metrics (Month / calendar YTD / MAT growth, share, contribution, evolution index, deterministic ranks) in SQL with NULL-safe growth semantics | `sql/metrics.sql`, `sql/periods.sql`, `tests/test_sql_analytics.py` (176) |
| 3 | Cross-validated SQL against an independently written Python engine field by field | `python/pci_analytics/{engine,metrics,validation}.py`, `tests/test_python_sql_crosscheck.py` (80) |
| 4 | Designed an explainable, versioned opportunity score (percentile evidence, insufficient-evidence rules, sensitivity analysis) | `docs/OPPORTUNITY_SCORING.md`, `python/pci_analytics/opportunity.py`, `tests/test_opportunity.py` (38) |
| 5 | Built a deterministic what-if scenario engine with validated assumptions and a price/volume decomposition (not a forecast) | `docs/SCENARIO_ENGINE.md`, `tests/test_scenario.py` (48, including 10 fault injections) |
| 6 | Designed a governed agentic layer (orchestrator, specialist agents, permissioned tool gateway, 16 deterministic QA checks) where answers are withheld if a number doesn't trace to a tool result | `python/pci_agents/`, `docs/AGENTIC_ARCHITECTURE.md`, `tests/test_agents.py` |
| 7 | Evaluated the agents with a 75-case suite (24 adversarial) plus negative controls; 100% routing, tool selection, QA and numeric provenance | `evaluation/reports/AGENT_EVAL_REPORT.md`, `docs/EVALUATION_METHODOLOGY.md` |
| 8 | Integrated an optional Gemini provider that only routes (synthetic data only; deterministic tools compute; mocked-model tests prove it cannot add metrics) | `python/pci_llm_providers/gemini.py`, `tests/test_gemini_provider_phase3.py` |
| 9 | Built a local commercial-intelligence web app (8 areas, per-panel source footers, an opportunity evidence drawer, explicit insufficient/unsupported states) over a whitelisted tool API | `app/web/`, `python/pci_app/`, `docs/UI_UX_ARCHITECTURE.md`, `tests/test_ui_m11.py`, `tests/test_app.py` |
| 10 | Generated a Power BI project (TMDL + PBIR, 157 measures, 9 pages) and reconciled it to the engines (83/83 cases, 5.2 M values, max relative difference 2.9e-11) | `python/pci_powerbi/`, `docs/POWER_BI_ARCHITECTURE.md`, `evaluation/reports/POWER_BI_RECONCILIATION*.md` |
| 11 | Created a deterministic, structure-only synthetic dataset with leakage tests so the whole product runs on public-safe data | `python/pci_synthetic/`, `docs/SYNTHETIC_DATA.md`, `tests/test_synthetic_*.py` |
| 12 | Put publication-safety guards in place (secrets, data files, paths, history) and portable configuration | `tests/test_publication_m14.py`, `docs/PUBLICATION_READINESS.md` |
| 13 | Maintained 878 automated tests and an end-to-end metric trace (answer → tool → SQL → independent Parquet → Python) | `scripts/run_tests_by_file.py`, `scripts/trace_metrics.py`, `docs/FINAL_AUDIT.md` |

## Suggested CV bullets (factual)
- Built an end-to-end pharma commercial-analytics product on a 105k-pack secondary-sales audit: a validated Parquet/DuckDB layer, SQL metrics cross-checked by an independent Python engine, an explainable opportunity score and a what-if scenario engine (878 automated tests).
- Designed a governed agentic-AI layer (orchestrator, permissioned tools, deterministic QA) in which every number in an answer must trace to a tested calculation; 100% routing and numeric provenance on a 75-case evaluation including adversarial prompts.
- Delivered a Power BI layer generated as code and reconciled to the engines (83/83 checks, 5.2 M values), plus a synthetic public dataset with leakage tests for safe demos.

## Claims NOT supported (do not make)
- Any client, employer, IQVIA/IMS or Veeva impact; revenue or commercial uplift; production deployment; real users.
- "Industry-standard" opportunity scoring; forecasting or price-elasticity capability.
- Live LLM evaluation results (not run); a Stitch/Antigravity redesign (not run).
- Real-market findings. Public figures come from the synthetic dataset.
