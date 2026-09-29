# API SPECIFICATION

Status (M4/M5): the functions below are **implemented and tested** in `python/pci_analytics/api.py` (`CommercialAnalytics`, SQL-backed; production) over the SQL layer (`docs/SQL_ANALYTICS.md`). They are the deterministic tools future agents will call. Agents must not compute metrics themselves.

**Independent twin (M5):** `pci_analytics.domains.PyCommercialAnalytics` exposes the same 9 functions with identical parameters, validation, error codes and envelope (`evidence.engine = "python"`), computed by the independent Python engine. It is used for cross-validation (`docs/PYTHON_ANALYTICS.md`); parity is tested for every function.

Application/agent access goes through the M8 tool boundary (`docs/TOOL_API.md`): the same functions exposed as whitelisted tools with JSON-Schema contracts and structured error codes.

```python
from pci_analytics import CommercialAnalytics, AnalyticsError
api = CommercialAnalytics()            # opens DuckDB views over the active dataset (synthetic default, or PCI_IMS_DATA_DIR)
api.get_market_performance("subgroup", "2024-05-01", "MAT", top_n=10)
```

## Common conventions
| Item | Rule |
|---|---|
| `anchor` | ISO date, any day of month (normalised to 1st). Data range 2021-06 … 2024-05 |
| `basis` | `MONTH` \| `YTD` (calendar Jan..anchor) \| `MAT` (12 months to anchor). Case-insensitive |
| Period validity | MONTH ≥ 2021-06; YTD ≥ 2022-01; MAT ≥ 2022-05, else `period_unavailable`. Growth needs one more year (MONTH ≥ 2022-06, YTD ≥ 2023-01, MAT ≥ 2023-05); otherwise prior/growth = null plus a caveat |
| Market levels | `total`, `supergroup`, `therapy_group`, `subgroup` (primary), `molecule` — see docs/MARKET_DEFINITION.md |
| Segments | `acute_chronic`, `indian_mnc`, `plain_combination`, `molecule_count`, `dosage_form`, `nfc1` — see docs/SEGMENT_DEFINITIONS.md |
| Keys | `entity_key` is the analytical key (product = `prod_code` as string); `entity_label` is display only |
| `top_n` | int 1..5000 or `None` (all) for in-process Python callers. **Tool endpoints (HTTP / agents, `pci_app.tools`) never return all rows:** `null`/absent = safe maximum 2,000 (500 for product lists); larger values are rejected. Ordering is always `rank_value` |
| Numbers | unrounded floats; value in ₹ crore, units '000 packs (inferred), qty '000 counting units (inferred) |

### Response envelope (every function)
```json
{ "function": "...", "filters": {...},
  "period": {"anchor", "basis", "basis_label", "cur_start", "cur_end", "prior_start", "prior_end", "prior_complete"},
  "units": {...}, "row_count": n, "total_rows": N, "rows": [ ... ],
  "evidence": {"sql": "...", "params": [...], "source_sha256": "...", "processed_built_at": "...", "definitions": [...]},
  "caveats": ["..."] }
```
`period` is null for trend and lookup functions. `total_rows` = rows matched before `top_n`.

### Metric row fields (performance functions)
`entity_type, entity_key, entity_label, scope_type, scope_key, anchor, basis, basis_label, cur_start, cur_end, prior_start, prior_end, n_packs, value_cur, value_prior, value_abs_chg, value_growth_pct, value_growth_status, units_cur, units_prior, units_abs_chg, units_growth_pct, qty_cur, qty_prior, qty_growth_pct, scope_value_cur, scope_value_prior, value_share_pct, value_share_prior_pct, value_share_chg_pp, units_share_pct, contribution_to_growth_pp, evolution_index, rank_value, rank_growth, n_entities`. Definitions: docs/SQL_ANALYTICS.md.

### Trend row fields
`period, period_index, value_cr, value_cr_prior, value_growth_pct, units_k, units_k_prior, units_growth_pct, value_ytd, value_ytd_prior, value_ytd_growth_pct, value_mat, value_mat_prior, value_mat_growth_pct, units_mat, units_mat_prior, units_mat_growth_pct` (36 dense months; NULL where the window is not available).

### Errors (`AnalyticsError.code`, `.to_dict()`)
| Code | When |
|---|---|
| `invalid_parameter` | bad basis/date/level/segment/top_n; wrong entity type for a function |
| `period_unavailable` | anchor outside data, or current window starts before 2021-06 |
| `unsupported` | geography/region/state, channel, SSA/HSA/DSA, HCP/prescriber, brand_name-as-key |
| `not_found` | unknown market/company/product key, or product has no packs in the requested market |

Empty results return `rows: []` plus the caveat "No rows matched the request." (e.g. a company filter with no products in the market).

## Functions

### `find_products(name_contains, limit=50)`
Resolves a brand-name fragment (case-insensitive, **literal** substring — `%`/`_` are not wildcards; fixed in M5) to candidate products. **Returns:** `prod_code, brand, company, manufacturer_code, prod_launch_month`. Brand names are not unique (658 names shared across companies), so the caller or agent must disambiguate before calling product functions.

### `get_market_performance(level="subgroup", anchor="2024-05-01", basis="MAT", top_n=None)`
Every market at `level` with value/units/qty, growth, share of total, contribution and ranks. **Macro:** `market_performance`.

### `get_market_trends(level="total", key="TOTAL")`
36-month dense series for one market, with month-YoY, calendar-YTD and MAT values and growth at every month. **Macro:** `market_trend`.

### `get_brand_performance(anchor, basis, market_level="total", market_key="TOTAL", company=None, top_n=20)`
Products (key `prod_code`; label brand + company) within a market scope (market level, `company`, or segment). Share and rank are within the scope. The `company` filter restricts rows but keeps the market denominator. Extra fields: `brand, company, manufacturer_code, prod_launch_month`. **Macro:** `product_performance`.

### `get_brand_growth(prod_code, market_level="total", market_key="TOTAL")`
36-month trend for one product, optionally restricted to its packs in one market. **Macro:** `product_trend`.

### `get_brand_share(prod_code, market_level, market_key, anchor, basis)`
One product row within the given market: share, share change, EI, contribution and rank within the market. `not_found` if the product has no packs there. **Macro:** `product_performance … WHERE entity_key = ?`.

### `get_company_performance(anchor, basis, market_level="total", market_key="TOTAL", top_n=20)`
Companies (source `COMPANY`) in the scope. Extra field `indian_mnc`. **Macro:** `company_performance`.

### `get_therapy_performance(level="supergroup", anchor, basis, within_supergroup=None, top_n=None)`
`supergroup` | `therapy_group` | `subgroup`. Subgroup rows carry `therapy_group, supergroup, acute_chronic`. `within_supergroup` restricts to one therapy area; shares are then within that area. **Macro:** `therapy_performance`.

### `get_segment_analysis(segment, anchor, basis, market_level="total", market_key="TOTAL")`
All members of one segment within the scope (market level or `company`). **Macro:** `segment_performance`.

### `get_opportunity_scores(level="product", anchor, basis="MAT", market_level=None, market_key=None, company=None, include_insufficient=False, top_n=50)` (M6)
- **Levels:** `product` (product within its subgroup market; key = source INDEX) or `market` (subgroup).
- **Basis:** `MAT` or `YTD` only.
- **Filters** select rows without changing scores:
  - `market_level` ∈ {supergroup, therapy_group, subgroup} (product), or {supergroup, therapy_group} (market);
  - `company` (product only).
- **Envelope** additionally carries `methodology` (version, fingerprint, normalization, reference population and size, components with weights, thresholds), `status_counts` and `national_value_growth_pct`.
- **Row fields:** `entity_level, entity_key, entity_label, score, score_status, insufficient_reason, opportunity_rank, rank_in_selection, methodology_version, components[{name, label, metric, raw, normalized, weight, weighted_contribution}], positive_drivers, constraints`, plus raw metrics. Product rows add `prod_code, brand, company, subgroup, therapy_group, supergroup, value_cur/prior, value_growth_pct, market_value_*, value_share_in_market_pct, evolution_index, active_products_in_market, matrix_quadrant`. Market rows add `value_share_of_total_pct, acute_chronic`.
- **Ordering:** scored rows by `opportunity_rank`; insufficient rows (only if `include_insufficient`) come after, ordered by key.
- **Errors:** `invalid_parameter` (level, basis, filter combination, top_n), `period_unavailable`, `unsupported` (e.g. geography), `not_found`.

### `get_opportunity_detail(level, entity_key, anchor, basis="MAT")` (M6)
Returns one entity's full breakdown, including insufficient ones (with reason), and the methodology block. `not_found` if the key does not exist.

Both functions exist on `CommercialAnalytics` and `PyCommercialAnalytics` and delegate to the single implementation `pci_analytics.opportunity`. The first call loads the Python engine (~0.7 s, ~140 MB).

### `run_scenario(scenario_type, entity_type, entity_key, assumptions, anchor="2024-05-01", basis="MAT", market_level=None, market_key=None)` (M7)
- **Scenario types:**
  - `PRICE_CHANGE` {price_change_pct}
  - `VOLUME_CHANGE` {volume_change_pct}
  - `PRICE_VOLUME_CHANGE` {price_change_pct, volume_change_pct}
  - `MARKET_GROWTH` {market_growth_pct} — market entities only
  - `MARKET_SHARE` {target_share_pct, optional market_growth_pct} — product/company with required market_level/market_key as the share denominator
- **Basis:** MONTH / YTD (calendar) / MAT; the window must be complete.
- **Returns** the scenario contract: `status, scenario_id, scenario_type, methodology_version, entity, period, baseline (OBSERVED), assumptions (ASSUMED), scenario_result / absolute_change / percentage_change (CALCULATED), units, calculation_basis, assumptions_and_limitations, evidence`.
- **Errors:** `invalid_parameter`, `invalid_assumption`, `period_unavailable`, `not_found`, `insufficient_baseline`, `unsupported` (elasticity, forecast, geography, channel, HCP).
- **Not a forecast.** See docs/SCENARIO_ENGINE.md.

### `get_scenario_baseline(entity_type, entity_key, anchor, basis="MAT", market_level=None, market_key=None)` (M7)
Returns the OBSERVED baseline (value, units, qty, ₹/pack, ₹/counting unit, packs; plus market value and share when a market is given). Uses the same validation and errors.

## Not implemented (by design, at this milestone)
| Function | Status |
|---|---|
| `get_geography_performance()` | **Unsupported by source** (no geography) — any geography request raises `unsupported` |
| `get_data_quality_status()` | **Implemented (Phase 3):** deterministic structural checks of the active dataset (keys, dense grain, nulls/negatives, snapshot MAT = Σ months, subgroup→supergroup, product→brand) plus informational counts and coverage. Counts only; tool `get_data_quality_status`, agent `DataQualityAgent`, intent `DATA_QUALITY`. |
