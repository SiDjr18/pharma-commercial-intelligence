# SQL ANALYTICS LAYER (M4)

## Files (executed in order by `pci_data.db.connect()`)
| File | Contents |
|---|---|
| `sql/views.sql` | M3 base views over Parquet + dimension views (unchanged in M4) |
| `sql/periods.sql` | `period_basis`: every anchor month × {MONTH, YTD, MAT} with current/comparison windows and completeness flags |
| `sql/entities.sql` | `entity_type_catalog`, `pack_entity`: pack → entity membership for 15 partition types (market, company, product, segment) |
| `sql/metrics.sql` | Generic engine: `entity_period(...)` table macro (level metrics) and `entity_trend(...)` (monthly series) |
| `sql/domains.sql` | Thin domain macros: `market_performance`, `market_trend`, `product_performance`, `product_trend`, `company_performance`, `therapy_performance`, `segment_performance` |

There are no materialised copies: everything is views and macros over the M3 Parquet files. One generic engine avoids duplicating the growth, share and rank logic across domains.

## Time logic (source-reproduced)
| Basis | Label | Current window | Comparison window | First valid anchor | First anchor with growth |
|---|---|---|---|---|---|
| `MONTH` | Month | anchor month | same month, previous year | 2021-06 | 2022-06 |
| `YTD` | **Calendar YTD** | Jan … anchor month (same calendar year) | same months, previous year | 2022-01 | 2023-01 |
| `MAT` | MAT (moving annual total) | 12 months ending at anchor | 12 months ending 12 months earlier | 2022-05 | 2023-05 |

- **Source evidence:** `CUMM MAY'YY` = Σ Jan–May (calendar), and `MAT MAY'YY` = Σ Jun(YY-1)–May(YY); both reconciled 100% in M3. The pivot's 18 growth fields compare MAY'YY with MAY'(YY-1) for MONTH, CUMM and MAT.
- **The source YTD is never labelled as financial year.** Indian FY (Apr–Mar) is not implemented.
- **Completeness:** data run gap-free from 2021-06 to 2024-05 (M3 test). A window is complete only if it starts on or after 2021-06.
  - If the current window is incomplete, the macro returns no rows, and the API raises `period_unavailable`. Partial YTD 2021 and MAT before 2022-05 are never computed.
  - If the comparison window is incomplete, the prior value and growth are NULL, and `value_growth_status = 'prior_unavailable'`.
- **Partial YTD** means Jan … anchor month, e.g. a March anchor gives a 3-month YTD compared with the previous Jan–Mar. This is exactly the source CUMM definition, not an annualisation.

## Metric definitions (`entity_period` output)
| Field | Definition |
|---|---|
| `value_cur`, `units_cur`, `qty_cur` | Σ over packs in entity ∩ scope and over months in the current window |
| `value_prior`, … | Same, comparison window; NULL if the comparison window is incomplete |
| `value_abs_chg` | `value_cur − value_prior` |
| `value_growth_pct` (units, qty alike) | `cur / prior × 100 − 100` (source pivot formula) when prior > 0; **NULL otherwise, never 0** |
| `value_growth_status` | `ok` \| `prior_zero` (prior = 0, cur > 0) \| `no_sales` (both 0) \| `prior_unavailable` |
| `scope_value_cur/prior` | Σ over all entities in scope (the share denominator) |
| `value_share_pct` | `value_cur / scope_value_cur × 100` |
| `value_share_prior_pct`, `value_share_chg_pp` | prior share; change in percentage points |
| `units_share_pct` | unit share in scope |
| `contribution_to_growth_pp` | `(value_cur − value_prior) / scope_value_prior × 100`; sums to scope growth |
| `evolution_index` | `share_cur / share_prior × 100` = `100 × (1+g_entity)/(1+g_scope)`; NULL if prior share is 0 or unavailable |
| `rank_value` | `row_number()` by `round(value_cur, 9)` desc, then `entity_key` asc |
| `rank_growth` | by `round(cur/prior, 9)` desc, NULLS LAST, then rounded value desc, then key |
| `n_packs`, `n_entities` | packs in entity∩scope; entities in result |

Negative values do not occur (M3 test). Growth on negative bases is therefore not defined or needed.

**Ranking determinism (defect found and fixed during M4):** Parallel floating-point summation can differ in the last bits between runs. Ranking on raw sums let exact ties (e.g. two molecules with identical sales) flip order against an independent Python implementation. Ranks now use values rounded to 1e-9 crore (₹0.01), and ties break on key byte order. Regression tests: `test_rank_is_deterministic_and_complete`, `test_rank_ties_exist_and_are_broken_by_key`, and the Python cross-check.

## Share validity
Shares and contributions are computed only **within one partition type and one scope**, so the denominator is the scope total and the members are mutually exclusive. Cross-type shares (e.g. subgroup share of a company) are not produced. Use `p_scope_type` / `p_scope_key` instead: company within market, product within market, segment within market.

## Tolerances
| Comparison | Tolerance |
|---|---|
| SQL vs M3 source snapshot sums | 1e-6 crore / '000 absolute |
| SQL vs pivot growth formula | 1e-6 percentage points |
| SQL vs independent Python (pyarrow) | 1e-9 relative + 1e-9 absolute |
| Shares sum / contribution sum | 1e-9 |
| Same query executed twice | up to ~1e-12 relative (parallel summation); rankings unaffected |

## Performance (warm, 4 threads, this laptop)
| Call | Time |
|---|---|
| connect + register all SQL | 0.38 s |
| market_performance(subgroup, MAT) — 1,889 rows | ~0.37 s |
| product_performance(total, MAT) top 20 of 60,079 | ~0.44 s |
| product_performance within a subgroup, YTD | ~0.30 s |
| company_performance(MAT) top 20 | ~0.33 s |
| therapy_performance(subgroup) | ~0.35 s |
| segment_performance(dosage_form, MONTH) | ~0.27 s |
| market_trend / product_trend (36 months) | 60–100 ms |

## Controlled-failure verification (performed, not committed)
Each injected defect was detected by the named test, and the restored code passes:

| Defect | Detected by |
|---|---|
| growth inverted | pivot-formula test |
| growth zero-filled | never-zero-filled test |
| YTD from April | snapshot YTD reconciliation |
| 13-month MAT | Python cross-check |
| pack duplicated into a second subgroup | one-entity-per-pack test and market reconciliation |
| tie-break reversed | tie test |
| scope filter dropped | scoped-shares test |

An on-disk mutation (growth formula without "− 100") failed 43 tests via pytest; the file was restored byte-identically.
