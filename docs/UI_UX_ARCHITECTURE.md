# UI / UX ARCHITECTURE (M11)

The local application (`run_app.py` → http://127.0.0.1:8765) redesigned as one commercial-intelligence product.
It is still vanilla HTML/CSS/JS served by the stdlib server: no framework, no build step, no external assets, no new dependency.

| File | Role |
|---|---|
| `app/web/index.html` | App shell (sidebar, context bar, drawer, tooltip) and the skeleton of 8 pages |
| `app/web/app.js` | Tool client, router, UI primitives, SVG charts, page controllers |
| `app/web/agent.html`, `agent.js` | AI Analyst page (same shell and design system), over the M9/M10 agent API |
| `app/web/style.css` | The single design system for both documents |

## 1. Design principle
Pages are built around the commercial questions, not around available charts:
**What changed? Where? Who or what drove it? Where is the opportunity? What should I investigate next?**
Each major panel states its question under the title, names its tool in the footer (`Source: get_…`), and leads to a drill-down.

## 2. Information architecture
| # | Page (route) | Purpose | Key questions |
|---|---|---|---|
| 01 | Executive Overview `#/overview` | Executive cockpit | What changed nationally; which therapy areas and subgroups drove it; which companies gain or lose share; where the strongest opportunity evidence is; what to investigate next |
| 02 | Market Intelligence `#/market` | Market exploration | Rank markets at any level; contribution to growth; select a market → trend, products that drove it, company positions |
| 02b | Segments & mix `#/therapy` | Mix and mix shift | How a scope splits by a source segment, now vs a year earlier |
| 03 | Brand & Portfolio `#/product` | Product intelligence | Leaderboard and share movement; search with brand disambiguation; product profile, trend, markets where it competes, position in one market |
| 04 | Company Intelligence `#/company` | Company and portfolio | Ranking and share movement within a scope; company profile, segment composition, leading products, market exposure |
| 05 | Opportunity Intelligence `#/opportunity` | Descriptive opportunity scoring (OPP) | Opportunity matrix, quadrant filters, ranked list with score decomposition, evidence drawer, methodology in force |
| 06 | Scenario Planning `#/scenario` | What-if on an observed baseline (SCN) | Observed baseline + assumption → calculated result, absolute and % change |
| 07 | AI Analyst `/agent` | Governed natural-language analytics | Question → orchestrator → specialist agent → permission-checked tool → engine → QA → answer |
| 08 | Methodology & Data Quality `#/method` | Trust | Data grain, market definition, periods (engine-returned windows), metrics and units, OPP/SCN methods, evidence statuses, unsupported analysis, DQ controls, tool contracts |

The M8 route ids are kept (`overview, market, product, company, therapy, opportunity, scenario, method, nl`); `nl` navigates to `/agent`.

## 3. Navigation and context
- **Sidebar** (primary IA, grouped Intelligence / Decision support / Trust), numbered, with `aria-current="page"`. Below 980 px it becomes an off-canvas drawer behind a menu button.
- **Context bar**: breadcrumb (page → level → selected entity) and the **global period**: period ending (month) + basis (Month · YTD · MAT). The period applies to every page and is remembered locally.
- **URL state**: every filter and selection is in the hash (`#/market?level=subgroup&key=…`), so views are bookmarkable. The back button returns to the previous selection. Filters replace history entries; selections push new ones.
- **Drill-downs**: overview charts → market or company; market → product/company/scenario/opportunity; product → market/scenario/opportunity evidence; company → product/opportunity/scenario; opportunity → drawer → product/market; “Ask the AI Analyst” deep links (`/agent?q=…`, prefilled grammar).

## 4. Design system (`style.css`)
- **Tokens** on `:root`: warm off-white canvas, white panels, navy sidebar, ink text; one interactive accent (deep teal); semantic positive/negative/caution/info; a single-hue opportunity score ramp; a 4-pt spacing scale; 6 px radius; system font stacks (serif for page titles and narrative, sans for UI, mono for identifiers), with no web fonts.
- **Colour means something**: movement (green ▲ / red ▼, always with arrow and sign), opportunity (teal), caution/assumption (amber), neutral information, selection, data status. It is never decorative, and never the only signal.
- **Components**: page header (eyebrow, serif title, lede, period chips); panel (title, question, tools, body, source footer); executive signal band; KPI strip; filter bar with active-filter chips and reset; segmented control (real radio inputs); sortable data table; badges (Observed / Assumed / Calculated / Descriptive / Scored / Insufficient evidence / Unsupported / QA pass-fail / product key); disclaimers; callouts; identity block (product code vs brand vs company vs launch); drawer (dialog); tooltip; investigation paths; quadrant filter buttons; scenario type cards and equation table; AI pipeline strip.
- **Restraint**: no gradients, glass, neon, stock imagery or decorative icons, and minimal shadows (overlays only).

## 5. Component architecture (`app.js`)
1. **Tool client**: `tool(name, params)` POSTs to `/api/tools/<name>`. It drops unset parameters, caches and de-duplicates by name + parameters (errors are not cached), and throws the ToolRegistry's structured error.
2. **Router**: `parseHash` → `route()` → `PAGES[page](params)`. Page controllers are idempotent. `once(el, signature, fn)` re-renders a section only when its inputs change. `load(el, kind, promise, render)` shows a skeleton after 90 ms (so cached data doesn't flash), ignores stale responses, and renders uniform error states.
3. **Primitives**: `S.loading/empty/insufficient/ambiguous/error`, `dataTable`, `kpiStrip`, `segControl`, `chips`, `tip`, drawer.
4. **Charts** (SVG strings, redrawn by a `ResizeObserver`): `lineChart` (hover/keyboard read-out, prior-year comparison series), `barChart` (ranking or diverging, selectable rows), `mixChart` (share now vs a year earlier), `scatter` (quadrants, log and symmetric-log axes, clamped points drawn hollow with true values in the tooltip), `scoreStack` (engine component points), `sparkline`.

## 6. Interaction model
- **Linked selection**: selecting a market in the ranking *or* the contribution chart updates the detail band, trend, products and company panels, the breadcrumb and the URL. Table, chart and URL stay in sync.
- **Period switching** re-renders the current page only; other pages re-render when visited (signature-based).
- **Opportunity**: quadrant buttons filter the list and dim the matrix; any row or point opens the evidence drawer (`get_opportunity_detail`). The drawer state is in the URL (`focus=`).
- **Scenario**: type cards, entity pickers (lists from tools; product search with disambiguation), a slider plus number input per assumption, and optional live recalculation (debounced 280 ms). The slider range is only a convenience: the typed number is sent unchanged, and the engine rejects invalid values, which are never clamped or corrected.
- **Motion** is limited to chart fade-in, drawer/sidebar slide and skeleton shimmer, and is disabled under `prefers-reduced-motion`.

## 7. Filter architecture
Filters are contextual per page and only expose dimensions the engines support: level, therapy area (within), name search, scope level + member, segment, company, top-N, include insufficient evidence, quadrant. Active filters appear as removable chips, alongside fixed context chips (level, basis). Each page has **Reset filters**. Defaults: subgroup level, total market, MAT, latest month. There are no geography, channel, SSA/HSA/DSA, prescriber or promotion filters.

## 8. Charting principles
Lines are used for trends (with the same period a year earlier as a dashed comparison), horizontal bars for rankings and contributions (diverging around zero), paired bars for mix shift, and a scatter for the opportunity matrix. There are no pies, donuts, gauges or 3D charts. Every chart has a question-style title, units in labels or tooltips, keyboard and hover detail, an empty state, and zero-based or reference lines where they aid reading. Axis scaling (log EI, symmetric-log growth, quantile-based ranges) is presentation only, and out-of-range points are marked, not dropped.

## 9. Loading, empty, error and special states
| State | Treatment |
|---|---|
| Loading | Skeleton per panel (after 90 ms), `aria-busy` |
| Empty | Why there is no data + what to change (e.g. “No subgroup names contain …”) |
| Error | Human title + engine message (already path-scrubbed) + next step + code. No stack traces, paths or environment details. |
| Insufficient evidence | Badge and state “Not scored — insufficient evidence … not a score of zero”; excluded from the matrix, with the count stated |
| n/a | Dotted “n/a” with the reason as a tooltip (no comparison window, prior zero, no sales). Never 0. |
| Unsupported | “Outside the supported analytical model” with the reason (national data only, …) |
| Ambiguous | Shared brand names produce a clarification callout and a list of product codes. Nothing is auto-selected. The AI Analyst uses the signed continuation token. |
| Rejected assumption | “Assumption rejected — invalid assumptions are rejected, never corrected” + engine range message |

## 10. Data integrity rules
- The UI reads only ToolRegistry outputs, and shows every number as returned, formatted with adaptive precision so that tiny non-zero values never read as 0.
- It never re-derives growth, share, EI, contribution, price, scores or scenario results. It only sorts, filters, selects, counts rows shown, and scales geometry.
- Identity follows the engine: product = product code, brand = label, company = source company, market = therapy subgroup. Opportunity rows are product-in-subgroup.
- Disclaimers are fixed in the page: **DESCRIPTIVE OPPORTUNITY SCORE — NOT A FORECAST · NOT A PROBABILITY · NOT A GUARANTEE**, **SCENARIO / WHAT-IF ANALYSIS — NOT A FORECAST** and “Scenario analysis — not a forecast.” Methodology version and fingerprint are shown with results.
- Documentation, tests and the repository contain no real figures; real values appear only in the local browser.

## 11. Security and privacy
The existing M8/M9 model is unchanged:
- CSP `default-src 'self'` with no inline scripts or styles, and `X-Frame-Options: DENY`.
- The server binds to 127.0.0.1 only. Static files are whitelisted, and there are no raw-row, SQL, code or file endpoints.
- `app.js` calls only `/api/tools/*`; `agent.js` calls only `/api/agent` and `/api/agent/status` (the agents keep their permissioned gateway).
- No external URLs, CDNs, fonts, analytics or telemetry.
- `localStorage` holds only three UI preferences (period, basis, trend metric) and is optional.

Server changes in M11:
- `/favicon.ico` returns 204 (avoids console noise).
- `/api/agent/status` adds `supported_requests`: the demo grammar text, so the UI does not duplicate it.

## 12. Responsive strategy
| Width | Layout |
|---|---|
| ≥ 1281 px | 12-column grid |
| ≤ 1280 px | Asymmetric panels stack; the signal band reflows to 3 + headline |
| ≤ 1180 px | All panels full width |
| ≤ 980 px | Off-canvas navigation, compact context bar |
| ≤ 720 px | 2-column signal band, stacked filters, single-column quadrants |

Tables scroll inside their panel; charts redraw to their container width. Verified with no horizontal page overflow at 375, 768, 1024 and 1440 px on every page.

## 13. Accessibility
- Semantic landmarks (`nav`, `main`, `aside`, `header`), a skip link, and one `h1` per page.
- Every form control is labelled. Segmented controls are native radio groups; the drawer is a modal dialog with a focus trap, Escape to close and focus return.
- Visible `:focus-visible` rings.
- Keyboard operation:
  - table rows: Tab, ↑ ↓, Enter or Space
  - sortable headers use `aria-sort`
  - bar rows and matrix points are focusable buttons
  - line charts are sliders read with ← → Home End, announced through `aria-valuetext`
- Live regions for filters, results and errors.
- Sign plus arrow on every delta; dotted n/a with a reason.
- Reduced-motion support.

## 14. Browser QA approach (M11)
The app was run locally and every page inspected in a browser at 1440, 1024, 768 and 375 px. The QA covered:
- navigation, the level/area/name filters and linked selection
- product search with a shared brand (clarification, then selection)
- the company profile
- the opportunity matrix, quadrant filter and evidence drawer (scored and insufficient)
- scenario types: valid, rejected (−150 %) and market share
- segments, the methodology timeline
- AI Analyst flows: answer with provenance, clarification → continuation, geography refusal, injection refusal, unrecognised request
- keyboard selection and the mobile navigation drawer

Defects found in QA and fixed include:
- chart styles not applied
- crowded opportunity matrix, fixed with log/symmetric-log axes and a ranked list on the overview
- tables clipped in narrow panels
- stale headers after deselection
- tiny values shown as 0
- a missing scenario measure (price per counting unit)
- the drawer focus parameter not being cleared

## 15. Performance
There is no framework and the page weight is under 300 KB (tested); tool responses are cached per session. The local server answers one request at a time (M8 design), so the overview issues its headline requests first and the 2 MB subgroup list last. Measured locally, warm: the signal band renders after about 1 s and all overview panels by about 3 s. Per-tool engine time is unchanged from M8.

## 16. Tests
`tests/test_ui_m11.py` covers:
- IA and navigation, and route/section/renderer integrity
- every element id the scripts use
- tool whitelist and endpoints
- error-code and agent-status coverage
- selector vocabularies equal to the engines' (entity types, segments, bases, scenario types and assumptions, opportunity quadrants)
- UI parameter shapes validated by the ToolRegistry, and field contracts (every field the UI reads exists)
- scenario OBSERVED/ASSUMED/CALCULATED and rejection
- disclaimers, ambiguity (never auto-select), n/a handling
- no hard-coded figures or client-side metric derivation
- no external assets, inline code or tracking; storage limited to preferences; payload budget
- labelled controls, landmarks and ARIA, CSS tokens, focus, reduced motion and breakpoints
- keyboard hooks
- HTTP pages, headers, 404s, agent status and structured errors

One M8 assertion was refined, not relaxed: the loaded assets must still be exactly `app.js` and `style.css`, and every navigation link must now be an in-app route.

## 17. Known limitations
- Single-user local server: requests are serialised, so first loads of heavy views take a few seconds.
- The AI Analyst remains DETERMINISTIC DEMO MODE: it understands a documented grammar, and its answers are templates.
- Charts are hand-written SVG. Very large selections (for example 500 opportunity points) are readable but dense.
- Company therapy-area exposure is shown at product-in-subgroup grain. There is no aggregated company × therapy tool, and the UI does not sum values itself.

## Phase 3 additions (2026-09-27)
- **Synthetic-data indicator:** `get_application_metadata.dataset` drives a top-bar "Synthetic data" badge, the sidebar note and a Data-foundation callout. It is hidden on the private dataset.
- **Live data-quality table:** `#/method` → Data quality controls, from `get_data_quality_status` (counts only).
- **Stitch / Antigravity:** not run (manual Google tools). See `docs/UI_STITCH_ANTIGRAVITY_HANDOFF.md`.
