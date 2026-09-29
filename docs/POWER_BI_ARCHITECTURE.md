# POWER BI ARCHITECTURE (M12)

Power BI is a **presentation and analysis layer** over the same processed data as the application. It is never a second source of truth:

```
data/processed/*.parquet  →  SQL engine (M4, reference)  →  independent Python engine (M5; canonical M6 opportunity, M7 scenarios)
                                                         ↘  Power BI semantic model (M12)  →  reconciled field-by-field against both
```

Code: `python/pci_powerbi/` (generator, export, local Desktop bridge, reconciliation). Output: `dashboards/PCI_Commercial_Intelligence.pbip` — a Power BI Project (PBIP) in text form: a TMDL semantic model plus a PBIR report. It is generated, not hand-edited, and a test fails if the committed definition drifts from the generator.

| Command (from `python/`) | Purpose |
|---|---|
| `..\.venv\Scripts\python.exe -m pci_powerbi.export` | Export canonical OPP-1.0.0 scores → the active dataset's `powerbi/` folder: `data/synthetic/powerbi/` (public mode, git-ignored) or `PCI_IMS_DATA_DIR/powerbi/` (IMS mode, outside the repository). ~2.5 min, streamed |
| `..\.venv\Scripts\python.exe -m pci_powerbi.build` | Regenerate the PBIP (text only; no data) |
| open `dashboards\PCI_Commercial_Intelligence.pbip` in Power BI Desktop → **Refresh** | Import the data (~45 s) |
| `..\.venv\Scripts\python.exe -m pci_powerbi.reconcile` | Reconcile the live model against the engines (Desktop must be open and refreshed) |

## 1. Data source
| Power BI table | Source file (exact) | Grain | Rows |
|---|---|---|---|
| `Pack Month` (fact) | `data/processed/fact_pack_month.parquet` | pack (PFC) × calendar month, dense | 3,791,412 |
| `Pack` | `data/processed/pack.parquet` (10 columns) | pack (PFC) | 105,317 |
| `Product` | `pack.parquet`, grouped by `prod_code` | product (PROD_CODE) | 60,079 |
| `Market` | `pack.parquet`, grouped by `subgroup` | therapy subgroup | 1,889 |
| `Company` | `pack.parquet`, grouped by `company` | company | 1,077 |
| `Period` | calculated from the fact's own min/max month | month | 36 |
| `Opportunity Product` | `<PowerBIFolder>/opportunity_product.parquet` | anchor × basis × product-in-subgroup | 1,961,940 |
| `Opportunity Market` | `<PowerBIFolder>/opportunity_market.parquet` | anchor × basis × subgroup | 56,670 |
| `Anchor`, `Basis`, `Scenario Type`, `Price Change`, `Volume Change`, `Market Growth Assumption`, `Target Share` | calculated (DAX) | selection/parameter tables, disconnected | — |

Not used: the raw IMS workbook, `pack_snapshot` (reconciliation only, as in the app), `pack_price_month` (price is derived as in M7), and any external dataset, API or cloud service. The two Power Query parameters `ProcessedFolder` and `PowerBIFolder` point at local folders. `PowerBIFolder` is `data/synthetic/powerbi/` in public mode and `PCI_IMS_DATA_DIR/powerbi/` in IMS mode. Before P1 isolation (2026-09-28), the IMS export was written to `data/processed/powerbi/` (historical location, still git-ignored). Import mode is used because Power BI cannot query Parquet in DirectQuery. Its compressed cache (`.pbi/cache.abf`) is the only copy, and it is git-ignored.

## 2. Model (star schema)
```
Period 1─* Pack Month *─1 Pack *─1 Product
                              Pack *─1 Market      (subgroup; hierarchy Supergroup → Subgroup)
                              Pack *─1 Company
Opportunity Product *─1 Product / Market / Company        Opportunity Market *─1 Market
Anchor, Basis, scenario parameters: disconnected (read by measures only)
```
- All relationships are many-to-one and single-direction. There is no bidirectional or many-to-many relationship, so double counting through relationships is impossible. The two fact tables share dimensions but have no path to each other.
- Market and segment attributes live at pack grain because a product can span subgroups and molecules (DATA_MODEL.md). Product-in-market is expressed as a Product × Market filter, never as a brand.
- **Product identity = `Product[Product Code]`.** `Product[Product]` is a unique display label "BRAND (COMPANY) · code". Brand + company is *not* unique (59,991 combinations for 60,079 products). `Brand` is display-only, and no visual uses it (tested).
- The measure table is named `Metrics`, because `Measures` is a reserved table name in Power BI.
- **Key safety:** Power BI compares text case-insensitively and ignores trailing spaces, while DuckDB does neither. `export.assert_keys_powerbi_safe()` proves that no key column collapses (all 11 key columns checked). The export fails if that ever changes.

## 3. Measures (157; display folders 0–8)
| Folder | Measures | Definition source |
|---|---|---|
| 0 Period | Anchor Month, Basis Selected, Current/Prior Start/End, Current/Prior Complete, First/Last Data Month, Window Months, Period Caption | `sql/periods.sql` |
| 1 Core | Value, Value Prior, Value Change, Value Growth %, Growth Status, Growth Evidence, Units (+Prior, Change, Growth %), Qty (+Prior, Growth %), Packs | `sql/metrics.sql` entity_period |
| 2 Market scope | Market Value (+Prior), Market Units, Market Growth %, Share of Market % (+Prior), Share Change pp, Units Share of Market %, Contribution to Market Growth pp, Evolution Index; hidden raw `Scope …` totals | scope = current filters minus Product and Company |
| 3 National | National Value (+Prior, Units, Growth %), Share of Total % (+Prior, Change pp), Units Share of Total %, Contribution to Total Growth pp, Evolution Index vs Total, Therapy Area Value (+Prior), Share of Therapy Area %, Contribution to Therapy Area Growth pp | scope = total market / the entity's supergroup |
| 4 Trend | Trend Value, Trend Value Prior Year, Trend Growth %, Trend Units (+Prior Year) | `entity_trend` (axis month = anchor) |
| 5 Rank | Rank Value / Rank Growth for Product, Company, Subgroup, Supergroup, Therapy Group, Molecule | row_number over the key rounded to 1e-9, ties by key in **byte order** (hidden ordinal `… Order` columns built with `Comparer.Ordinal`; DAX text order is culture-aware). `RANK` runs over the entity's whole dimension table with `MATCHBY` on the key, so other grouped attributes (label, Indian/MNC) cannot distort it |
| 6 Display | top-N selectors (Top 10 gains/declines, Top 12 contribution, Top 8 share movers, Top 15 products/subgroups), colour measures, labels, coverage counts | selection and formatting only; the values come from the measures above |
| 7 Opportunity | score, rank, evidence, raw components, percentiles, quadrant, counts, availability message | imported canonical OPP-1.0.0 export |
| 8 Scenario | assumptions, entity resolution, status, OBSERVED baseline, CALCULATED results, decomposition, formula text | `docs/SCENARIO_ENGINE.md` (SCN-1.0.0) |

**Scope totals:** `Market Value`, `National Value` and `Therapy Area Value` are BLANK for an entity with no packs in scope, because SQL returns no row for it. The ratio measures read hidden raw totals (`Scope Market Value`, …), which the engine evaluates once per query; a per-row guard made the product table 2–4× slower.

**Evidence rules**
- Growth is `cur / prior × 100 − 100` only when prior > 0. Otherwise it is BLANK, and `Growth Status`/`Growth Evidence` give the reason (`prior_zero`, `no_sales`, `prior_unavailable`).
- There is no `COALESCE` or `+ 0` on any growth, share or index (tested).
- Share, contribution and EI are BLANK when their denominators are not positive, exactly as in SQL.
- A multi-selected anchor or basis gives BLANK everywhere, with "Select one anchor month and one basis". The model never silently picks one.

## 4. Period logic (identical to the application)
| Basis | Current window | Comparison window | Complete when |
|---|---|---|---|
| MONTH | anchor | anchor − 12 months | anchor ≥ first month (comparison from Jun 2022) |
| YTD (calendar) | Jan..anchor of the anchor's year | same months a year earlier | Jan of the anchor year ≥ first month (YTD from 2022; growth from 2023) |
| MAT | anchor − 11 .. anchor | shifted back 12 months | anchor ≥ May 2022 (growth from May 2023) |

- Defaults: no selection means the latest data month and MAT, the same as the application. The report's slicers are pre-set to those values: the latest month is derived from the data at build time and is never typed in.
- An incomplete current window shows no values, and the caption explains why.
- The Indian fiscal year is not used.

## 5. Market definition
- The primary market is the therapy **SUBGROUP**; each pack belongs to exactly one.
- The hierarchy is Total → Supergroup (therapy area) → Subgroup. Therapy group is a separate attribute, because it is not nested in supergroup (2 source exceptions), and roll-ups go via subgroup.
- Molecule is available as an alternative market lens (a Pack attribute).
- Segments (acute/chronic, Indian/MNC, plain/combination, molecule count, dosage form, NFC1) are source-confirmed classifications.
- Geography, channel, prescriber, promotion and SSA/HSA/DSA are **not** in the model (tested).

## 6. Pages (1280 × 720, M11 design language)
| Page | Question | Main visuals | Interactions |
|---|---|---|---|
| Executive Overview | What changed in the market? | headline card (value, growth, change, units growth, evidence); opportunity signal card; therapy-area contribution bars (±colour); value trend vs prior year; top-10 subgroup gains and declines; top-8 company share gainers/decliners | cross-filtering between visuals; synced anchor/basis |
| Market Intelligence | How are therapy markets moving? | matrix Supergroup → Subgroup (value, growth, share of total, share change, contribution, EI vs total, share of therapy area); trend; top-12 contribution bars | hierarchy expand/drill; row selection filters the trend; therapy-area and acute/chronic slicers |
| Brand & Portfolio | Which products are winning in their markets? | product table (code, label, value, growth, share of market, share change, EI, rank, growth evidence) with a **Top 500 by value** visual filter; top-15 bars; trend | market/company/product search slicers; **drill-through → Product detail** |
| Company Intelligence | How are companies performing, and where? | company table (value, growth, share of market, share change, contribution, EI, rank); share movers; "where does it compete" matrix | select company → portfolio by market; **drill-through → Company detail** |
| Opportunity Intelligence | Where is the strongest evidence of opportunity? | availability banner; market score table; 2×2 opportunity matrix (counts of scored products); evidence-status bars (exact counts); product-in-market score table; momentum × market-growth percentile scatter of the top 500 scored products | market/company/evidence slicers (they select rows; they never change scores) |
| Scenario Planning | What if price, volume, market growth or share changed? | scenario type; entity slicers; four assumption slicers; status, entity and formula cards; OBSERVED baseline; CALCULATED result; decomposition | not synced (except anchor/basis) |
| Methodology & Data Quality | How are these numbers defined, and can they be trusted? | coverage card; definitions; periods/market/identity; governance and limitations | — |
| Product detail / Company detail (hidden) | drill-through targets | KPI card, trend, markets/portfolio | Back button |

- **Visual language:** the theme is generated from `app/web/style.css` tokens (`pci_powerbi/theme.py`): warm paper canvas, white panels with 1-px line borders, ink-first series, one teal accent, and green/red only paired with signed numbers. Titles are Georgia questions. There are no gauges, donuts, 3-D visuals, gradients or rainbow palettes (tested).
- **Filters:** the anchor and basis slicers are synced across all pages (sync groups `anchor`, `basis`). Market and company slicers are page-local.
- **Bookmarks:** none. They would duplicate slicer state without analytical value.
- **Tooltips:** default tooltips carry supporting measures (growth, share).
- **Export of underlying rows is disabled:** `exportDataMode = AllowSummarized`.

## 7. Opportunity methodology in Power BI
- Scores come **only** from `pci_analytics.opportunity.score_all` (single canonical implementation, OPP-1.0.0, fingerprint `ac4362f187f25004`). `pci_powerbi.export` copies its fields verbatim for every anchor whose window and comparison window are complete: MAT May 2023–May 2024 (13 anchors) and calendar YTD Jan 2023–May 2024 (17 anchors).
- Earlier anchors, and MONTH, show an explicit availability message instead of rows (insufficient_history; MONTH excluded as too noisy).
- DAX never ranks, weights or normalises. Tests assert that no weight literal or RANK appears in opportunity measures.
- Insufficient evidence is a visible status with its reason. It is never a zero score.
- The reference population is national, so filters select rows only.
- Omitted from the import to save memory, because no visual uses them: weighted contributions (= 100 × weight × percentile), the position percentile and the brand. The full decomposition remains available in the app and tool API.

## 8. Scenario methodology in Power BI
All five M7 types are implemented with the SCN-1.0.0 formulas, in the same arithmetic order as `scenario.py`:
- price = value × 10⁷ / (units × 10³)
- value₁ = price₁ × units₁ × 10³ / 10⁷
- value₁ = value₀ (1 + g), or t × market₀ (1 + g)
- decomposition: price effect, volume effect, interaction

The baseline is the observed `[Value]`, `[Units]` and `[Qty]` of **one** entity in the selected window:
- **Entity resolution:** one product, or one company, or neither (a market entity). The most specific single market filter is the market: subgroup, therapy group, supergroup, molecule, or total when none is set.
- **Assumptions:** these come only from parameter tables that contain exactly the allowed M7 grid. They cannot express an out-of-range value, so nothing is ever clamped:
  - price (−100, 1000], step 1
  - volume and market growth [−100, 1000], step 1
  - target share [0, 100], step 0.1
- **Rejected, never corrected:** a missing, multiple, extra or out-of-grid assumption, an invalid entity for the type, multiple entities, a combined molecule/therapy-group/supergroup market, segment filters, an incomplete window, or an insufficient baseline (units 0, market 0). Each gives a status text and no result.
- Outputs are labelled OBSERVED or CALCULATED, and the page states "SCENARIO ANALYSIS — NOT A FORECAST".

Limitations: integer-percent grids (the engine accepts any real number in range), and one scenario at a time. There is no elasticity, forecasting or promotion effect, because none exists in M7.

## 9. Reconciliation method
- `python -m pci_powerbi.reconcile` runs a DAX query per case against the live model. It uses Power BI Desktop's local Analysis Services engine through the ADOMD client that ships with Desktop (`as_bridge.ps1`: localhost only, no new dependency).
- Each result is compared with the reference engine:
  - numbers: relative difference ≤ 1e-9
  - text, status and ranks: exact
  - BLANK must equal NULL
  - entity-key sets must be identical
- Coverage: total market in every basis, including windows whose comparison predates the data; all supergroups, therapy groups, subgroups and molecules; all companies (national and within a market); all 60,079 products (national and within markets); subgroups within a therapy area; six segments; 36-month trends for the total, a subgroup and a product in all three bases; evidence guards; opportunity (imported vs canonical, plus model metrics vs the score inputs); 14 scenario cases (10 valid, 4 rejected); one query per report visual.
- The committed summary is `evaluation/reports/POWER_BI_RECONCILIATION.md`. It holds no values. Detailed JSON/CSV with values stays local and git-ignored.

### Results (final run, 2026-09-27, Power BI Desktop 2.157.1354.0)
| Cases | Pass | Fail | Values compared | NULL/BLANK checks | Max relative difference | Visual queries | Failures |
|---|---|---|---|---|---|---|---|
| 83 | 83 | 0 | 5,195,694 | 512,103 | 2.9e-11 | 88 | 0 |

By area:
- market 23 (incl. one three-way SQL = Python = Power BI check)
- scenario 14
- opportunity 10
- trend 9
- segment 7
- guards 6
- company 5
- visual-shape 5
- product 4 (full 60,079-product populations)

The remaining differences are floating-point summation order only (≤ 3e-11 relative).

**Discrepancies found and resolved.** In every case Power BI was changed. The reference engines were never changed.

| # | Found by | Discrepancy | Root cause | Resolution |
|---|---|---|---|---|
| 1 | molecule ranks | 75 rank values off by 1–24 among zero-value molecules | tie-break by text: DAX culture order (ignores `-`) ≠ SQL byte order | ordinal `… Order` columns (`Comparer.Ordinal`) used as the tie-break |
| 2 | company/product within a market | Power BI returned entities with no packs in scope | scope totals non-blank for every entity | scope totals BLANK when the entity is BLANK; raw hidden totals for ratios |
| 3 | opportunity quadrant | `""` instead of BLANK for insufficient-evidence products | `CONCATENATEX` turns NULL into an empty string | text fields return BLANK for NULL |
| 4 | visual-shape probe | every product ranked 1 when a visual groups code **and** label | `ALLSELECTED(code)` kept the label filter during ranking | `RANK` over the whole dimension table + `MATCHBY` key; 5 visual-shape cases added |
| 5 | visual smoke test | "Top 15" bar pulled all 60,079 products | tooltip measures are non-blank for every category | top-N bars carry only their top-N measure (tested) |
| 6 | visual smoke test | product table hit the engine memory limit (7 GB) / 90 s timeout | 60,079 rows × all measures | Top 500 visual filter (2.2 s) and raw scope totals |


## 10. Performance
- **Import:** ~45 s full refresh on this laptop.
- **Engine memory:** ~87 MB of column segments after optimisation (fact 36 MB, opportunity 45 MB); the Desktop engine working set is ~1.2 GB.
- **Decisions:**
  - Import only the needed Parquet columns.
  - One dense fact at the source grain (no duplicated fact).
  - Dimensions are grouped from `pack.parquet` in Power Query (no extra files).
  - `isAvailableInMdx = false` on aggregate-only numeric columns.
  - No auto date/time tables (`__PBI_TimeIntelligenceEnabled = 0`).
  - Measures instead of calculated columns.
  - Window sums are one CALCULATE over a contiguous `Period` range.
  - Ranks use the DAX `RANK` window function, not O(n²) RANKX patterns.
  - The opportunity export is streamed per anchor (bounded memory) and trimmed to the columns the report uses (the first version was 330 MB in the engine).
- **Query timings:** all 88 visual queries complete in 1.7–2.5 s end to end (median 1.8 s), including ~1.5 s of bridge start-up per query.
- **Safety:** the bridge sets a server-side query timeout (`-TimeoutSec`, default 600 s), so a runaway query is cancelled by the engine instead of exhausting laptop memory.

## 11. Privacy and Git
- The PBIP definition is text without data. Tests fail if a `.pbix`, `.pbit`, `.abf`, `.parquet`, `.csv` or `.xlsx` appears under `dashboards/`.
- Git-ignored: `*.pbix`, `*.pbit`, `*.abf`, `**/.pbi/` (Desktop cache and local settings), `data/synthetic/powerbi/` (public export), `evaluation/reports/powerbi_reconciliation*`, and the historical `data/processed/powerbi/`. The IMS export is written to `PCI_IMS_DATA_DIR/powerbi/`, outside the repository.
- Slicer defaults in the definition contain only "May 2024" and "MAT" (tested).
- No external service, API, sign-in or publish step is used. Power BI Desktop settings (including usage-data telemetry) were **not changed** by the build. Users who want no telemetry should turn off *Options → Global → Usage data* themselves.
- Screenshots of this report show licensed IMS aggregates and must not be published. Public screenshots come from the M13 synthetic dataset. QA captures live only in the git-ignored `.cache/`.

## 11b. Synthetic data target (M13)
The same model and report build on the synthetic dataset **without** touching the committed private project:

```
set PCI_DATASET=synthetic
python -m pci_powerbi.export      # -> data/synthetic/powerbi/
python -m pci_powerbi.build       # -> dashboards/_synthetic/ (git-ignored); parameters point at data/synthetic
python -m pci_powerbi.reconcile   # with the synthetic project open -> POWER_BI_RECONCILIATION_SYNTHETIC.md
```

`build` refuses to write a synthetic-data project over `dashboards/` (tested). With `PCI_DATASET` unset, the committed private default is unchanged.

## 12. Limitations
- Reconciliation needs Power BI Desktop open on this Windows machine; the offline tests check the definition, not the engine.
- Ranks are "within the current selection". With a company filter on the product table, ranks are within that company, whereas the SQL API ranks nationally before filtering.
- Rank tie-breaks use key order. DAX text ordering is case-insensitive and DuckDB's is byte order; this only matters for exact ties, and the reconciled populations showed none that differed.
- Scenario assumptions are limited to the grids above.
- Opportunity scores exist only for exported anchors and cannot be recomputed with other weights in Power BI (by design).
- Units/qty scale is inferred; value in ₹ crore is confirmed. Data is national only.
