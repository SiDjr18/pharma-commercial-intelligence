# FINAL AUDIT (M15) — 2026-09-27

A factual audit from repository evidence. Ratings: **PASS / PARTIAL / FAIL / NOT TESTED**. No overall score is given.

Baseline:
- `main` at the M14 commit plus this audit.
- Full suite **878 passed, 0 failed, 0 errors, 0 skipped** at the audit, run per file with `scripts/run_tests_by_file.py`, private dataset. Final run on the public snapshot: **886 passed, 0 failed, 0 errors, 0 skipped** (7 Gemini retry tests and 1 publication test added).
- Power BI reconciliations are M12 (private) and M13 (synthetic).

| # | Area | Rating | Evidence | Gaps |
|---|---|---|---|---|
| 1 | Architecture | PASS | ARCHITECTURE.md layers are implemented. One source of truth (processed Parquet → SQL, with an independent Python engine). Agents reach data only via ToolGateway → ToolRegistry (static test). | — |
| 2 | Data pipeline | PASS | M3 streamed build with source hash and control totals; `test_reconciliation.py` (49) re-reads the source and proves the processed layer reproduces it | The source re-read needs the licensed file (private only) |
| 3 | SQL | PASS | `sql/*.sql`; `test_sql_analytics.py` (176) with fixture and real-data checks | — |
| 4 | Python | PASS | Independent engine; `test_python_*` (74) | — |
| 5 | SQL/Python parity | PASS | `test_python_sql_crosscheck.py` (80, private); the full M5 matrix re-run on synthetic data in `test_synthetic_m13.py`; three-way SQL = Python = Power BI case in both reconciliations | — |
| 6 | Opportunity scoring | PASS | OPP-1.0.0, one canonical implementation; fingerprint `ac4362f187f25004` pinned; `test_opportunity.py` (38); imported unchanged into Power BI (reconciled) | Weights are documented methodology assumptions, not empirical estimates |
| 7 | Scenario engine | PASS | SCN-1.0.0; `test_scenario.py` (48) including 10 fault injections; DAX reproduction reconciled (14 cases private, 14 synthetic) | Integer-percent grids in Power BI |
| 8 | Agent routing | PASS | M9/M10: 75-case evaluation, routing 75/75; Phase 3 `DATA_QUALITY` intent; geography, channel, forecast and unsafe requests refused before any tool call | Deterministic grammar only unless a live LLM is configured |
| 9 | Function/tool grounding | PASS | QA numeric provenance 334/334 (M10); `tests/test_gemini_provider_phase3.py` re-resolves every finding value at its source path; metric traces below | — |
| 10 | QA / evaluation | PARTIAL | M10 evaluation 100% on every metric (evaluation/reports/AGENT_EVAL_REPORT.md); Phase 2 offline package (26 cases, dry run 19/19) | **Live Gemini evaluation PARTIAL / NOT COMPLETE DUE TO FREE-TIER DAILY QUOTA**: 2/26 executed and passed; 24 blocked by HTTP 429/503; refusal/adversarial not tested live |
| 11 | Application UX | PARTIAL | M11 UI (8 areas, all states, browser QA at 5 widths); Phase 3 synthetic badge and live DQ view browser-verified on synthetic data; `test_ui_m11.py` (31) | Stitch used as a visual reference; Antigravity CSS-only restyle done (post-M16). Antigravity reported five-width browser QA (1440/1366/1024/768/375) to the owner; Claude's independent check was a partial smoke test (synthetic data: Overview at desktop width, Scenario and AI Analyst at 375 px; no horizontal scroll, no console errors, local requests only). |
| 12 | Power BI | PASS | Private: 83/83 cases, 5.2 M values, 88/88 visual queries. Synthetic: 83/83, 88/88. Drift test; top-N performance ≤ 2.5 s per visual query | Desktop only (no Service publishing, by design); `.pbix` never produced |
| 13 | Synthetic / public-data safety | PASS | Structure-only generator that reads no data file (static test); 19 leakage tests (labels, codes, row vectors, value triples, aggregate shape); 3 real collisions found and fixed | Leakage tests need the private layer (local only) |
| 14 | Security | PARTIAL | `test_publication_m14.py`: no secrets, data files, large files or profile paths in the tree **or history**; keys only from the environment in a header; external model synthetic-only (tested) | **History contains real aggregates (commit 5319019).** Mitigation: publish an orphan snapshot (documented) |
| 15 | Documentation | PASS | Per-milestone docs; PROJECT_STATUS and IMPLEMENTATION_PLAN current; this audit | Portfolio set completed in M16 |
| 16 | Storage (local drives) | PARTIAL | Project, venv, caches, synthetic data (12 MB) and exports on the project drive; scratch and logs in a dedicated scratch folder outside the repository (6 MB) | Power BI Desktop left **10 engine workspace folders (415 MB) on C:** (application-managed; 9 from private sessions). Deletion is blocked by the tool guard; the user can remove them (command in PROJECT_STATUS risks) |
| 17 | GitHub readiness | PARTIAL | Publish/exclude lists, `.env.example`, guards (docs/PUBLICATION_READINESS.md) | Licence: MIT (added). Orphan-snapshot publication and push approval are pending with the owner |
| 18 | CV evidence | PASS (M16) | docs/CV_EVIDENCE.md: every claim maps to a file, test or report; no impact claims | — |

## Representative metric traces (synthetic dataset, `scripts/trace_metrics.py`)
Each trace follows the agent's answer → tool result field (recorded source path) → the tool's SQL → an **independent** re-query of the raw Parquet (no views or macros) → the **independent** Python engine. Output: `evaluation/reports/METRIC_TRACES_SYNTHETIC.json`.

| Question (AI Analyst, deterministic mode) | Value in answer | Tool → field | Independent re-computation | Agree | QA |
|---|---|---|---|---|---|
| market performance by therapy area MAT for 2024-05 | 1469.055… ₹ cr (top therapy area) | get_market_performance → rows[0].value_cur | Σ value_cr from Parquet, Jun 2023–May 2024; Python `entity_period` | yes (≤1e-9) | passed |
| market performance total month for 2024-05 | +11.26 % growth | get_market_performance → rows[0].value_growth_pct | May 2024 / May 2023 − 1 from Parquet | yes (≤1e-9) | passed |
| what if price +10% for product 1000101704 | 594.29 ₹ cr scenario value | run_scenario → scenario_result.value_cr | Parquet MAT value × 1.10 | yes (≤1e-9) | passed |

The same chain is proven at scale by the reconciliations (Power BI ↔ SQL ↔ Python) and by M10 numeric provenance (334/334).

## Open items (none block local use)
1. Live Gemini evaluation: partial (2/26). Finishing it needs further free-tier quota resets (docs/GEMINI_VALIDATION.md).
2. Stitch and Antigravity: done as a CSS-only restyle; full five-width browser QA was reported by Antigravity, and Claude's independent check was partial (docs/UI_STITCH_ANTIGRAVITY_HANDOFF.md).
3. Publication: orphan snapshot and push approval (docs/PUBLICATION_READINESS.md); licence is MIT.
4. Remove the Power BI Desktop workspace folders on C: (user action).
