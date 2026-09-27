# EVALUATION RESULTS — consolidated (2026-09-27)

This page summarises results against `EVALUATION_PLAN.md`. Methodology is in `docs/EVALUATION_METHODOLOGY.md`, agent details in `docs/AGENT_EVALUATION.md` and `evaluation/reports/AGENT_EVAL_REPORT.md`, and the audit in `docs/FINAL_AUDIT.md`. Private-data results are reported as counts and tolerances only; no business values appear here.

## 1. Automated test suite
Run per file (`scripts/run_tests_by_file.py`) to bound memory. Result (final run on the public snapshot): **886 passed, 0 failed, 0 errors, 0 skipped** (578 s).

| Area | File | Tests |
|---|---|---|
| Source → processed reconciliation | test_reconciliation | 49 |
| Data model / data quality | test_data_model, test_data_quality | 52 + 32 |
| SQL metrics | test_sql_analytics | 176 |
| Python engine | test_python_metrics, _periods, _rankings, _validation | 27 + 21 + 8 + 18 |
| SQL = Python parity | test_python_sql_crosscheck | 80 |
| Analytics API | test_api | 20 |
| Opportunity scoring (OPP-1.0.0) | test_opportunity | 38 |
| Scenario engine (SCN-1.0.0) | test_scenario | 48 |
| Tool API / server | test_app | 56 |
| Agents | test_agents | 82 |
| Agent evaluation (M10) | test_eval_m10 | 50 |
| UI contracts | test_ui_m11 | 31 |
| Power BI definition | test_powerbi_m12 | 22 |
| Synthetic generation and parity | test_synthetic_m13 | 22 |
| Synthetic leakage | test_synthetic_leakage_m13 | 19 |
| Gemini contract package (offline) | test_llm_eval_phase2 | 15 |
| Gemini provider (mocked) | test_gemini_provider_phase3 | 11 |
| Publication safety | test_publication_m14 | 9 |
| **Total** | | **886** |

The earlier M15 run had 878 tests; commit `470be18` added 7 offline retry-handling tests to `test_llm_eval_phase2`, and the public snapshot adds 1 publication test (the private workbook is never named).

## 2. Numeric correctness
| Check | Result |
|---|---|
| Processed layer vs source totals and source MAT/YTD/price fields | PASS (test_reconciliation) |
| SQL vs independent Python, period × entity × scope matrix | PASS, private and synthetic |
| Power BI vs SQL/Python, private | 83/83 cases, 5,195,694 values, max relative difference 2.9e-11 |
| Power BI vs SQL/Python, synthetic | 83/83 cases |
| Power BI visual queries | 88/88 run (private and synthetic) |
| End-to-end metric traces (synthetic) | 3/3 agree to ≤1e-9, QA passed |

## 3. Agent evaluation (M10, deterministic mode)
75 cases, 24 of them adversarial, plus negative controls and fault injection.

| Metric | Result |
|---|---|
| Intent routing | 100% |
| Tool selection | 100% |
| QA pass / block correctness | 100% |
| Numeric provenance (answer numbers traced to tool fields) | 334/334 |
| Refusals (geography, channel, forecast, unsafe) | 100%, before any tool call |

## 4. LLM provider (Gemini)
| Item | Result |
|---|---|
| Contract package, offline dry run | 19/19 tool-routed cases resolved against local tools |
| Provider with mocked model | 11/11: synthetic-only gate, header key, refusals before the model, allow-listed params, every value re-resolved at its source path |
| **Live model evaluation** | **PARTIAL / NOT COMPLETE DUE TO FREE-TIER DAILY QUOTA** (free tier, `gemini-3.7-flash`, synthetic only). 2/26 cases executed, 2/2 passed (routing, function calling, 8-field contract, numeric grounding). 24/26 were blocked by HTTP 429 (20 requests/day) or 503. Refusal and hallucination cases were not tested live. Record: `docs/GEMINI_VALIDATION.md` |

## 5. UX
- Browser QA at 5 widths (M11).
- Synthetic badge and live data-quality view verified on synthetic data.
- Stitch/Antigravity: Stitch used as a visual reference only; Antigravity implemented a CSS-only restyle. Antigravity reported five-width browser QA (1440/1366/1024/768/375) to the owner; Claude's independent check was a partial smoke test (synthetic data: Overview at desktop width, Scenario and AI Analyst at 375 px; no horizontal scroll, no console errors, local requests only) (`docs/UI_STITCH_ANTIGRAVITY_HANDOFF.md`).

## 6. Known gaps
- Live LLM evaluation.
- Visual redesign.
- The leakage and private reconciliation tests need the licensed data locally.
- Git history requires an orphan snapshot before publication (`docs/PUBLICATION_READINESS.md`).
