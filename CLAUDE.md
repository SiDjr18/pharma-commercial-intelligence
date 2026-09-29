# CLAUDE.md — working rules for this repository

Project: **Pharma Commercial Intelligence & Brand Growth Engine**
Root: `<PROJECT_ROOT>` · Private source: `<PRIVATE_WORKBOOK_PATH>` (set via `PCI_SOURCE_PATH`)

Read first, every session: `PROJECT_STATUS.md` → `PROJECT_CONTEXT.md` → `IMPLEMENTATION_PLAN.md` → `DATA_DICTIONARY.md`.

## Non-negotiable rules
1. **Storage** — everything lives under `<PROJECT_ROOT>` (the project drive). Never create project files, venvs, caches or node_modules on the system drive. If the project drive is unavailable, stop and report. Do not change TEMP/TMP.
2. **Private source** — the workbook at `<PRIVATE_WORKBOOK_PATH>` is licensed/proprietary.
   - Never modify, rename, move or copy it (including into `data/raw`).
   - Never paste raw rows into chat, docs, commits, screenshots, prompts or external services.
   - Process locally only (Python/DuckDB/streaming). Only derived metadata, validated aggregates and synthetic data may leave `data/`.
3. **Deterministic analytics** — agents route and explain; they never compute or invent business metrics. Every number shown to a user comes from a tested Python/SQL function.
4. **No fabrication** — if the data does not support a question (e.g. geography, channel, HCP), say so. Unknowns in `DATA_DICTIONARY.md` stay UNKNOWN until confirmed.
5. **Milestone discipline** — work only on the current milestone in `PROJECT_STATUS.md`. Update `PROJECT_STATUS.md` at the end of every milestone.
6. **Performance** — keep the laptop responsive: stream/chunk, no duplicate datasets, no Docker, one dev server at most, minimal dependencies.
7. **Git** — never commit `.env`, keys, IMS data or anything derived from it, logs, caches. Do not push without explicit approval. Enable the publication guard once per clone (`git config core.hooksPath .githooks`; docs/PUBLICATION_GUARD.md).
8. **Two modes (P1 isolation)** — `PCI_DATASET` unset/`synthetic` = public mode (default, `data/synthetic`). `PCI_DATASET=ims` + `PCI_IMS_DATA_DIR=<PRIVATE_EXTERNAL_DIRECTORY>` = private mode; the directory must be outside the repository and every IMS-derived output goes under it (`pci_data.schema.output_dir`). No discovery, no fallback between modes (docs/DATA_ISOLATION.md).

## Key schema facts (see DATA_DICTIONARY.md)
- Sheet `DATA`, 105,317 rows × 203 cols, one row per pack (`PFC`, unique). Wide format.
- Monthly value / units / qty, Jun'21–May'24 (36 months). MAT = trailing 12m; CUMM = calendar YTD.
- Brand name (`BRANDS`) is NOT unique — key on `PROD_CODE`.
- No geography. SSA/HSA/DSA meaning UNKNOWN.
- Value = ₹ crore (confirmed); units/qty = '000 (inferred). `INDEX` = brand : subgroup : manufacturer : prod_code.
- A product can span several subgroups/molecules, so therapy/molecule membership is at pack grain.

## Working with the data (M3+)
- Query via `pci_data.db.connect()` (DuckDB views over the active dataset folder: `data/synthetic` or `PCI_IMS_DATA_DIR`). Never re-read the xlsx for analytics.
- Rebuild (IMS mode only, with `PCI_SOURCE_PATH`): `cd python && ..\.venv\Scripts\python.exe -m pci_data.build_processed` (writes to `PCI_IMS_DATA_DIR`). Test: `.venv\Scripts\python.exe -m pytest` (`PCI_SKIP_SOURCE=1` for fast mode); tests marked `ims` run only in IMS mode.
- Metrics come from `fact_pack_month`; `pack_snapshot` is only for reconciliation. Roll therapy up via subgroup. SSA/HSA/DSA are not for analytics.
- Analytics (M4): call `pci_analytics.CommercialAnalytics` functions (API_SPEC.md); SQL engine in `sql/metrics.sql`. Primary market = SUBGROUP. YTD = calendar YTD. Growth NULL when prior ≤ 0/unavailable, never 0.
- Independent check (M5): `cd python && ..\.venv\Scripts\python.exe -m pci_analytics.validation`. Any change to SQL metrics must keep SQL/Python parity (tests/test_python_sql_crosscheck.py).
- Opportunity scoring (M6): single canonical implementation `pci_analytics/opportunity.py`; parameters only in `opportunity_config.py`. Any methodology change = version bump + new pinned fingerprint in tests/test_opportunity.py + doc update.
- Scenarios (M7): canonical `pci_analytics/scenario.py`; price = 1e4 × value_cr/units_k (₹/pack, equals source PR). Scenarios are what-if arithmetic, never forecasts; outputs are CALCULATED, never OBSERVED. Invalid assumptions are rejected, never corrected.
- Application (M8): `run_app.py` → http://127.0.0.1:8765. UI/agents call only `pci_app.tools.ToolRegistry` (whitelisted tools, schema validation, structured errors). Never add endpoints returning raw rows, SQL, code execution or file access; no external/CDN assets; no new runtime dependency without a free/offline justification.
- Agents (M9): `python/pci_agents` may only reach analytics through `ToolGateway` → M8 `ToolRegistry`; never import engines/DuckDB/pyarrow/network libs (static test). Agents reference tool fields, never compute numbers; QA withholds failing answers. `LLM_PROVIDER` defaults to NONE (DETERMINISTIC DEMO MODE). The optional `LLM_PROVIDER=GEMINI` (Phase 3, `python/pci_llm_providers/`) routes only, requires `PCI_DATASET=synthetic` and an env key, and never writes numbers; keep network code out of `pci_agents`.
- UI (M11): `app/web` is one design system (style.css tokens) and one router (`app.js`); UI code formats/sorts/filters/selects tool outputs only — never derives metrics, never auto-selects an ambiguous product, never clamps scenario assumptions. `app.js` calls only `/api/tools/*`, `agent.js` only `/api/agent*`. Keep `tests/test_ui_m11.py` green (ids, field contracts, vocabularies, a11y, security). Browser QA on a spare port if 8765 is taken.
- Power BI (M12): `python/pci_powerbi/` generates `dashboards/*.pbip` (never hand-edit; `-m pci_powerbi.build`; drift test). Measures must reproduce `sql/metrics.sql`; opportunity scores are imported from `-m pci_powerbi.export`, never recomputed in DAX. After any model change run `-m pci_powerbi.reconcile` with Power BI Desktop open (0 failures). Never commit `.pbix/.pbit/.abf/.pbi/`; never change Power BI Desktop settings; no publish/sign-in.
- Synthetic data (M13): `PCI_DATASET` ∈ {synthetic (default), ims}; generator `python/pci_synthetic` (structure-only, fictional, deterministic; reads no data file; `SYNTHETIC_FINGERPRINT.json` pinned by tests). Never calibrate on, copy, sample or scale real rows or aggregates. Run the full suite with `PCI_DATASET=ims` + `PCI_IMS_DATA_DIR` (private) and again unset (public); `ims`-marked tests are skipped in public mode. Demos, screenshots and external/LLM use: synthetic only.
- Committed docs must not contain real value aggregates (shares, totals, prices); keep them under `PCI_IMS_DATA_DIR` (outside the repository).
- Install packages only into `.venv`, with `PIP_CACHE_DIR=<PROJECT_ROOT>\.cache\pip`.

## Conventions
- Python 3.13, code in `python/` (package code) and `analytics/` (metric functions); SQL in `sql/`; tests in `tests/` (pytest).
- Never rename source columns in the raw read; map to snake_case only in the processed layer with an explicit mapping table.
