# PYTHON ANALYTICS & SQL/PYTHON CROSS-VALIDATION (M5)

## Purpose
This is a second, independent implementation of every validated M4 metric, used to cross-validate the SQL layer. SQL (DuckDB macros) and Python (pyarrow + plain Python) read the same M3 Parquet files but share **no calculation code**. When the two agree field by field, the results carry high confidence.

## Architecture (`python/pci_analytics/`)
| Module | Role | Uses SQL? |
|---|---|---|
| `api.py` | Production API `CommercialAnalytics` (M4, SQL-backed) | yes |
| `periods.py` | Month / Calendar YTD / MAT windows, comparison windows, completeness | no |
| `engine.py` | `DataEngine`: loads Parquet once with pyarrow; pack-level window sums (pyarrow group-by, cached per window); entity membership re-implemented from pack attributes | no |
| `metrics.py` | Pure metric functions (growth, status, share, contribution, EI) + `entity_period` / `entity_trend` (mirror the SQL macros field by field) | no |
| `rankings.py` | Deterministic value/growth ranking rules | no |
| `domains.py` | `PyCommercialAnalytics`: same 9 functions, parameters, errors and envelope as `CommercialAnalytics`, computed by the Python engine | no |
| `validation.py` | Cross-check framework, tolerance policy, validation matrix, CLI | runs both |

Only **definitions** are shared between the engines: `docs/SQL_ANALYTICS.md`, `docs/MARKET_DEFINITION.md` and `docs/SEGMENT_DEFINITIONS.md`. Parameter validation constants and the error type (`AnalyticsError`) are imported from `api.py`. They are not metric logic.

## Independent calculation methodology
| Step | SQL (M4) | Python (M5) |
|---|---|---|
| Read | `read_parquet` views | `pyarrow.parquet.read_table` (5 fact columns, once) |
| Window | `period_basis` view (SQL date arithmetic) | `periods.make_window` (integer month arithmetic) |
| Pack sums | DuckDB `SUM … FILTER` grouped by pfc | `pyarrow` filter + `group_by("pfc").aggregate(sum)` |
| Entity membership | `pack_entity` UNION ALL view | `engine.ENTITY_TYPES` key/label functions over pack dicts |
| Entity sums | DuckDB hash aggregate (parallel double sums) | `math.fsum` (correctly rounded) |
| Metrics | SQL CASE expressions | `metrics.growth_pct`, `share_pct`, `contribution_pp`, `evolution_index` |
| Ranks | `row_number()` over `round(x, 9)` | `sorted()` with `rankings._r` (mirrors DuckDB `round` semantics, verified on 9,000 values) |
| Trends | window functions (`lag`, `ROWS 11 PRECEDING`) | list slicing over a dense 36-month series |

## Metric definitions
These are identical to docs/SQL_ANALYTICS.md: value/units/qty current and prior, absolute change, growth, growth status, scope totals, value share (current and prior), share change, units share, contribution to growth, evolution index, `rank_value`, `rank_growth`, `n_packs`, `n_entities`. Growth is `cur / prior × 100 − 100` only when prior > 0; otherwise it is `None` with a status. It is never 0.

## Period handling
Calendar logic only; there is no Indian FY. First valid anchors: MONTH 2021-06, YTD 2022-01, MAT 2022-05. First with growth: 2022-06, 2023-01 and 2023-05. An incomplete current window returns `[]`. An incomplete comparison window gives `None` prior and growth with status `prior_unavailable`. All 108 anchor × basis windows are identical to SQL `period_basis` (`test_parity_with_sql_period_basis`).

## Numerical tolerance policy (`validation.py`)
| Field class | Rule |
|---|---|
| Structural: keys, labels, types, dates, window bounds, growth status, `n_packs`, ranks, `n_entities` | **exact** equality, including NULL-ness |
| Numeric: sums, growth, shares, contribution, EI | `abs(sql − py) ≤ 1e-9 + 1e-9·abs(py)`, NULL-ness exact |

- **Rationale.** The observed SQL run-to-run noise is ≤ ~1e-12 relative (parallel summation), and the observed SQL-vs-Python maximum is 1.5e-10 relative. The 1e-9 bound is tight enough that a business discrepancy at entity level would be visible. `n_packs` is compared exactly, so a dropped or duplicated pack can never hide inside the numeric tolerance.
- **Near-zero values.** Some results cancel to about zero (e.g. cur == prior gives an absolute change around 1e-18 and growth around 3e-14 pp). Relative error means nothing there. Those values are governed by the absolute bound (observed ≤ 5.7e-14), and the reported `max_rel_diff` ignores |value| < 1e-6.
- **Ranks.** Ranks are exact. Both engines rank on values rounded to 1e-9 crore, so float noise can't reorder ties.

## Validation coverage
- **Matrix:** 48 period cases + 6 trend cases = 54 checks, about 12.7 million field values compared, all passing.
  - Domains: total, therapy area (supergroup), therapy group, market/subgroup, molecule, company, manufacturer, product, product-in-subgroup, and all 6 segments.
  - Bases: MONTH, Calendar YTD and MAT; first valid month, YTD and MAT; first periods with growth.
  - Scopes: brand in market (subgroup, molecule), brand in company, company in market, subgroup in therapy area, segment in market, segment in company.
- **Edge cases (real data, both engines):**
  - zero prior (`prior_zero`, `no_sales`) and missing prior
  - unavailable windows
  - one-product market (113 exist)
  - zero market denominator (41 subgroups with zero MAT value)
  - rank ties (thousands of zero-value products)
  - duplicated display labels
  - products spanning subgroups (4,519; Python product total = Σ product-in-subgroup parts)
  - unclassified molecule
  - Plain ⇔ 1 molecule, and Combination ⇔ 2–10 molecules
  - SQL run-to-run noise within policy
- **Synthetic unit tests** (hand-computable numbers) for every metric function, window rule and ranking rule.
- **API parity:** all 9 functions return the same envelope (function, filters, period, units, counts, caveats), the same rows in the same order, and the same error codes.

## Fault injection (permanent tests, auto-restored via monkeypatch)
| Injected fault | Result |
|---|---|
| Growth formula missing "− 100" | detected |
| Comparison window 11 months back instead of 12 | detected |
| Share denominator missing one entity | detected |
| Ranking ascending instead of descending | detected |
| Market (scope) filter dropped | detected |
| SQL-side growth defect (fresh connection, modified macro) | detected by the Python engine |

Each test also asserts that the check passes before the fault is injected and after it is restored. No fault remains in the repository.

## Defect found during M5
`find_products` (SQL) passed user text into `ILIKE`, so `_` and `%` acted as wildcards. For example, a 5-character pattern with `_` matched 5 unrelated brands. It now escapes wildcards (literal substring, same as Python). Regression test: `test_find_products_treats_wildcards_literally`.

## Performance (warm unless stated; same laptop, 4 DuckDB threads)
| Call | Python cold | Python warm | SQL |
|---|---|---|---|
| Engine load (fact + pack, ~138 MB Arrow) | 0.74 s | – | connect 0.38 s |
| market_performance(subgroup, MAT) | 316 ms | 177 ms | 368 ms |
| brand_performance(total, MAT, top 20 of 60,079) | 894 ms | 739 ms | 471 ms |
| brand_performance(in subgroup, YTD) | 155 ms | 19 ms | 295 ms |
| company_performance(MAT) | 322 ms | 142 ms | 338 ms |
| therapy_performance(subgroup) | 349 ms | 196 ms | 376 ms |
| segment_analysis(dosage_form, MONTH) | 241 ms | 105 ms | 278 ms |
| market/brand trends (36 months) | 80–90 ms | 80–90 ms | 60–90 ms |

"Cold" means the window pack-sums cache is empty. Python is competitive; it is slower only where it builds 60k Python dicts (full product ranking). The single Arrow copy of the fact table is the only large allocation.

## Running
```
cd python
..\.venv\Scripts\python.exe -m pci_analytics.validation     # prints PASS/FAIL + max diffs per check (no values)
cd ..
.venv\Scripts\python.exe -m pytest                          # full suite (M3 + M4 + M5)
```

## Known limitations
- Python ranks mirror DuckDB `round` via `std::round`-style scaling. This was verified empirically on 9,000 values, including near-half cases; a future DuckDB change to `round` semantics would be caught by `test_rounding_matches_duckdb_round`.
- The Python engine keeps one in-memory Arrow copy of the fact table (~138 MB) per process.
- The M4 test `tests/test_python_validation.py` (a lighter pyarrow cross-check) is retained. The M5 suites supersede it in coverage.
