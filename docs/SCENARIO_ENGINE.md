# SCENARIO ENGINE — SCN-1.0.0 (M7)

Canonical implementation: `python/pci_analytics/scenario.py` (Python only; no SQL copy). API: `run_scenario()`, `get_scenario_baseline()` on both API classes.

> **A scenario is deterministic what-if arithmetic on an observed baseline under explicit assumptions. It is not a forecast, prediction or probability.** No elasticity, seasonality, competitor reaction or statistical model is involved, and no LLM does any calculation.

## 1. Supported scenario types
| Type | Entities | Assumptions (required / optional) | What changes | Held constant (stated in the response) |
|---|---|---|---|---|
| `PRICE_CHANGE` | any of the 15 validated entity types (product, product-in-subgroup, company, manufacturer, market levels, segments), optionally within a market | `price_change_pct` | price | units, qty (**no elasticity**) |
| `VOLUME_CHANGE` | same | `volume_change_pct` | units, qty | price, pack mix |
| `PRICE_VOLUME_CHANGE` | same | `price_change_pct`, `volume_change_pct` | price, units, qty | pack mix; price and volume are linked only through the two assumptions |
| `MARKET_GROWTH` | market entities: total, supergroup, therapy_group, subgroup, molecule | `market_growth_pct` | market value | units/price not modelled |
| `MARKET_SHARE` | product or company **within a stated market** (`market_level`/`market_key`, the share denominator) | `target_share_pct` / `market_growth_pct` (default 0) | entity value | share moves to/from the rest of the market; market changes only by the stated growth |

## 2. Unsupported (explicitly rejected with `unsupported`)
| Request | Reason |
|---|---|
| `PRICE_ELASTICITY` / automatic volume response to price | The source has no price experiments or controls, so elasticity can't be estimated. Supply `volume_change_pct` explicitly instead. |
| `FORECAST` | No forecasting model exists; the engine is what-if only |
| Geography, channel (SSA/HSA/DSA), HCP, promotion | Not in the source, or meaning unconfirmed |

## 3. Price definition and unit conversion
| Quantity | Source unit | Status |
|---|---|---|
| `value_cr` | ₹ crore; 1 crore = ₹10,000,000 | CONFIRMED |
| `units_k` | thousand packs; 1 unit = 1,000 packs | INFERRED |
| `qty_k` | thousand counting units | INFERRED |

**Derivation:**
- `price [₹/pack] = value_cr × 10^7 / (units_k × 10^3) = 10,000 × value_cr / units_k`
- `value_cr = price × units_k × 10^3 / 10^7`
- The same formula with `qty_k` gives ₹ per counting unit.

**Source reconciliation (tested, `test_derived_price_reproduces_source_pr`):**
- The source `PR_MON'YY` field exists for Dec 2023 – May 2024 only.
- For every pack with units > 0 in all 6 months, all 25 therapy areas and both years (2023, 2024), about 463k pack-months, the derived price equals source PR to ≤ 4.4e-16 relative (machine precision). The 10^4 factor is exactly the source's own definition.
- This corroborates the crore / '000 interpretation. If units were raw packs, the factor would be 10^7.

**Differences documented, not forced:**
- When units = 0, the source carries the previous month's price forward (11,561 of 11,568 checked cases). The derived price is **undefined** (`None`), and price-based scenarios return `insufficient_baseline`.
- Months before Dec 2023 have no source price field; the price there is derived only.
- For aggregates (product, market, company), price is the **value-weighted average** (Σvalue / Σunits). The source has no aggregate price field.

## 4. Baseline definition (OBSERVED)
- The baseline is `metrics.entity_period` from the M5-validated Python engine, which equals M4 SQL (tested for product, market and share baselines across MONTH/YTD/MAT).
- Periods: MONTH, **Calendar YTD** (Jan..anchor, no Indian FY), MAT. The window must be complete (MAT ≥ 2022-05, YTD ≥ 2022-01), otherwise `period_unavailable`. No prior window is needed.
- `market_level`/`market_key`: restricts the baseline to the entity's packs in that market and supplies the share denominator (`market_value_cr` = the market's value in the same window).

## 5. Formulas (CALCULATED)
With p = `price_change_pct` / 100, v = `volume_change_pct` / 100, g = `market_growth_pct` / 100 and t = `target_share_pct` / 100:

| Type | Formula |
|---|---|
| Price / volume | `price₁ = price₀(1+p)`; `units₁ = units₀(1+v)`; `qty₁ = qty₀(1+v)`; `value₁ = price₁ × units₁ × 10³ / 10⁷ = value₀(1+p)(1+v)` |
| Decomposition | price effect = value₀·p, volume effect = value₀·v, interaction = value₀·p·v (sums exactly to Δvalue) |
| Market growth | `value₁ = value₀(1+g)` |
| Market share | `market₁ = market₀(1+g)`; `value₁ = t × market₁`; share change = target − baseline share (pp) |
| Changes | absolute = scenario − baseline; percentage = (scenario − baseline) / baseline × 100, **undefined (null) when baseline = 0** |

## 6. Assumption validation (rejected, never corrected: `invalid_assumption`)
| Assumption | Allowed | Why |
|---|---|---|
| `price_change_pct` | (−100, 1000] | price must stay > 0 |
| `volume_change_pct` | [−100, 1000] | units ≥ 0 (−100 gives zero volume) |
| `market_growth_pct` | [−100, 1000] | value ≥ 0 |
| `target_share_pct` | [0, 100] | a share can't be negative or above 100 % |

Other rules:
- Assumptions must be finite numbers. Booleans, strings, NaN and ∞ are rejected.
- Missing required or unexpected extra keys are rejected.
- The +1000 % ceiling is a sanity guard (a methodology assumption).

## 7. Errors (`AnalyticsError.code`)
| Code | When |
|---|---|
| `invalid_parameter` | bad basis/date/scenario type/entity type; the wrong entity for a type (e.g. MARKET_GROWTH on a product); MARKET_SHARE without a market |
| `invalid_assumption` | see §6 |
| `period_unavailable` | anchor outside 2021-06..2024-05, or incomplete window |
| `not_found` | unknown entity/market key, or the entity has no packs in the market |
| `insufficient_baseline` | units = 0 (price undefined) for price/volume types; market value = 0 for growth/share |
| `unsupported` | elasticity, forecast, geography, channel, HCP |

## 8. Response contract
The response contains these fields:
- `status`, `function` ("run_scenario")
- `scenario_id`: `SCN-` + sha256 of (version, type, entity, period, assumptions). It is deterministic.
- `scenario_type`, `methodology_version`, `entity` (type, key, label, scope), `period`
- `baseline` (**kind OBSERVED**), `assumptions` (**kind ASSUMED**, with `implicit_premises`)
- `scenario_result`, `absolute_change`, `percentage_change` (**kind CALCULATED**)
- `units` (labels with conversions), `calculation_basis` (formulas), `assumptions_and_limitations` (the disclaimer comes first), `evidence`

`get_scenario_baseline` returns the OBSERVED block only.

## 9. Validation
`tests/test_scenario.py`, 48 tests:
- **Hand-calculated synthetic cases:** ₹20/₹30/₹100 packs; product baseline 0.005 cr, 2,000 packs, ₹25; +10 % price → 0.0055 cr; +20 % volume → 0.006 cr; both → 0.0066 cr (+32 %) with a 0.0005 / 0.001 / 0.0001 decomposition; MAT 0.115 cr / 46k packs; market +5 %; share 50 → 60 % (+ market +10 %).
- **Unit conversion:** 1 crore / 1,000 packs = ₹10,000 per pack.
- **Other synthetic checks:** zero and edge values, insufficient baselines, determinism and scenario IDs, response schema, and no forecast language outside the disclaimer.
- **Real data:** source price reconciliation; baselines = M4 SQL; real arithmetic identities; 23 error cases; SQL API delegates to the same engine.
- **Fault injection (permanent, auto-restored):**
  1. wrong crore conversion
  2. price change applied to units
  3. volume change applied to price
  4. wrong percentage formula
  5. baseline window shifted a month
  6. out-of-range share accepted
  7. negative price/volume accepted
  8. scenario labelled OBSERVED
  9. wrong unit label
  10. wrong value calculation

  All 10 are detected.

## 10. Performance (warm; the first call loads the Python engine, ~0.7 s)
| Call | Time |
|---|---|
| Product price / volume / combined scenario | ~30–35 ms |
| Subgroup market growth | ~18 ms |
| Total market growth | ~120 ms |
| Product share in subgroup | ~29 ms |
| Company share in a therapy area | ~280 ms |
| Baseline only | ~33 ms |

No data is materialised; baselines use the in-memory engine's cached window sums.

## 11. Limitations
- There are no behavioural links: price does not move volume unless you say so; competitors don't react; the market doesn't respond to share changes.
- Aggregate price is a value-weighted average, so a uniform % price change is assumed across all packs of the entity.
- The units/qty scale is inferred (though consistent with the source PR). The market definition is analytical. The data is national only.
- Scenario outputs are not observed data and must never be stored or presented as such.
