# AGENTIC ARCHITECTURE (M9)

A governed, provider-agnostic agent layer over the M8 ToolRegistry. Code: `python/pci_agents/`. It is local, free and deterministic by default (`LLM_PROVIDER=NONE`).

```
USER ──> Orchestrator ── provider.plan() ─> {intent, params}  (or refusal: unsafe / unsupported / unrecognized)
            │   rejects unknown intents and unknown parameters
            ├─> MarketTrendAgent ─┐
            ├─> BrandProductAgent ─┤
            ├─> CompanySegmentAgent┤   agent.tool(name, params)
            ├─> OpportunityAgent ──┤ ───────────────> ToolGateway ── permission check (deny by default)
            └─> ScenarioAgent ─────┘                      │  execution record (no raw rows)
                                                           ▼
                                                  M8 ToolRegistry.invoke()  (schema validation, structured errors, scrubbing)
                                                           ▼
                                     validated engines (SQL / Python / M6 opportunity / M7 scenario) -> DuckDB/Parquet
            findings (references to tool-result fields, no arithmetic)
            ▼
   provider.compose() ─> draft text ─> Insight/QA engine (deterministic) ─> pass: final response | fail: QA_FAILED (withheld)
```

## Rule: AGENT → TOOLREGISTRY → VALIDATED ANALYTICS
- Agents hold only a `ToolGateway`. The gateway holds the M8 `ToolRegistry` privately.
- The `pci_agents` package imports none of `pci_analytics`, `pci_data`, `duckdb`, `pyarrow`, `openpyxl`, `sqlite3`, `socket`, `urllib`, `requests`, `subprocess`, and it never calls `open`/`eval`/`exec`. This is enforced by a static test.
- It uses exactly one M8 module, `pci_app.tools`: for contracts and for the response sanitiser.

## Components
| Component | File | Responsibility |
|---|---|---|
| Orchestrator | `orchestrator.py` | Plan via provider; validate intent and parameters (allow-list per intent); route to 1–2 agents (`ROUTES`); merge outcomes; compose; QA gate; scrub output. It calls no tools and computes no metrics. |
| Specialist agents | `agents.py` | Call permitted tools; build **findings** that *reference* tool-result fields (call + JSON path); pass tool errors through; never pick an entity for the user |
| ToolGateway | `gateway.py` | Per-agent permissions (`AGENT_TOOLS`); execution records; in-memory results for QA only |
| Insight / QA | `qa.py` | Deterministic validation of every response (12 checks) |
| Providers | `providers.py` | `LLMProvider` contract; `DeterministicDemoProvider` (`NONE`); `register_provider` plug-in point |
| Request grammar | `parser.py` | Demo-mode rule-based planning with refusals (unsafe / unsupported / unrecognized) |
| Schemas | `schemas.py` | Statuses, the unit of every metric, deterministic formatting, finding builders |
| Evaluation | `evaluation.py` | 28 deterministic cases plus a runner |

## Workflows (`ROUTES`)
| Intent | Agents → tools |
|---|---|
| MARKET_PERFORMANCE | MarketTrend → get_market_performance |
| MARKET_TREND | MarketTrend → get_market_trends |
| BRAND_PERFORMANCE | BrandProduct → (find_products →) get_brand_share + get_brand_growth |
| TOP_PRODUCTS | BrandProduct → get_brand_performance |
| COMPANY / THERAPY / SEGMENT | CompanySegment → get_company_performance / get_therapy_performance / get_segment_analysis |
| OPPORTUNITY / OPPORTUNITY_DETAIL | Opportunity → get_opportunity_scores / get_opportunity_detail |
| SCENARIO / SCENARIO_BASELINE | Scenario → run_scenario / get_scenario_baseline |
| GROWTH_AND_MOMENTUM (multi-step) | MarketTrend → get_market_performance (tool `rank_growth`), then Opportunity → get_opportunity_scores (evolution index) |

A workflow stops at the first non-OK step. No agent is called unless the intent needs it.

## No invented numbers
Agents never do arithmetic. `make_value(ctx, call, path, metric)` copies a field from a recorded tool result and formats it with a fixed formatter.

QA then checks provenance in several ways:
- **Values:** QA re-reads every path, compares the value and its display, and checks the unit.
- **Text:** every number in the final text must be traceable to a value's display or to tool-provided text (limitations, caveats, labels).
- **Missing values:** these are shown as `n/a (not available)` and never filled.
- **Counts:** agents do not state counts they computed themselves; the disambiguation message says "more than one product".

## Brand disambiguation
`find_products` → exact brand matches if any exist, otherwise all fragment matches → the distinct `prod_code`s:
- 0 codes → `ENTITY_NOT_FOUND`
- more than 1 → `AMBIGUOUS_ENTITY`, with options (product code, brand, company) and **no** further tool calls
- exactly 1 → proceed

The QA `disambiguation` check fails any response that continued after an ambiguous search. A regression test injects a "guessing" agent to prove this.

## Period, market and unsupported governance
- **Periods:** Month / Calendar YTD / MAT, and first-usable/first-growth months, are exactly the engine's. QA checks that tool result periods equal the requested anchor and basis. A missing comparison period gives `n/a` growth plus the engine caveat.
- **Market:** the therapy SUBGROUP definition note must appear in any response using market or therapy tools (QA `market_definition`).
- **Unsupported requests:** geography, channel, SSA/HSA/DSA, forecasting, elasticity, prescriber and promotion requests are refused **before** any tool call. Structured requests are also refused by the tool layer.
- **Unsafe requests:** SQL, code, files, URLs, credentials and instruction overrides return `UNSAFE_REQUEST` without any tool call.

## Provider abstraction (`LLMProvider`)
```python
class LLMProvider(ABC):
    name: str; is_llm: bool
    def plan(self, text, tool_contracts) -> dict      # {"intent", "params"} or {"refusal", "message"}
    def compose(self, response) -> str                 # text built only from findings/limitations
    def metadata(self) -> dict                         # provider, is_llm, network, api_key_required
```
- `get_provider()` reads `LLM_PROVIDER`, which defaults to **NONE** and resolves to `DeterministicDemoProvider`.
- **Optional Gemini provider (Phase 3):** `LLM_PROVIDER=GEMINI` loads `python/pci_llm_providers/gemini.py` lazily. There is no network code inside `pci_agents`.
  - **Opt-in and synthetic-only:** construction fails unless `PCI_DATASET=synthetic` and `GEMINI_API_KEY` is set in the environment. The server then falls back to NONE with a notice, so private data plus an external model is always rejected.
  - **Deterministic refusals run before the model:** geography, channel, forecasts, code/SQL/raw data and instruction overrides are refused and never sent.
  - **What the model sees:** only the question and the static intent catalogue. It returns `{intent, params}`, which the orchestrator validates against `ROUTES`/`INTENT_PARAMS` (unknown intent → `UNRECOGNIZED_REQUEST`; unknown parameter → `INVALID_INPUT`).
  - **Who writes the answer:** permissioned agents call deterministic tools, templates write the answer, and QA verifies every number. The model's own text is never shown.
  - **The key** travels in the `x-goog-api-key` header only.
  - **Evidence:** 11 tests with a mocked model (`tests/test_gemini_provider_phase3.py`). Live calls need the user's key.
- Other named providers (OPENAI, CLAUDE, ANTHROPIC, …) raise `ProviderNotConfigured`, and the server falls back to NONE.
- A future provider plugs in with `register_provider()`. It changes neither the ToolRegistry, the engines, QA, the security rules nor the permissions. Its plans still go through the intent/parameter allow-lists and permissions, and its text through QA. Tests prove a fabricating provider and a disclaimer-dropping provider are both blocked.

**DETERMINISTIC DEMO MODE** is a rule-based request grammar (`parser.SUPPORTED_FORMS`) with template text. It is labelled as such everywhere (response `mode`, text prefix, `/api/agent/status`, UI banner) and never presented as an LLM.

## QA checks (`qa.validate`)
1. numeric_provenance
2. unit_consistency
3. text_numbers_traceable
4. period_entity_consistency (all references, and the requested anchor/basis)
5. tool_provenance (calls exist, succeeded, were permitted; any denied attempt fails)
6. market_definition
7. no_unsupported_claims (forecast/probability/guarantee/geography wording outside tool-provided disclaimers; refusals made no successful calls)
8. insufficient_evidence_handling
9. scenario_not_forecast (banner in text, and OBSERVED/ASSUMED/CALCULATED kinds)
10. methodology_preserved (opportunity version + fingerprint + DESCRIPTIVE label; scenario version)
11. disambiguation

On failure the response becomes `QA_FAILED`: findings are withheld (with a count), the text is replaced, and nothing is repaired.

## Response structure
| Field | Contents |
|---|---|
| `request_id`, `status`, `mode` | request identity, outcome, and provider metadata |
| `intent`, `route` | planned intent and the agents called |
| `request_params` | the validated request parameters |
| `tool_calls` | execution records (call, agent, tool, input, status, error code, rows, ms) |
| `evidence` | per successful call: period, filters, units, methodology, engine evidence, scenario id |
| `findings` | statement, values (value, display, unit, source), refs |
| `limitations` | tool caveats and governance notes |
| `clarification` | options when the entity is ambiguous |
| `message` | explanation for refusals and errors |
| `qa` | the QA report |
| `final_response`, `elapsed_ms` | user-facing text and timing |

Statuses:
- `OK`, `AMBIGUOUS_ENTITY`, `UNSAFE_REQUEST`, `UNRECOGNIZED_REQUEST`, `TOOL_NOT_PERMITTED`, `QA_FAILED`
- every M8 tool error code, passed through unchanged

## Memory, privacy and security
- **Memory:** there is no long-term memory, profile or history. `RequestContext` lives for one request, and the orchestrator holds no per-request state (tested).
- **Logs:** execution records contain inputs, statuses and counts, never result rows. Nothing is persisted.
- **Output:** responses pass through the M8 sanitiser, so no filesystem paths appear, including echoed user input.
- **No access:** agents have no shell, Python, SQL, filesystem or HTTP access, and no unrestricted tool discovery (fixed permission lists). There are no network calls (tested with sockets blocked) and no API keys.

## UI
The M8 "Natural language (M9)" tab links to `/agent`. That page shows the provider banner ("Provider: DETERMINISTIC DEMO MODE · LLM connected: no"), example requests, the final text, the tool-call audit and the QA checks. The M8 pages are unchanged. Server routes: `GET /agent`, `GET /api/agent/status`, `POST /api/agent`.

**M11 (AI Analyst UX):** `/agent` now shares the product shell. It shows the pipeline (question → orchestrator → agent → permission-checked tool → engine → QA → answer) for the last request, value chips with claim → tool → field provenance, clarification buttons (signed continuation), refusal/unsupported/unrecognised states and collapsible audit sections. `/api/agent/status` additionally returns `supported_requests` (the demo grammar text). The orchestrator, agents, gateway, permissions and QA are unchanged.

## Limitations
- Demo mode understands only the documented request grammar. Names and keys must be in double quotes, and anything else returns `UNRECOGNIZED_REQUEST` with the supported forms.
- Refusal keywords are conservative: e.g. "select … from" or "state" trigger refusals even in benign phrasing.
- Findings are limited to 20 rows per step. Growth-ranked markets include very small markets (a stated limitation).
- There is no real LLM yet, so answers are templated, not conversational.
