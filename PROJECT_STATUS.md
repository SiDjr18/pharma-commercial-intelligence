# PROJECT STATUS

## CURRENT MILESTONE
**P1 isolation fixes — COMPLETE (2026-09-28), not committed, not pushed.** Two-mode architecture: public/synthetic by default,
private IMS only with `PCI_DATASET=ims` + an external `PCI_IMS_DATA_DIR`. M1–M16 complete. Remaining items are owner actions (below).

## COMPLETED
- **M1–M8:** foundation, profiling, data layer, SQL analytics, Python cross-validation, opportunity scoring (OPP-1.0.0), scenario engine (SCN-1.0.0), local application and tool API (TOOLS-1.0.0)
- **M9** Governed, provider-agnostic agent layer (docs/AGENTIC_ARCHITECTURE.md)
  - Orchestrator plus 5 specialist agents (MarketTrend, BrandProduct, CompanySegment, Opportunity, Scenario) and an Insight/QA engine
  - Agents reach analytics only via a permissioned `ToolGateway` → M8 `ToolRegistry` (13 tools, each owned by one agent; deny by default)
  - `LLMProvider` interface; `LLM_PROVIDER=NONE` (default) = DETERMINISTIC DEMO MODE (rule-based grammar, template text, clearly labelled); external providers not connected
  - Deterministic QA (11 checks): numeric provenance, units, traceable text numbers, period/entity, tool provenance, market definition, unsupported claims, insufficient evidence, scenario-not-forecast, methodology, disambiguation. Failures withhold the answer (QA_FAILED).
  - Brand disambiguation (AMBIGUOUS_ENTITY), unsafe/unsupported refusals before any tool call, per-intent parameter allow-lists
  - 28-case deterministic evaluation suite (28/28 pass)
  - UI: `/agent` page linked from the M8 NL tab; routes `GET /api/agent/status`, `POST /api/agent`

- **M10** Systematic evaluation (docs/EVALUATION_METHODOLOGY.md, evaluation/reports/AGENT_EVAL_REPORT.md)
  - `python/pci_eval/`: 75-case structured dataset (31 categories, 24 adversarial), 24 negative controls (hallucination, disclaimer, fault injection, rogue agents), 6 tool-failure simulations, 3× repeatability, independent provenance re-execution, routing error matrix, latency benchmark, JSON/CSV/MD reports
  - QA extended with 5 checks: tool_scope, requested_entity, evidence_complete, result_well_formed, published_methodology. Governed messages are excluded from wording scans; QA is defensive and never bypassed.
  - Multi-turn clarification via an HMAC-signed, request-scoped continuation token (no server memory); claim → tool → field provenance in responses; per-response timing
  - Parser red-team hardening: raw data/database/Excel access, path traversal, URLs, guardrail overrides, forced selection, fabrication and previous-conversation requests
  - Results: every metric 100% (see denominators in the report). First run 73/75 cases and 21/24 controls, fixed at the root cause.

- **M11** Premium UI/UX + productization (docs/UI_UX_ARCHITECTURE.md)
  - One design system (`app/web/style.css`: tokens, typography, panels, tables, badges, states, charts, drawer, tooltip) shared by the app and the AI Analyst; no framework, no external assets, no new dependency
  - 8-area information architecture: Executive Overview, Market Intelligence (+ Segments & mix), Brand & Portfolio, Company Intelligence, Opportunity Intelligence, Scenario Planning, AI Analyst, Methodology & Data Quality; hash routing with bookmarkable filters/selections, breadcrumbs, global period (month + Month/YTD/MAT)
  - Linked selections and drill-downs across pages; sortable/keyboard-navigable tables; SVG charts (trend with prior-year comparison, contribution/diverging bars, mix shift, opportunity matrix with log / symmetric-log axes, score decomposition); evidence drawer
  - Explicit states: skeleton loading, empty, structured error, n/a with reason, insufficient evidence (never zero), unsupported, ambiguous (never auto-selected), rejected assumption (never corrected)
  - AI Analyst UX over the unchanged M9/M10 agents: pipeline view (question → orchestrator → agent → permission-checked tool → engine → QA → answer), value provenance chips, clarification buttons (signed continuation), audit sections
  - Server: `/favicon.ico` → 204; `/api/agent/status` adds `supported_requests` (demo grammar text). ToolRegistry, engines, agents, QA and permissions unchanged
  - Browser QA at 1440/1366/1024/768/375 px: every page, filters, drill-downs, scenario valid/rejected/share, opportunity drawer (scored + insufficient), shared-brand clarification, AI Analyst refusals; no horizontal page overflow; no clipped tables at 1024 px; defects found were fixed

- **M12** Power BI layer (docs/POWER_BI_ARCHITECTURE.md)
  - Generated Power BI Project (`dashboards/PCI_Commercial_Intelligence.pbip`: TMDL semantic model + PBIR report + M11-derived theme) from `python/pci_powerbi/` (never hand-edited; drift test)
  - Star schema over the SAME processed Parquet (fact 3.79M pack-months, Pack/Product/Market/Company/Period, disconnected Anchor/Basis/scenario parameters); single-direction many-to-one relationships only
  - 157 measures reproducing sql/periods.sql + sql/metrics.sql (Month/YTD/MAT, growth BLANK when prior <= 0, market/national/therapy-area shares, contribution, EI, deterministic ranks with byte-order tie-break), trends, top-N display selectors
  - Opportunity scores imported verbatim from the canonical OPP-1.0.0 engine (`-m pci_powerbi.export`, 30 anchor/basis combinations); never recomputed in DAX
  - M7 scenarios (all 5 types) with SCN-1.0.0 formulas; assumptions only from allowed grids; missing/extra/ambiguous/out-of-grid rejected, never corrected; OBSERVED/CALCULATED labels; "SCENARIO ANALYSIS — NOT A FORECAST"
  - 7 pages + 2 hidden drill-through pages (Product, Company); synced anchor/basis slicers, matrix drill, cross-filtering, Top-500 visual filters on product tables
  - Live reconciliation against the SQL/Python engines through Power BI Desktop's local engine (`-m pci_powerbi.reconcile`): **83/83 cases pass, 5,195,694 values, 512,103 NULL/BLANK checks, max relative difference 2.9e-11, 88/88 visual queries**; 6 discrepancies found and fixed in Power BI (never in the engines): see docs/POWER_BI_ARCHITECTURE.md §9
  - Visual QA in Power BI Desktop 2.157 (local captures only, git-ignored; no real-data screenshots published)

- **M13** Synthetic public dataset (docs/SYNTHETIC_DATA.md)
  - `python/pci_synthetic`: deterministic generator SYN-1.0.0 (seed 20260927), structure-only, fictional names/codes, invented numbers. It reads no data file (static test). 4,000 packs × 36 months (144k fact rows) in ~1 s; fingerprint `a28d3496…` committed in `data/synthetic/SYNTHETIC_FINGERPRINT.json`; the Parquet is regenerated, not committed
  - Dataset switch `PCI_DATASET` ∈ {private (default), synthetic} in `pci_data.schema`; engines, app, agents and Power BI follow it. Private behaviour is unchanged (809 private tests pass with it unset)
  - Tests: `tests/test_synthetic_m13.py` (20: determinism, schema parity, grain/manifest, functional dependencies, DQ quirks, snapshot/price rules, full unchanged M5 SQL=Python matrix, API, opportunity, scenarios, switch, Power BI build guard) and `tests/test_synthetic_leakage_m13.py` (19, local-only: no shared labels/codes/row vectors/value triples/aggregate shapes)
  - Leakage findings fixed in the generator: 4 short brand names coincided with real brands (now ≥ 8 letters); 4 PFC codes overlapped the real range (now 10-digit codes); vector test tightened to 1e-9 after one single-month chance coincidence at 1e-6
  - Application + tool API + governed agents on synthetic data (port 8766): 7 intents routed to deterministic tools, QA passed, geography refused
  - Power BI: synthetic export, build (`dashboards/_synthetic/`, git-ignored; the committed private project is unchanged, tested), refresh 7.5 s, **reconciliation 83/83, 224,417 values, max rel diff 3.4e-11, 88/88 visual queries** (`evaluation/reports/POWER_BI_RECONCILIATION_SYNTHETIC.md`); synthetic footer relabelled "SYNTHETIC dataset"

- **Phase 2 — Gemini agent-contract validation:** offline package (`python/pci_llm_eval/`, 16 declarations, 26 cases, dry run 19/19, 15 tests incl. retry handling). **Live run PARTIAL / NOT COMPLETE DUE TO FREE-TIER DAILY QUOTA**: 2/26 cases executed on `gemini-3.7-flash`, both passed. 24 were blocked by HTTP 429/503. Refusal and adversarial cases were not tested live. Record in docs/GEMINI_VALIDATION.md.
- **Phase 3 — Optional Gemini provider + data-quality tool:** `LLM_PROVIDER=GEMINI` (opt-in, synthetic-only, env key in a header, routing only; deterministic refusals before the model; answers composed from tools; QA unchanged); `get_data_quality_status` tool, `DataQualityAgent`, `DATA_QUALITY` intent ('data quality status'); 15 registry tools (was 14). No geography tool (refused as UNSUPPORTED_GEOGRAPHY). Tests: `tests/test_gemini_provider_phase3.py` (11, mocked model, strict per-value provenance); pinned contract counts updated in test_app/test_agents.

- **M14** Security + GitHub prep (docs/PUBLICATION_READINESS.md)
  - Automated guards: no data files, secrets, large files, `.env`, user-profile paths or undocumented drive paths in the tracked tree or anywhere in history (`tests/test_publication_m14.py`, 8 tests)
  - Portability without changing private behaviour: `PCI_SOURCE_PATH`; profiling scripts derive paths from the project root; `pci_powerbi.build --data-root/--out` (cannot overwrite the committed project); `.env.example`; `scripts/run_tests_by_file.py` (memory-safe per-file runner)
  - Open for the owner: history contains real aggregates in commit 5319019, so publish an orphan-branch snapshot (commands documented), never the existing history; licence choice; push approval

- **M15** Final audit (docs/FINAL_AUDIT.md): 18 areas rated PASS/PARTIAL (no FAIL). PARTIAL: QA/evaluation (live Gemini partial, 2/26: free-tier quota), application UX (Stitch/Antigravity since done as a CSS-only restyle; five-width QA reported by Antigravity, Claude's independent check partial), security (real aggregates in history, mitigated by an orphan snapshot), storage (Power BI Desktop workspaces on C:), GitHub readiness (owner decisions). Three end-to-end metric traces (agent → tool → SQL → independent Parquet → Python) agree to 1e-9 with QA passed

- **M16** Portfolio documentation
  - README rewritten as the front page (synthetic quickstart, results, docs index, repository map, limitations)
  - docs/CASE_STUDY.md (problem → roadmap; findings explicitly synthetic), docs/EXECUTIVE_CASE_STUDY_SLIDES.md (7-slide spec), docs/CV_EVIDENCE.md (13 claims → file/test/report; unsupported claims listed), DEMO_GUIDE.md (10-minute synthetic walkthrough; all 8 AI Analyst questions verified), EVALUATION_RESULTS.md (consolidated counts/tolerances, no private values)
  - Existing methodology docs referenced, not duplicated. No client/employer impact, production or industry-standard claims.

- **Phase 4/5 — Stitch / Antigravity UI restyle (post-M16):** Stitch used as a visual reference only (export audited, never copied). Antigravity implemented a CSS-only restyle of `app/web/style.css`; the 28 Power BI-linked colour tokens are unchanged. Antigravity reported five-width browser QA (1440/1366/1024/768/375) to the owner; Claude's independent check was a partial smoke test (synthetic data: Overview at desktop width, Scenario and AI Analyst at 375 px; no horizontal scroll, no console errors, local requests only). Full regression (public snapshot): **886 passed, 0 failed, 0 errors, 0 skipped**. MIT LICENSE added.

- **P1 isolation fixes (2026-09-28)** (docs/DATA_ISOLATION.md, docs/PUBLICATION_GUARD.md, SECURITY.md):
  - `PCI_DATASET` unset/`synthetic` = public mode (default); `ims` = private mode with an explicit, validated external `PCI_IMS_DATA_DIR` (absolute, existing, outside the repo, not a parent, not via a link, not a drive root, not in a git work tree). Anything else fails closed; no fallback, no discovery (`python/pci_data/dataset.py`).
  - IMS-derived outputs (processed layer, Power BI exports, reconciliation/evaluation reports, profiles, caches, test logs) go under `PCI_IMS_DATA_DIR` (`schema.output_dir`, `assert_outside_repo`); `build_processed`/profiling are IMS-only; the synthetic generator refuses to overwrite another layer; the manifest must match the selected mode.
  - The existing IMS-derived files were moved (same-volume rename, hash-verified, no copy) from the repository to the external private directory; the source workbook was not touched.
  - Local server: Host allow-list (421), POST requires JSON and a same-origin Origin/Sec-Fetch-Site (415/403), loopback bind only, refusals drain the body (no Windows reset). Tool results capped: null/absent `top_n` = at most 2,000 rows (500 for product lists).
  - Publication guard: `.githooks/pre-commit` + `pre-push` → `scripts/publication_guard.py` (fail closed; path + reason only; blocks data files, private locations, secrets, drive/profile paths, configured private identifiers (never hard-coded; `PCI_SOURCE_PATH` stem, `PCI_IMS_DATA_DIR`, optional untracked `.git/info/pci-private-identifiers`), large/binary files and history not rooted at the public snapshot). Client-side hook: active once `core.hooksPath=.githooks` is set; a safety control, not a guarantee (`--no-verify` skips it). `.gitignore` hardened.
  - Tests: +86 `tests/test_isolation_p1.py`, +32 `tests/test_publication_guard.py`; licensed-data tests carry the `ims` marker and are skipped in public mode.

## IN PROGRESS
- **Live Gemini evaluation: PARTIAL / NOT COMPLETE DUE TO FREE-TIER DAILY QUOTA**: 2/26 executed and passed; 24 quota-blocked (free tier, 20 requests/day). Do not rerun before the quota resets; no paid tier. Record in docs/GEMINI_VALIDATION.md.

## NOT STARTED
- None (no work planned beyond M16). Owner decisions: orphan-snapshot publication and push approval (licence: MIT, added).

## BLOCKERS
- None.

## KNOWN RISKS / PRE-PUBLICATION ITEMS
- A stale app process from a previous session may still hold port 8765 (serving pre-M10 code); M11 browser QA used port 8766. Stop old `run_app.py` processes before demoing.
- **Git history cleanup before any publication (M14):** commit 5319019 contains real value shares and price percentiles in DATA_DICTIONARY.md history. They are removed from the working tree; history has not been rewritten (by instruction). P1: the pre-push guard refuses any history not rooted at the public snapshot, once enabled with `git config core.hooksPath .githooks`.
- Demo mode understands only the documented request grammar; refusal keywords are conservative.
- No real LLM is connected: answers are templated. The evaluation proves governance for the demo grammar, not free-form language understanding.
- M12: the committed PBIP keeps the local processed-data folder as its documented default parameter (`expressions.tmdl`); M14 added `pci_powerbi.build --data-root/--out` for portable builds (tested).
- M12: Power BI Desktop settings were not changed (including usage-data telemetry); turning telemetry off is the user's choice (Options → Global → Usage data).
- M12: live reconciliation needs Windows + Power BI Desktop; offline tests guard the definition only. Re-run `python -m pci_powerbi.reconcile` after any model change.
- Power BI Desktop left 10 engine workspace folders (~415 MB) under %USERPROFILE%\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces (C:); remove them with Desktop closed: `Get-ChildItem "$env:USERPROFILE\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces" -Directory -Filter "AnalysisServicesWorkspace_*" | Remove-Item -Recurse -Force`
- M12: real-data report captures must not be published; public screenshots wait for the M13 synthetic dataset.

## FILES CREATED (M13)
`python/pci_synthetic/{__init__,spec,names,generate}.py`, `python/pci_synthetic/processed_schema.json`, `data/synthetic/SYNTHETIC_FINGERPRINT.json`, `tests/test_synthetic_m13.py`, `tests/test_synthetic_leakage_m13.py`, `docs/SYNTHETIC_DATA.md`, `evaluation/reports/POWER_BI_RECONCILIATION_SYNTHETIC.md`

## FILES MODIFIED (M13)
`python/pci_data/schema.py` (dataset switch), `python/pci_analytics/api.py` (optional `manifest_path`), `python/pci_powerbi/{build,export,reconcile,report}.py` (dataset-aware paths/outputs/footer; private defaults unchanged), `.gitignore`, README.md, DATA_PROVENANCE.md, SECURITY.md, ARCHITECTURE.md, CLAUDE.md, docs/POWER_BI_ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, PROJECT_STATUS.md. SQL, engines, M12 measures/report and the 809 private tests are unchanged.

## FILES CREATED (M12)
`python/pci_powerbi/{__init__,model,report,theme,build,export,desktop,reconcile}.py`, `python/pci_powerbi/as_bridge.ps1`, `tests/test_powerbi_m12.py`, `docs/POWER_BI_ARCHITECTURE.md`, `evaluation/reports/POWER_BI_RECONCILIATION.md` (value-free summary), generated `dashboards/PCI_Commercial_Intelligence.pbip` + `.SemanticModel/` + `.Report/` (text definitions, no data). Local only at M12 (git-ignored): `data/processed/powerbi/` (canonical opportunity export), `evaluation/reports/powerbi_reconciliation.{json,csv,progress.jsonl}`, QA captures in `.cache/`. Since P1 (2026-09-28) these IMS outputs are written under `PCI_IMS_DATA_DIR` (`powerbi/`, `reports/`, `cache/`), outside the repository; the synthetic export goes to `data/synthetic/powerbi/`.

## FILES MODIFIED (M12)
`.gitignore` (`*.abf`, `**/.pbi/`, PBIP cache/local settings; reconciliation summary allowed), PROJECT_STATUS.md, IMPLEMENTATION_PLAN.md, README.md, ARCHITECTURE.md, CLAUDE.md. Engines (SQL, Python, opportunity, scenario), ToolRegistry, agents and app are **unchanged**.

## FILES CREATED (M11)
`docs/UI_UX_ARCHITECTURE.md`, `tests/test_ui_m11.py`

## FILES MODIFIED (M11)
`app/web/{index.html,app.js,style.css,agent.html,agent.js}` (redesign), `python/pci_app/server.py` (favicon 204, agent-status grammar, docstring), `tests/test_app.py` (asset assertion refined: loaded assets still exactly app.js + style.css; navigation links must be in-app routes), PROJECT_STATUS.md, IMPLEMENTATION_PLAN.md, README.md, ARCHITECTURE.md, CLAUDE.md, docs/APPLICATION.md, docs/AGENTIC_ARCHITECTURE.md. Engines, ToolRegistry, agents, QA and evaluation unchanged.

## FILES CREATED (M10)
`python/pci_eval/{__init__,__main__,cases,controls,runner}.py`, `tests/test_eval_m10.py`, `docs/EVALUATION_METHODOLOGY.md`, `evaluation/reports/AGENT_EVAL_REPORT.md` (JSON/CSV reports are local, git-ignored)

## FILES MODIFIED (M10)
`python/pci_agents/{parser,gateway,qa,orchestrator}.py` (hardening, 5 QA checks, continuation, provenance, timing, fail-safe), docs/AGENT_EVALUATION.md, PROJECT_STATUS.md, IMPLEMENTATION_PLAN.md, README.md. M3–M8 unchanged; all earlier tests pass unmodified.

## FILES CREATED (M9)
`python/pci_agents/{__init__,schemas,gateway,parser,providers,agents,qa,orchestrator,evaluation}.py`, `app/web/agent.html`, `app/web/agent.js`, `tests/test_agents.py`, `docs/AGENTIC_ARCHITECTURE.md`, `docs/AGENT_TOOLS.md`, `docs/AGENT_EVALUATION.md`

## FILES MODIFIED (M9)
`python/pci_app/server.py` (agent routes, `/agent` page, provider fallback; M8 routes unchanged), `app/web/index.html` (button on the M9 placeholder tab), `app/web/app.js` (button handler), `app/web/style.css` (pre styling), ARCHITECTURE.md, AGENT_SPEC.md, README.md, PROJECT_STATUS.md, IMPLEMENTATION_PLAN.md, CLAUDE.md.
M3–M7 engines and the M8 ToolRegistry are **unchanged**; all M8 tests pass unmodified.

## TESTS
**P1 (2026-09-28): IMS mode 1004 passed, 0 failed, 0 errors, 0 skipped; public mode 594 passed, 410 skipped (`ims`), 0 failed**; per-file sequential runs. Clean public clone (committed snapshot + P1 changes, no private files): 594 passed, 410 skipped, 0 failed. Pre-change baseline: 886 passed.
**M12 (2026-09-27): 809 passed, 0 failed, 0 errors, 0 skipped**: all 787 earlier tests plus 22 new `tests/test_powerbi_m12.py`, full mode (source re-read included).
**M13 (2026-09-27): 848 passed, 0 failed, 0 errors, 0 skipped** (809 private + 20 synthetic + 19 local leakage), per-file sequential run, 424 s.

- Executed one test file per process, sequentially (`.cache/regression/run_split.py`), in 414 s. No test or assertion was changed or skipped.
- Per file: agents 81 (34 s) · api 20 · app 56 · data_model 52 · data_quality 32 · eval_m10 50 (96 s) · opportunity 38 · powerbi_m12 22 · python_metrics 27 · python_periods 21 · python_rankings 8 · python_sql_crosscheck 80 (56 s) · python_validation 18 · reconciliation 49 (68 s, source re-read) · scenario 48 · sql_analytics 176 (76 s) · ui_m11 31.
- Slowest individual tests: test_eval_m10 `test_all_cases_pass` setup (~51 s, full M10 evaluation), `test_no_network_during_evaluation` (~17 s), rankings `test_rounding_matches_duckdb_round` (~8–11 s).
- **Stalled single-process run (stopped):** a monolithic `pytest` run stalled for about 2 h 40 m at test #407, `test_python_rankings.py::test_rank_parity_with_sql_on_real_ties`. That test normally takes 2–3 s.
  - The stalled process had its working set trimmed to 206 MB, one thread at 100% CPU and DuckDB threads idle, which is consistent with paging under system memory pressure.
  - The same 407-test prefix re-run in one process passed in ~3 min (peak ~2.3 GB private; 3.3 GB RAM free at the lowest point), so the stall did not reproduce.
  - Evidence is kept in `.cache/powerbi/pytest_full.log`, `.cache/powerbi/pytest_full_STOPPED.txt` and `.cache/regression/`.
  - Recommendation for this laptop: run the suite per file (or close memory-heavy apps first).
- **Power BI reconciliation** (`python -m pci_powerbi.reconcile`, Desktop 2.157):
  - 83/83 cases, 5,195,694 values, 512,103 NULL/BLANK checks, max relative difference 2.9e-11
  - 88/88 visual queries (1.7–2.5 s each)
- Earlier baseline (M11): 787 passed.

## DATA STATUS
P1: the processed IMS layer and all IMS-derived outputs live in the external `PCI_IMS_DATA_DIR` (outside the repository); the repository holds only regenerable synthetic data. M3 Parquet unchanged (80 MB). The M12 Power BI export (canonical opportunity export, ~170 MB, regenerable with `-m pci_powerbi.export`) is written to the active dataset folder: `PCI_IMS_DATA_DIR/powerbi` in IMS mode (outside the repository), `data/synthetic/powerbi/` (git-ignored) in public mode. Power BI's import cache is never saved to the repo.

## LAST VERIFIED
2026-09-28

## NEXT ACTION
Owner: review and commit the P1 changes on `public-release`; enable the guard (`git config core.hooksPath .githooks`); decide on archiving the private `main` history outside this repository. No push without explicit approval.
