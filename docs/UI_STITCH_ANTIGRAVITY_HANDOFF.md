# UI HANDOFF — Stitch design → Antigravity implementation

**Status (2026-09-27):**
- **Done (owner-operated).** Stitch produced 9 screens from the prompt below. They were used as a **visual reference only**: the export (Tailwind CDN, web and icon fonts, inline scripts, invented figures and metrics) was audited and never copied into the repository.
- **Antigravity implemented a CSS-only restyle** of `app/web/style.css`: light sidebar with a teal active item, shadow tokens, tighter type and radius, teal selected states, red scenario banner, and a phone breakpoint at 480 px. No HTML, JavaScript, metric, tool, test or Power BI change. The 28 colour tokens that Power BI reads are unchanged, and the Power BI drift test passes.
- **Not implemented** (optional in the brief): a per-panel evidence drawer, nav sub-links, and a scenario waterfall.
- **Verification:** full suite 886 passed. Antigravity reported five-width browser QA (1440/1366/1024/768/375) to the owner; Claude's independent check was a partial smoke test (synthetic data: Overview at desktop width, Scenario and AI Analyst at 375 px; no horizontal scroll, no console errors, local requests only).
- The application already covers every required area: the M11 UI, plus in Phase 3 a synthetic-data badge and a live data-quality table. So the tools below would be a **visual refinement**, not a functional dependency.

## Manual step 1 — Stitch (≈15–20 min)
1. Open Google Stitch in your own browser (free use only; no paid plan, no connectors, no file uploads) and start a new **web** project, desktop-first.
2. Paste the prompt below as-is. It is the only input: **no screenshots, files or data**. Every label in it is a placeholder, and it contains no real company, brand, product or value.
3. If Stitch produces fewer screens than asked, send follow-ups one screen at a time ("Now design screen 4 — Opportunity Intelligence, same system").
4. Export HTML/CSS (preferred) or Figma to `<DOWNLOADS_DIR>\stitch\`, one sub-folder per screen, and tell Claude the export is there.

Everything the prompt asks for exists in the current tool contracts (`/api/tools/*`): the dimensions, the measures, the opportunity score components and quadrant labels, the scenario types and their assumption ranges. It adds nothing the data cannot support.

```
Design a desktop-first web application (primary 1440 px; must also work at 1366, 1024, 768 and 375 px with no
horizontal page scroll) for a governed PHARMA COMMERCIAL-INTELLIGENCE product. It analyses a NATIONAL secondary-
sales audit at pack level: 36 months (Jun 2021 - May 2024), value in Rs crore, units in thousands. There is NO
geography, NO sales channel, NO prescriber/HCP, NO promotion data and NO forecasting. Do not design maps, regions,
territories, reps, prescribers, promotions, forecasts or predictions anywhere.

CONTENT RULES: use placeholder labels only ("Therapy area A", "Subgroup S01A", "Company 07", "Product 1000123",
"Brand ALFA"). Show numbers as neutral placeholders (e.g. "12.3%", "Rs 1,234 cr") and never write a headline as
if it were a real finding.

VISUAL SYSTEM: consulting-grade, editorial, calm. Warm off-white canvas #f4f3ee, white panels, ink #142231, one
teal accent #0d6570. Green #1b6f45 and red #ad3a2c only together with a signed number or an arrow; grey for "no
evidence". Serif display headings, sans-serif UI text, tabular figures right-aligned in tables. No gradients,
gauges, donuts, 3-D, pie charts or decorative illustrations. Accessible contrast, visible focus rings, keyboard-
reachable controls.

GLOBAL SHELL (every screen):
- Left sidebar navigation with the 9 areas below; collapses to a menu button under 1024 px.
- Top bar: breadcrumb; a persistent "SYNTHETIC DATA" badge (fictional dataset); a global PERIOD control =
  period-ending month dropdown + segmented basis switch [Month | Calendar YTD | MAT].
- Consistent filter bar under the page title, same position and style on every screen: market level
  [Therapy area | Therapy group | Subgroup | Molecule], market selector, company selector where relevant, top-N,
  and a "Reset filters" link. Applied filters show as removable chips.
- An EVIDENCE DRAWER (right side panel) opens from an "Evidence" link on every panel and every number. It shows
  the tool name, parameters, period window (current vs prior), row count, source fingerprint and caveats.

SCREENS (question-led titles):
1 EXECUTIVE OVERVIEW - "What changed in the market?" Headline sentence; 3 KPI tiles (value growth %, unit growth
  %, largest contributor to growth); 36-month value trend, current vs prior year; top gainers / decliners by
  contribution to growth (pp); a small "Opportunity signals - descriptive, not a forecast" panel.
2 MARKET TRENDS - "Which markets are driving growth?" Table: market, value, value growth %, units growth %, share
  of total, contribution to growth (pp), evolution index, growth rank. Row click drills into a market: monthly
  trend chart plus its sub-markets. Therapy area > group > subgroup hierarchy in the breadcrumb.
3 BRAND PERFORMANCE - "How is this product performing in its market?" Product search keyed by PRODUCT CODE; brand
  names are NOT unique, so show a "shared name" chip and require the user to pick one (never auto-pick). Table:
  product code, brand, company, value, growth %, share of market %, share change (pp), evolution index. Product
  detail: the markets it competes in (a product can span several subgroups), share trend, growth vs market.
  Include a company view: company table with the same measures, drill into its products.
4 OPPORTUNITY INTELLIGENCE - "Where is relative momentum strongest?" A 2x2 matrix: x = evolution index (threshold
  line), y = market growth vs national growth. Quadrant labels: "Outperforming in faster-growing market",
  "Outperforming in slower-growing market", "Underperforming in faster-growing market", "Underperforming in
  slower-growing market". Ranked table: rank, product, score 0-100, status, quadrant. Permanent banner:
  "Descriptive score, not a forecast." SCORE EXPLANATION drawer: stacked bar of the three weighted components
  (Market growth 30%, Relative momentum / evolution index 40%, Market position / value share 30%), each with its
  raw value, normalised value and contribution; positive drivers; constraints; methodology version. Status
  "INSUFFICIENT EVIDENCE" (with a reason: insufficient history, prior zero, below materiality, single-product
  market) shows in grey with a dash, never as a score of zero. Market-level variant: Market growth 60%, Market
  size 40%.
5 SEGMENT EXPLORER - "How is the mix shifting?" Segment switch: Acute/Chronic, Indian/MNC, Plain/Combination,
  Molecule count, Dosage form, NFC1. Table: segment, value, growth %, share %, share change (pp), contribution
  (pp); a mix-shift bar (share current vs prior). Optional scope to a market or company.
6 SCENARIO PLANNING - "What if these assumptions held?" Red-bordered banner "SCENARIO - NOT A FORECAST".
  Scenario type: Price change | Volume change | Price + volume | Market growth | Market share. Entity type and
  entity picker. Assumption inputs with visible allowed ranges: price change % (> -100 to 1000), volume change %
  (-100 to 1000), market growth % (-100 to 1000), target share % (0 to 100). Two columns: OBSERVED baseline
  (labelled OBSERVED) vs CALCULATED result (labelled CALCULATED), delta row, and a bridge/waterfall. An invalid
  assumption is REJECTED with a message naming the allowed range; never silently clamp or correct it.
7 DATA QUALITY - "Can the numbers be trusted?" Live checks table: check, expected, observed, status
  (PASS / FAIL / INFO); coverage (months, packs, period window); an explicit "Not supported by this data" list:
  geography, sales channels, SSA/HSA/DSA fields (meaning unconfirmed), prescribers, forecasts, price
  elasticity.
8 AI ANALYST - "Ask a question." Question box with supported-question examples; the answer has value chips
  linked to evidence; a mode label ("Deterministic demo mode" or "Gemini routing - synthetic data"); clarification
  buttons when a brand name is ambiguous; refusal cards for unsupported requests (geography, channel, forecast,
  unsafe instructions) that explain why and what can be asked instead.
9 EVIDENCE / QA - "How was this answer produced?" Pipeline: question -> selected agent -> tool call(s) with
  parameters -> deterministic engine -> QA checks (contract, routing, numeric grounding: every number traced to a
  tool field) -> answer with verification status (VERIFIED / QA FAILED / REFUSED).

STATES - design each as a reusable panel variant and show it at least once:
- Loading: skeleton rows and chart placeholder.
- Empty: "No rows for these filters" plus a "Reset filters" action.
- Error: tool error code and message, "Retry" action; the rest of the page keeps working.
- Insufficient evidence: grey, dash instead of number, reason text; never zero.
- Unsupported request: neutral card, "This data has no geography" (or channel / prescriber / forecast), with
  supported alternatives.
- Ambiguous request: "Several products share this name" with a list to choose from (product code, company,
  markets).

COMPONENTS to include in the design system: sidebar nav item (default / hover / active), period control, basis
switch, filter bar and chips, KPI tile, data table (sortable header, numeric column, rank, status pill,
pagination), line chart, bar chart, 2x2 matrix, stacked contribution bar, waterfall, evidence drawer, banner
(info / scenario warning / descriptive), status pills (PASS, FAIL, INFO, SCORED, INSUFFICIENT EVIDENCE,
OBSERVED, CALCULATED, VERIFIED, REFUSED), synthetic-data badge, and all six state panels.
```

## Manual step 2 — Antigravity (implementation)
1. Open this repository in Antigravity.
2. Give it the Stitch export and this brief:

```
Implement the attached Stitch design INSIDE the existing application (app/web: index.html, app.js, style.css,
agent.html, agent.js). Do not create a second app, framework or build step; no CDN or external assets.
Keep: hash routes (#/overview, #/market, #/therapy, #/product, #/company, #/opportunity, #/scenario, #/method,
/agent), all element ids used by tests/test_ui_m11.py, the design-token names in style.css :root, and the rule
that app.js calls only /api/tools/* and agent.js only /api/agent*. The UI formats/sorts/filters tool outputs only:
never compute a metric, never auto-select an ambiguous product, never clamp scenario assumptions.
Map the 9 designed areas onto the existing routes (see "Coverage of the brief" in docs/UI_STITCH_ANTIGRAVITY_HANDOFF.md);
add a route only for a screen with no existing home, and keep every existing id. Show only fields the tools return;
if the design shows something no tool provides, leave it out and list it. No geography UI.
Data: run with PCI_DATASET=synthetic (python -m pci_synthetic.generate; run_app.py --port 8766), LLM_PROVIDER
unset (deterministic mode), so browser QA makes no Gemini calls. Save QA screenshots and logs under
<SCRATCH_DIR>\ui_qa\ (synthetic data only), never in the repository.
Done when: .venv\Scripts\python.exe -m pytest tests/test_ui_m11.py tests/test_app.py tests/test_synthetic_m13.py
passes, and browser QA at 1440/1366/1024/768/375 px shows every page, filter, drill-down, scenario
(valid/rejected), opportunity drawer, AI Analyst clarification/refusal, evidence drawer and all panel states,
with the "Synthetic data" badge visible and no horizontal page scroll.
```

## Coverage of the brief by the current application
| Required area | Where it is now | Tools |
|---|---|---|
| Executive Overview | `#/overview` | get_market_performance, get_market_trends, get_company_performance |
| Market Trends | `#/market` (+ `#/therapy` Segments & mix) | get_market_performance, get_market_trends, get_therapy_performance |
| Brand Performance | `#/product` | find_products, get_brand_performance, get_brand_growth, get_brand_share |
| Opportunity Matrix | `#/opportunity` (matrix + decomposition drawer) | get_opportunity_scores, get_opportunity_detail |
| Segment Explorer | `#/therapy` | get_segment_analysis |
| Scenario Analysis | `#/scenario` | run_scenario, get_scenario_baseline |
| Data Quality | `#/method` → "Data quality controls" (live table, Phase 3) | get_data_quality_status |
| Natural Language Analytics | `/agent` (AI Analyst) | governed orchestrator → tools |
| Evidence / QA | a source footer on every panel (tool, window, scope); the evidence drawer is used for opportunity details (score breakdown, drivers, constraints, methodology); AI Analyst pipeline + QA audit | all |
| Synthetic-data indicator | top-bar badge, sidebar note, Data-foundation callout (Phase 3) | get_application_metadata.dataset |
| Geography | **deliberately absent**: the data has none; questions are refused (`UNSUPPORTED_GEOGRAPHY`) | — |
