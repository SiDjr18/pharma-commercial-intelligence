# DATA QUALITY BASELINE — private source workbook

Run: 2026-09-26 · Scripts: `python/profiling/profile_ims.py`, `profile_ims_supplement.py` · Mode: read-only streaming (openpyxl `read_only`), ~100 s per pass.
Findings only — **no source data was modified or "fixed"**. Details in `ims_profile.json` / `ims_profile_supplement.json`.

Severity: 🔴 must handle before analytics · 🟡 handle in transformation / document · 🟢 informational / passed

## 1. Structure
| Check | Result | Severity |
|---|---|---|
| Header names unique | 203/203 unique | 🟢 |
| Blank rows | 0 | 🟢 |
| Full-row duplicates | 0 | 🟢 |
| Primary key `PFC` | unique, 0 nulls | 🟢 |
| Periods embedded in column headers (wide format) | 36 months × 3 families + snapshots | 🟡 unpivot required |
| Second sheet `PivotTable` is derived from `DATA` | do not ingest as independent data | 🟡 |

## 2. Missingness
| Column(s) | Nulls | Notes | Severity |
|---|---|---|---|
| `Plain/Combination`, `Ind`, `MOLECULE_DESC` | 1 row each (same row) | one pack lacks molecule info | 🟡 flag row |
| `NI 24 MONTHS MAT MAY'22/23/24` | 91.9% / 87.5% / 87.6% | null by design (only new introductions) | 🟢 structural |
| All other 197 columns | 0 nulls | | 🟢 |
| `PACK_LNCH` = 0 | 682 rows | sentinel for unknown launch month | 🟡 map to NULL |
| `PROD_LNCH` = 0 | 1,953 rows | sentinel for unknown launch month | 🟡 map to NULL |

## 3. Data types
| Check | Result | Severity |
|---|---|---|
| Measures mix `int` and `float` cells | expected Excel behaviour; all numeric | 🟢 cast to float/decimal |
| Text in numeric columns | none found | 🟢 |
| Leading/trailing whitespace in text columns | 0 found | 🟢 |
| Launch fields YYYYMM | all non-zero values have valid month (01–12) | 🟢 |
| Case / spelling inconsistency in `SHORT DESCRIPTION` | e.g. "Parentral", "OTIC" vs "external" | 🟡 cosmetic mapping |

## 4. Keys & hierarchies
| Check | Result | Severity |
|---|---|---|
| `PFC` → PACK_DESC / PROD_CODE / BRANDS / MOLECULE / PACK_LNCH | 0 violations | 🟢 |
| `PROD_CODE` → BRANDS / MANUFAC CODE / PROD_LNCH | 0 violations | 🟢 |
| `MANUFAC CODE` ↔ `MANUFACT. DESC` → COMPANY → INDIAN_MNC | 0 violations | 🟢 |
| `SUBGROUP` → GROUP, ACUTE_CHRONIC | 0 violations | 🟢 |
| `GROUP` → SUPERGROUP | **2 groups** map to >1 supergroup (583 rows) | 🟡 therapy roll-ups must use SUBGROUP→SUPERGROUP path or a resolved mapping |
| `BRANDS` (name) → COMPANY | **658 brand names** shared by >1 company (2,907 rows) | 🔴 never key on brand name; use `PROD_CODE` |
| `BRANDS` (name) → PROD_CODE | 739 names map to >1 product code | 🔴 same as above |
| `PACK_DESC` unique? | no — 2,191 rows share a pack description | 🟡 use `PFC` |
| `PACK_LNCH` < `PROD_LNCH` | 0 rows | 🟢 |

## 5. Values
| Check | Result | Severity |
|---|---|---|
| Negative values in any measure | 0 | 🟢 |
| Packs with zero value in all 36 months | 8,895 (8.4%) | 🟡 dormant packs — keep for completeness, exclude from rankings |
| Packs with sales in latest month (May'24) | 77,408 | 🟢 |
| Active packs per month | rises steadily from 64,880 (Jun'21) to ~77–78k (2024) | 🟢 informational (new launches) |
| Intra-series zero gaps (zero month between first and last active month) | 20,338 packs; median gap 4 months; max 34 | 🟡 affects growth / CAGR on small packs — needs minimum-base rules |
| `PR_*` non-zero when units = 0 | 27,909 rows (May'24) | 🟡 price is carried forward; don't treat as sales signal |
| Outliers (positive values > Q3 + 3·IQR, per column) | present in every measure column (heavy right tail, as expected for pharma pack sales) | 🟢 do not remove; use robust stats / log scales |
| QTY/UNIT factor stable across months per pack | 78,103 / 78,946 packs (98.9%) | 🟡 843 packs with changing factor — investigate |
| QTY/UNIT factor non-integer | ~23% of active packs | 🟢 informational (fractional counting units) |

## 6. Units
| Check | Result | Severity |
|---|---|---|
| Value unit | INFERRED ₹ crore (pivot label + `PR = 1e4 × value/unit` exact) | 🟡 confirm with data owner |
| Units / Qty unit | INFERRED '000 | 🟡 confirm |
| Single currency | yes (LC suffix only) | 🟢 |

## 7. Periods
| Check | Result | Severity |
|---|---|---|
| Missing months in 2021-06 … 2024-05 | none (all 36 present for VALUE, UNIT, QTY) | 🟢 |
| Partial months / future months | none detected | 🟢 |
| `CUMM` definition | calendar YTD (Jan–May), not Indian FY | 🟡 label clearly in UI |
| Market seasonality | monthly totals vary ~±10% month-to-month | 🟢 informational — use MAT / YoY same-month comparisons |

## 8. Internal reconciliation (source consistency)
| Rule | Rows passing | Severity |
|---|---|---|
| `MONTH MAY'YY` == `MAY'YY` (value, unit, qty; 3 years) | 100% | 🟢 |
| `MAT MAY'YY` == Σ 12 months (value, unit, qty; 3 years) | 100% (float tolerance ≤ 1e-9) | 🟢 |
| `CUMM MAY'YY` == Σ Jan–May (value, unit, qty; 3 years) | 100% | 🟢 |
| `TSA` == SSA + HSA + DSA (LC, UN, ST; 3 years) | 100% | 🟢 |
| `TSA-LC` == `MAT` value | 100% within 1.4e-6 | 🟢 |
| `TSA-UN` == `UNIT MAT` | 100% | 🟢 |
| `TSA-ST` == `QTY MAT` | 100% within 0.005 (rounding) | 🟢 |
| `NI 24 MONTHS MAT` == `MAT` where non-null | 100% exact | 🟢 |
| `PR_*` == 1e4 × value / units | 100% within 0.1% | 🟢 |

These rules become permanent automated reconciliation tests in M3/M5 (processed layer must reproduce them).

## 9. Unknowns blocking some analytics
- SSA / HSA / DSA meaning → any channel/panel analysis is **blocked** until confirmed.
- No geography → geography analysis (`get_geography_performance`) is **not supported** by this source.
- ~~`INDEX` composition unknown~~ → resolved: `BRANDS : SUBGROUP : MANUFACT. DESC : PROD_CODE` (see DATA_DICTIONARY §8).

## 10. Automation (M3)
All checks above are automated in `tests/test_data_quality.py` and `tests/test_reconciliation.py` against the Parquet layer, with baseline counts as regression guards (`tests/conftest.py::BASELINE`).
