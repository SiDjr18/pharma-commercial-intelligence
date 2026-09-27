# CASE STUDY — Governed commercial intelligence for pharma brand growth

*A portfolio project. It makes no claim of client, employer or commercial impact. All figures in the "Findings" section come from the **synthetic** dataset and describe an invented market.*

## 1. Problem
Pharma commercial teams answer the same questions every cycle:
- Where is the market growing?
- Who is gaining share?
- Which brands are losing momentum?
- What would a price or volume change do?

The data (pack-level secondary sales audits) is wide, messy and easy to misread. Brand names are not unique. Growth on a zero base gets reported as a number. Fiscal and calendar periods get mixed. AI assistants make this worse when they generate numbers instead of retrieving them.

**Goal:** a product where every number is reproducible, every AI answer is traceable to a tested calculation, and the same definitions drive the web app, the agent layer and Power BI.

## 2. Business questions (the product's scope)
1. How big is each market, and how fast is it moving (Month, calendar YTD, MAT)?
2. Which therapy areas and subgroups drove national growth (contribution in percentage points)?
3. Which companies and products gained or lost share within *their* market (evolution index)?
4. Where is the strongest *observed* evidence of opportunity, and why?
5. What happens to value under explicit price, volume, market-growth or share assumptions?
6. Is the data complete and consistent, and what can it **not** answer (geography, channel, prescribers, forecasts)?

## 3. Data
- **Source:** a licensed national secondary-sales audit, one wide sheet: 105,317 packs × 203 columns, 36 months (Jun 2021 – May 2024), value in ₹ crore plus units and counting units. It was profiled read-only, and every column was classified as CONFIRMED, INFERRED or UNKNOWN.
- **Processed layer:** a streamed, hash-verified extract to 4 Parquet tables: pack, dense pack × month fact (3.79 M rows), source snapshots and prices. Reconciliation tests prove the layer reproduces every source total and the source's own MAT/YTD/price fields.
- **Pitfalls found and designed around:**
  - 658 brand names are shared across companies, so the key is the product code;
  - therapy groups aren't strictly nested in therapy areas, so roll-ups go via subgroup;
  - SSA/HSA/DSA columns have unknown meaning, so they are excluded;
  - there is no geography, so geographic questions are refused.
- **Public data:** a structure-only synthetic dataset (4,000 packs) with fictional names and invented numbers, generated deterministically. Leakage tests prove no label, code, row or aggregate shape is shared with the licensed data.

## 4. Methodology and analytics
- **Periods:** Month vs the same month last year; calendar YTD (not the Indian fiscal year); MAT vs the previous 12 months. Windows that start before the data give no value, and comparison windows before the data give BLANK growth.
- **Metrics:**
  - value, units, qty;
  - growth, which is `NULL` unless the prior value is positive, and never 0;
  - share, share change, units share;
  - contribution to scope growth, which sums to scope growth;
  - evolution index;
  - deterministic ranks with byte-order tie-breaks.
- **Primary market:** the therapy **subgroup**. Shares are always within an explicit scope.
- **Two engines:** a SQL engine (DuckDB macros) and an **independent** Python engine are compared field by field across a period × entity × scope matrix (80 parity tests, re-run on the synthetic data).

## 5. Opportunity framework (OPP-1.0.0)
- **What is scored:** product-in-market and market (subgroup).
- **Product components:** market growth 30%, relative momentum (evolution index) 40%, market position (share) 30%.
- **Market components:** growth 60%, size 40%.
- **Method:** mid-rank percentiles within the national scored population, which makes the score robust to heavy tails. Weights are documented assumptions, the parameters are pinned by a fingerprint, and a sensitivity analysis is included.
- **Evidence rules:** no prior sales, a sub-materiality base, a market with no sales, or fewer than 2 competitors means **Insufficient evidence**, never a low score.
- **Positioning:** a *descriptive* ranking, not a forecast or probability, and not an industry standard.

## 6. Scenario engine (SCN-1.0.0)
- **Scenario types:** price, volume, price + volume, market growth, market share.
- **Price per pack:** `1e4 × value / units`, which reproduces the source's price field to machine precision.
- **Outputs:** the baseline is labelled OBSERVED; results and the price/volume/interaction decomposition are labelled CALCULATED.
- **Validation:** assumptions outside their ranges are **rejected, never clamped**.
- **What it is not:** there is no elasticity, forecasting or competitor reaction. The engine states this on every result.

## 7. Agentic AI
- **Orchestrator:** parses the request into an intent, then routes it to specialist agents (market, brand, company/segment, opportunity, scenario, data quality).
- **Permissioned gateway:** each tool is owned by one agent, and the default is deny.
- **Tools:** 15 whitelisted deterministic tools with JSON-schema contracts.
- **Answers:** agents reference tool fields; they never compute. Answers are template-composed.
- **QA:** 16 deterministic checks, including numeric provenance, text numbers traceable, period/entity consistency, unsupported claims, the scenario-not-forecast label and methodology preservation. **A failing answer is withheld.**
- **Default mode:** `LLM_PROVIDER=NONE` runs a deterministic request grammar, offline.
- **Optional Gemini provider:**
  - synthetic data only, key from the environment;
  - deterministic refusals happen *before* the model;
  - the model returns only `{intent, params}`, validated by allow-lists;
  - tools compute and templates write the answer.
- **Evaluation:** 75 cases, 24 of them adversarial, plus negative controls and fault injection, all at 100%. The Gemini contract package has 26 cases (offline dry run 19/19; **live run partial**: 2/26 executed on the free tier and both passed, 24 blocked by the daily quota).

## 8. UX
- **Structure:** a local, offline web app with one design system and 8 areas: Executive Overview, Market Intelligence (with segments), Brand & Portfolio, Company Intelligence, Opportunity Intelligence, Scenario Planning, AI Analyst, and Methodology & Data Quality.
- **Controls:** a global period control and bookmarkable, linked drill-downs.
- **Evidence and states:** a source footer on every panel (tool, window, scope), an evidence drawer for opportunity details, and explicit loading, empty, error, insufficient-evidence, unsupported and ambiguous states.
- **Rules:** ambiguous brands are never auto-selected; assumptions are never clamped. A synthetic-data badge shows whenever the synthetic dataset is active.

## 9. Power BI
- **Model:** a generated Power BI project on the same Parquet: a star schema with 157 measures that reproduce the SQL definitions. Opportunity scores are imported from the canonical engine, and DAX scenarios follow the M7 formulas.
- **Report:** 7 pages plus 2 drill-through pages, in the web app's visual language.
- **Reconciled:**
  - against SQL and Python: 83/83 cases, 5.2 M values, max relative difference 2.9e-11, on the private data;
  - on the synthetic data: 83/83;
  - all 88 visual queries run.
- **Defects found by reconciliation and fixed in Power BI:** culture-aware rank tie-breaks, scope rows, NULL→"" in text measures, ranks under multi-column grouping, tooltips inflating top-N charts, and a 60k-row table exhausting memory.

## 10. Evaluation (summary; details in `EVALUATION_RESULTS.md`)
- **Tests:** 878 passed. They cover:
  - data reconciliation, SQL, Python, parity, scoring and scenarios;
  - app contracts, agents, evaluation, UI;
  - Power BI, synthetic generation, leakage and publication.
- **Traces:** agent answer → tool field → SQL → independent Parquet re-query → Python engine. They agree to 1e-9 (`docs/FINAL_AUDIT.md`).

## 11. Findings from the synthetic data (illustrative only; invented market)
Produced by the deterministic tools on `PCI_DATASET=synthetic`, MAT to May 2024:
- The synthetic national market is ₹7,532.9 cr MAT, growing **+10.3%**.
- The largest contributor is the invented therapy area "INFECTION DEFENCE": +3.56 pp of growth (+19.8%). "BONE & JOINT CARE" is the only declining area (−2.8%).
- Acute therapies are 67.7% of value and grow faster than chronic (+11.4% vs +8.0%).
- Concentration is moderate: the top company holds 8.0% and the top three 21.9%.
- The highest-scoring markets on OPP-1.0.0 score about 91–92 (descriptive percentile evidence, not a forecast).
- Data-quality status: PASS on all structural checks.

These numbers show the product working end to end. They say nothing about any real market.

## 12. Limitations
- National data only: no geography, channel, prescriber or promotion data.
- Units scale inferred.
- Opportunity weights are judgement-based, and scores are relative within one period and version.
- Scenarios have no behavioural response.
- The default AI mode understands only its documented grammar.
- The live LLM evaluation and the Stitch/Antigravity redesign have not been run.
- The git history needs an orphan snapshot before publication.

## 13. Demonstration workflow
See `DEMO_GUIDE.md`: generate synthetic data, then run the app and walk the Executive Overview through to a scenario and an AI Analyst question with evidence. Then build Power BI on the synthetic data.

## 14. Roadmap
1. Finish the live Gemini evaluation on the synthetic set (24 cases left, free-tier quota) and compare it with the deterministic grammar.
2. Stitch/Antigravity visual refinement (handoff ready).
3. A publication snapshot with a licence.
4. Optional: synthetic public screenshots.
5. A small set of parameterised report exports.
