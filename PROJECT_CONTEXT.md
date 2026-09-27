# PROJECT CONTEXT

## Project name
Pharma Commercial Intelligence & Brand Growth Engine

## Objective
A functional commercial-pharma analytics and agentic-AI portfolio project: deterministic, tested market/brand analytics over IMS-style secondary sales data, exposed through function-calling agents that answer natural-language questions with grounded, reconciled numbers, plus a Power BI layer.

## Target users
- Brand / product managers (brand growth, share, competition)
- Commercial excellence & sales-strategy analysts (market trends, segments, opportunities)
- Business-development / portfolio teams (therapy and molecule opportunity screening)
- Portfolio reviewers / hiring managers (demonstration of Python, SQL, BI and agentic AI)

## Business problem
IMS/IQVIA pack-level data is wide, large (105k packs × 203 columns) and hard to interrogate quickly. Teams need fast, trustworthy answers — "which chronic cardiac subgroups are growing fastest?", "how is brand X gaining share vs. molecule peers?", "what if our price rises 5%?" — without spreadsheet errors or AI hallucinations.

## Core capabilities (planned)
Market trends · brand performance & growth · market share & contribution · segmentation (therapy, acute/chronic, Indian/MNC, form, plain/combination, new introductions) · explainable opportunity scoring · scenario analysis · data-quality status · evaluation · NL analytics · Power BI dashboards.

## Technology stack
| Layer | Choice | Status |
|---|---|---|
| Language | Python 3.13.3 (installed) | CONFIRMED |
| Analytical store | Parquet (zstd) + DuckDB 1.5.5 in-memory views; pyarrow 25.0.1 | CONFIRMED (M3) |
| SQL | DuckDB SQL views + table macros (`sql/*.sql`) | CONFIRMED (M3 data layer, M4 metrics) |
| Tests | pytest 9.1.1 in the project `.venv` | CONFIRMED (full suite M3–M8) |
| Agents | LLM function calling (provider TBD; Claude preferred) | TBD M9 |
| App/API | Python stdlib HTTP server + vanilla-JS UI; whitelisted tool API (no new dependencies, offline, $0) | CONFIRMED (M8) |
| BI | Power BI Desktop | TBD M11 |
| Node.js 24.14 / npm 11.9 | available, not used yet | CONFIRMED available |

## Agentic architecture
User → Orchestrator → intent/scope detection → specialist agent (Market Trend, Brand Performance, Segment, Scenario, Insight/QA) → deterministic tools (SQL, Python, scenario engine) → structured results → QA/reconciliation → grounded NL response. See `ARCHITECTURE.md`, `AGENT_SPEC.md`.

## Deterministic analytics principle
**Agents explain and route. Agents do not invent or calculate critical business metrics.** All numbers come from deterministic, tested Python/SQL functions; responses cite the function and parameters used.

## Data privacy rules
- Source is private/licensed; stays at `<PRIVATE_WORKBOOK_PATH>`, never copied, uploaded or committed.
- No raw rows in chat, docs, screenshots or prompts. LLMs receive only structured function outputs (aggregates).
- Public/GitHub version uses a synthetic dataset (M12). See `SECURITY.md`, `DATA_PROVENANCE.md`.

## Locations
- Project root: `<PROJECT_ROOT>`
- Private source: `<PRIVATE_WORKBOOK_PATH>` (xlsx; location set via `PCI_SOURCE_PATH`)

## Status
- Current status: M1–M5 are complete. The SQL commercial analytics layer (M4) is independently re-implemented in Python and cross-validated field by field (M5): 54-check matrix, API parity for all 9 functions, fault injection.
- Primary market = therapy SUBGROUP (analytical definition; docs/MARKET_DEFINITION.md).
- M6: deterministic, explainable opportunity scoring (OPP-1.0.0) for products-in-market and markets.
- M7: deterministic scenario engine (SCN-1.0.0): price, volume, price+volume, market growth, market share — what-if only, not forecasts.
- M8: local application (`run_app.py` → http://127.0.0.1:8765) over a whitelisted tool API; no AI, no cloud, no API keys.
- Current milestone: M8 complete; **awaiting user approval**.
- Next milestone: M9 — Agentic layer.
- Open business questions (non-blocking): SSA/HSA/DSA meaning; licence terms for publishing aggregates (`DATA_DICTIONARY.md` §8).
