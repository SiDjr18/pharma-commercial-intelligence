# DATA MODEL — Analytical layer (M3)

Source: `<PRIVATE_WORKBOOK_PATH>`, sheet `DATA` only (the `PivotTable` sheet is a derived view and is not ingested).
Physical storage: 4 Parquet files in `data/processed/` (git-ignored). Logical model: DuckDB views in `sql/views.sql`, opened with `pci_data.db.connect()`.

## Design decision

The source is one wide table at pack grain with three kinds of repeated measure blocks (36 monthly periods, 3 May snapshots, 6 price months). Forcing a full normalised star schema would create physical duplicates of small dimension data with no benefit. The model is therefore:

- **1 descriptive table** at pack grain (`pack`), which already holds every attribute. It is small: 105,317 rows, 5.8 MB.
- **1 core fact** at pack × month (`fact_pack_month`): the wide monthly columns, unpivoted.
- **2 secondary facts** that keep the source's own pre-aggregated and price columns (`pack_snapshot`, `pack_price_month`). They preserve every source column, so the source's internal logic can be re-tested.
- **Dimension views** (no storage) derived from `pack`. Their keys are proven unique by tests.

## Tables (physical Parquet)

### `pack` — ONE ROW REPRESENTS: one pack (PFC)
| Column | Source | Notes |
|---|---|---|
| `source_row` | Excel row number (2…105,318) | traceability |
| `pfc` | `PFC` | **business key**, unique |
| `plain_combination`, `molecule_count`, `molecule_desc` | `Plain/Combination`, `Ind`, `MOLECULE_DESC` | 1 row null (source) |
| `pack_desc` | `PACK_DESC` | not unique |
| `prod_code`, `brand` | `PROD_CODE`, `BRANDS` | brand name is not a key; use `prod_code` |
| `manufacturer_code`, `manufacturer_desc`, `company`, `indian_mnc` | `MANUFAC CODE`, `MANUFACT. DESC`, `COMPANY`, `INDIAN_MNC` | |
| `subgroup`, `therapy_group`, `supergroup`, `acute_chronic` | `SUBGROUP`, `GROUP`, `SUPERGROUP`, `ACUTE_CHRONIC` | |
| `pack_launch_yyyymm`, `prod_launch_yyyymm` | `PACK_LNCH`, `PROD_LNCH` | raw ints kept (0 = unknown) |
| `pack_launch_month`, `prod_launch_month` | derived | DATE; NULL where source = 0 |
| `index_desc` | `INDEX` | = `brand : subgroup : manufacturer_desc : prod_code` |
| `nfc`, `nfc1`, `nfc2`, `nfc3`, `form_short_desc` | `NFC`, `NFC 1/2/3`, `SHORT DESCRIPTION` | |

### `fact_pack_month` — ONE ROW REPRESENTS: one pack in one calendar month
Keys `(pfc, period)`; 105,317 × 36 = **3,791,412 rows**, dense (zero months kept).
| Column | Source columns | Unit |
|---|---|---|
| `period` (DATE, 1st of month) | header `MON'YY` | 2021-06-01 … 2024-05-01 |
| `value_cr` | `JUN'21` … `MAY'24` (36) | ₹ crore (CONFIRMED) |
| `units_k` | `UNIT JUN'21` … `UNIT MAY'24` (36) | '000 packs (INFERRED) |
| `qty_k` | `QTY JUN'21` … `QTY MAY'24` (36) | '000 counting units (INFERRED) |

### `pack_snapshot` — ONE ROW REPRESENTS: one pack at one May snapshot (2022, 2023, 2024)
Keys `(pfc, snapshot_year)`; **315,951 rows**. These are source pre-aggregates, kept for reconciliation and fidelity. Analytics should derive them from `fact_pack_month`.
| Column(s) | Source | Meaning |
|---|---|---|
| `value_month/ytd/mat`, `units_*`, `qty_*` | `[UNIT/QTY] MONTH/CUMM/MAT MAY'YY` | May month, calendar YTD (Jan–May), MAT (Jun–May) |
| `ni_24m_mat_value_cr` | `NI 24 MONTHS MAT MAY'YY` | MAT value for packs launched in the prior 24 months; NULL otherwise |
| `{ssa,hsa,dsa,tsa}_mat_{value_cr,units_k,qty_k}` | `{SSA..TSA} - MAT ~ 05/YYYY - {LC,UN,ST}` | TSA = total (= MAT). SSA/HSA/DSA meaning UNKNOWN, so **not for analytics** |

### `pack_price_month` — ONE ROW REPRESENTS: one pack's source price in one month
Keys `(pfc, period)`, Dec 2023 … May 2024; **631,902 rows**. `price_rs` = `PR_MON'YY` = 10,000 × value_cr / units_k (₹ per pack). It carries a value even when units = 0.

## Dimension views (derived, no storage)
| View | Key (verified unique) | Rows | Attributes |
|---|---|---|---|
| `dim_period` | `period` | 36 | year, month, source_label (`JUN'21`), period_index 1–36, mat_year_ending_may |
| `dim_product` | `prod_code` | 60,079 | brand, manufacturer_code, company, prod_launch |
| `dim_product_subgroup` | `index_desc` ≡ (`prod_code`, `subgroup`) | 65,398 | brand |
| `dim_manufacturer` | `manufacturer_code` | 1,106 | manufacturer_desc, company, indian_mnc |
| `dim_company` | `company` | 1,077 | indian_mnc |
| `dim_therapy` | `subgroup` | 1,889 | therapy_group, supergroup, acute_chronic |
| `dim_form` | `nfc` | 261 | nfc1, nfc2, nfc3, form_short_desc |
| `v_pack_month` | (`pfc`, `period`) | 3,791,412 | fact + key pack attributes (analysis convenience) |

## Relationships
```
dim_period 1─* fact_pack_month *─1 pack *─1 dim_product *─1 dim_manufacturer *─1 dim_company
                                    pack *─1 dim_therapy (subgroup) · pack *─1 dim_form (nfc)
                                    pack *─1 dim_product_subgroup (index_desc)
pack 1─* pack_snapshot · pack 1─* pack_price_month
```

## Hierarchies
- Company: pack → product (`prod_code`) → manufacturer → company → indian_mnc
- Therapy: pack → subgroup → supergroup (and subgroup → therapy_group, subgroup → acute_chronic). **Do not** roll up group → supergroup: 2 groups span two supergroups.
- Form: nfc → nfc3 → nfc2 → nfc1; nfc → form_short_desc
- Molecule: pack → molecule_desc → plain_combination; molecule_count = number of components

## Duplicate-key and modelling risks
| Risk | Handling |
|---|---|
| `brand` shared by 658 names across companies; 739 names map to >1 product | Always key on `prod_code`; resolve names to codes in the orchestrator |
| Product spans several subgroups (4,519) and molecules (4,643) | Therapy and molecule attributes live at pack grain; product-in-market = `dim_product_subgroup` |
| `pack_desc` not unique | Key on `pfc` |
| GROUP → SUPERGROUP not nested | Roll up via SUBGROUP |
| Source snapshots vs. derived | Tests prove that snapshot = Σ months; analytics use `fact_pack_month` |
| Calendar YTD in source | Name the metric `ytd_calendar`; Indian FY YTD must be derived explicitly |
