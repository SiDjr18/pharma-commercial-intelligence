# DATA PIPELINE (M3)

```
<PRIVATE_WORKBOOK_PATH>  (private, read-only, never copied)
        │  openpyxl read_only streaming, 5,000-row chunks, sheet DATA
        ▼
python/pci_data/build_processed.py   ── column classification (python/pci_data/schema.py)
        │  sha256 of source before and after (abort if changed)
        ▼
data/processed/  (git-ignored)
  pack.parquet               105,317 rows    5.8 MB
  fact_pack_month.parquet  3,791,412 rows   42.6 MB
  pack_snapshot.parquet      315,951 rows   30.2 MB
  pack_price_month.parquet   631,902 rows    1.9 MB
  _manifest.json             source hash, header, column map, source-side control totals
        │
        ▼
sql/views.sql (+ M4: periods, entities, metrics, domains) → pci_data.db.connect()  (in-memory DuckDB, views/macros only, threads=4, memory 2 GB)
```

## Run
```
cd <PROJECT_ROOT>\python
..\.venv\Scripts\python.exe -m pci_data.build_processed      # ~115 s
cd ..
.venv\Scripts\python.exe -m pytest                           # 133 tests, ~70 s incl. source re-read
set PCI_SKIP_SOURCE=1 && .venv\Scripts\python.exe -m pytest  # fast mode, ~7 s
```
Environment: project `.venv` (Python 3.13.3) with duckdb 1.5.5, pyarrow 25.0.1, openpyxl 3.1.5, pytest 9.1.1 (`requirements.txt`). pip cache was placed in `<PROJECT_ROOT>\.cache\pip` via `PIP_CACHE_DIR`. pandas is not needed in this layer.

## Why Parquet + DuckDB views
- The source is a 240 MB xlsx that expands to ~873 MB of XML. Parsing it takes ~100 s on every read, so repeated querying needs a columnar format.
- Parquet (zstd) holds the full content in **80 MB** (about 1/3 of the xlsx). It is columnar, typed, and read directly by DuckDB, Power BI and pyarrow.
- DuckDB views over Parquet mean **one physical copy**, with no `.duckdb` database file duplicating the data. Registering the views takes ~0.2 s.
- DuckDB's Excel extension was not used, because it would download extension binaries into the user profile on C:.

## Columns retained
All 203 source columns are retained. None are dropped.
| Kind | Source cols | Destination |
|---|---|---|
| descriptive | 23 | `pack` |
| monthly value/units/qty | 108 | `fact_pack_month` (unpivoted) |
| MONTH/CUMM/MAT snapshots | 27 | `pack_snapshot` |
| NI 24 months MAT | 3 | `pack_snapshot.ni_24m_mat_value_cr` |
| SSA/HSA/DSA/TSA splits | 36 | `pack_snapshot` |
| PR_ price | 6 | `pack_price_month` |

The build fails if any header is unclassified, duplicated or missing, or if any measure cell is non-numeric.

## Transformations
1. Source column names are mapped to snake_case in the processed layer only (`schema.py`). The mapping is recorded in `_manifest.json.column_map`.
2. Wide to long: `MON'YY` headers become `period` DATE (first of month); the value/units/qty families become three columns.
3. Snapshot, split and NI columns are pivoted to one row per `snapshot_year`.
4. Launch `YYYYMM` ints are kept as-is. A DATE copy is added, with source sentinel `0` → NULL.
5. Numeric cells are cast to DOUBLE and integer keys to BIGINT. Text is **not** trimmed, recased or corrected; tests confirm there is no stray whitespace.
6. `source_row` (Excel row) is added for traceability.
7. No filtering, deduplication, imputation or "fixing" is done. Zero months are kept, so the fact is dense.

## Validation (automated — `tests/`)
| Suite | Tests | Covers |
|---|---|---|
| `test_data_model.py` | 52 | schemas; column map covers all 203 columns; grain uniqueness (13 keys); dense row counts; dimension cardinalities; referential integrity; 18 functional dependencies; INDEX composition; period dimension |
| `test_reconciliation.py` | 49 | row count; **all 186 numeric source columns** (sum + non-null count) vs source controls; grand totals per measure; 36 period totals; totals by supergroup / acute-chronic / Indian-MNC / plain-combination / form / **company** (6 measures each); MAT/YTD/MONTH = Σ months (27 combinations); TSA = SSA+HSA+DSA; TSA = MAT; NI = MAT; price = 1e4·value/units; **independent re-read of the source workbook** + sha256 check |
| `test_data_quality.py` | 32 | nulls only where the baseline expects them; duplicates; brand-name and group-supergroup regression guards; date validity; period completeness; non-negative/finite; molecule_count meaning; dormant pack count; allowed categories; whitespace; roll-ups = grand total |

Sensitivity check (performed once, not committed): perturbing one control total by 1e-7 relative, one company total by 0.01, and one fact cell by +1 were each detected.

## Reconciliation findings
- No discrepancies between source and Parquet. Every column matched to relative 1e-9.
- One documented source tolerance: `TSA … ST` differs from `QTY MAT` by ≤ 0.005 (rounding in the source). The test tolerance is 0.0051.

## Performance (warm, this laptop, 4 threads)
| Query | Time |
|---|---|
| Register views | 0.17 s |
| Market monthly trend (36 rows) | 24 ms |
| Supergroup × month over 3.8M-row join | 81 ms |
| Company MAT ranking | 21 ms |
| Product-in-subgroup 12-month totals (65k groups) | 95 ms |

## Rebuild policy
Rebuild when the source sha256 changes. `test_independent_source_reread` fails with "source changed since build" in that case. The build writes `*.tmp` files and swaps them in only on success.
