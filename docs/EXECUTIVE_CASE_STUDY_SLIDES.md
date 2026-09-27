# EXECUTIVE CASE STUDY — 7-slide specification

This is a specification for building the deck in any tool. Use synthetic-data screenshots only, and give every chart the footer "Synthetic data — invented market, illustrative only". Visual language: the app's design tokens (`app/web/style.css`), one accent colour, no 3-D, no stock imagery.

---
### Slide 1 — The problem
- **Headline:** "Commercial questions are routine; trustworthy answers are not."
- **Body:**
  - Pack-level audits are wide and easy to misread (non-unique brands, zero-base growth, period mix-ups).
  - AI assistants add a new risk: invented numbers.
- **Visual:** three "failure mode" tiles: ambiguous brand, 0% vs BLANK growth, hallucinated figure.

### Slide 2 — What was built
- **Headline:** "One governed engine behind the app, the AI analyst and Power BI."
- **Visual:** a layer diagram: Parquet → SQL engine (cross-checked by the Python engine) → tool registry → {web app, agents + QA, Power BI}.
- **Callout:** "Agents route and explain. Tested functions calculate."

### Slide 3 — Analytics and methodology
- **Headline:** "Consistent definitions: Month, calendar YTD, MAT; share within market; contribution; evolution index."
- **Visual:** a synthetic growth-contribution waterfall by therapy area (Executive Overview screenshot).
- **Footnote:** growth is BLANK without a positive base; the primary market is the subgroup.

### Slide 4 — Opportunity and scenarios
- **Headline:** "Explainable evidence, not black-box predictions."
- **Left:** OPP-1.0.0 component bars for one synthetic market (percentiles, weights, evidence status).
- **Right:** a scenario card, price +10%: OBSERVED baseline → CALCULATED result with price/volume/interaction decomposition; an invalid assumption is rejected.
- **Footnote:** descriptive ranking and what-if arithmetic; not forecasts, not an industry standard.

### Slide 5 — Governed agentic AI
- **Headline:** "An AI analyst that cannot make up a number."
- **Visual:** a flow: question → parser/LLM (intent only) → permissioned tools → template answer → 16 QA checks → shown or withheld.
- **Proof:** 75-case evaluation (24 adversarial) at 100%; 334/334 numbers traced. The optional Gemini provider runs on synthetic data only; **live evaluation partial** (2/26 cases, free-tier quota).

### Slide 6 — Evidence of quality
- **Headline:** "Proven, not asserted."
- **KPI tiles:**
  - 878 tests;
  - SQL = Python parity;
  - Power BI 83/83 reconciled (5.2 M values, ≤2.9e-11);
  - metric traces agree to 1e-9;
  - synthetic leakage tests: 19.
- **Footnote:** counts only; no private values.

### Slide 7 — Limits and next steps
- **Limits:** national data only (no geography, channel or HCP); inferred unit scale; judgement-based weights; no elasticity; grammar-bound default AI.
- **Next:** live LLM evaluation on synthetic data; UI visual refinement; public snapshot with a licence.
- **Closing line:** "Portfolio project — no client or commercial impact is claimed."

---
**Build notes:**
- Figures quoted on slides must come from `EVALUATION_RESULTS.md` or `docs/CASE_STUDY.md` §11 (synthetic).
- Screenshots must be taken with `PCI_DATASET=synthetic` and show the synthetic badge.
