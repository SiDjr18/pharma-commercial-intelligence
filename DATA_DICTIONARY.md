# DATA DICTIONARY — private source workbook

Status: **M2 complete · §8 open questions resolved where technically possible · M3 analytical layer built (2026-09-26)**
Machine-readable outputs: `data/profile/ims_profile.json`, `data/profile/ims_profile_supplement.json`,
`data/profile/column_profile.csv`, `data/profile/workbook_structure.json`, `data/profile/open_questions_evidence.json` (all git-ignored, local only).
Analytical model: `docs/DATA_MODEL.md` · pipeline: `docs/DATA_PIPELINE.md`.
Profiling code: `python/profiling/profile_ims.py`, `python/profiling/profile_ims_supplement.py`.

Legend
- **CONFIRMED** — verified programmatically against the source file.
- **INFERRED** — strongly supported by evidence, but not stated by the source. Must be confirmed by the data owner before use in client-facing numbers.
- **UNKNOWN / NOT PRESENT** — not in the source, or meaning not determinable from the data.

No raw rows, brand names, company names or market totals are recorded in this file.

---

## 1. File

| Item | Value | Status |
|---|---|---|
| Path | `<PRIVATE_WORKBOOK_PATH>` (set via `PCI_SOURCE_PATH`) | CONFIRMED |
| Extension | `.xlsx` (Office Open XML; magic bytes `50 4B 03 04`) | CONFIRMED |
| Size / modified | recorded locally only (the build manifest keeps the source size and sha256; not published) | CONFIRMED |
| Sheets | `PivotTable` (A1:H65368), `DATA` (A1:GU105318) | CONFIRMED |
| Pivot cache source | `DATA!A1:GU105318`, last refreshed Excel serial 45453.67 (≈ 2024-06-10) | CONFIRMED |
| External links | none | CONFIRMED |
| Uncompressed size | DATA sheet XML ~873 MB; pivot cache records ~400 MB | CONFIRMED |

`PivotTable` is a derived view of `DATA` (7 page filters, data fields = Sum of MAT MAY'22/'23/'24, plus 18 calculated growth fields). It is **not** an independent source. Pivot header labels state `VAL IN CR'` and `UNITS & QTY IN 000'`.

## 2. Shape and grain

| Item | Value | Status |
|---|---|---|
| Data rows | 105,317 (+1 header row) | CONFIRMED |
| Columns | 203 (23 descriptive, 3 new-introduction, 171 measures, 6 price) | CONFIRMED |
| Blank rows | 0 | CONFIRMED |
| Full-row duplicates | 0 | CONFIRMED |
| Primary key | `PFC` — unique, non-null for all 105,317 rows | CONFIRMED |
| Grain | one row per **pack** (`PFC`), national level, wide format (periods as columns) | CONFIRMED (national = no geography field present) |
| Product key | `PROD_CODE` (60,079 distinct) → 1 brand name, 1 manufacturer, 1 product-launch date | CONFIRMED |
| Format | wide; must be unpivoted to long (pack × month × measure) for analytics | CONFIRMED |

## 3. Time

| Item | Value | Status |
|---|---|---|
| Monthly series | `JUN'21` … `MAY'24` — 36 consecutive months, no missing month columns | CONFIRMED |
| Earliest period | 2021-06 | CONFIRMED |
| Latest period | 2024-05 | CONFIRMED |
| Time grain | Monthly; plus pre-aggregated MONTH / CUMM (YTD) / MAT snapshots at May'22, May'23, May'24 | CONFIRMED |
| `MAT MAY'YY` | = sum of the 12 monthly columns Jun(YY-1)…May(YY) (all rows within float tolerance) | CONFIRMED |
| `CUMM MAY'YY` | = sum Jan…May of year YY (**calendar YTD**, not Indian FY Apr–Mar) | CONFIRMED |
| `MONTH MAY'YY` | = the `MAY'YY` monthly column (exact) | CONFIRMED |
| Date columns | none are real dates; periods are encoded in column headers as `MON'YY` | CONFIRMED |

## 4. Descriptive (dimension) columns — positions 0–22

| # | Column | Type | Nulls | Distinct | Meaning | Status |
|---|---|---|---|---|---|---|
| 0 | `PFC` | int | 0 | 105,317 | Pack code — unique row identifier | CONFIRMED unique; meaning INFERRED (IQVIA pack form code) |
| 1 | `Plain/Combination` | text | 1 | 2 (`Plain`, `Combination`) | Single vs combination molecule | CONFIRMED values |
| 2 | `Ind` | int 1–10 | 1 | 10 | Count of molecules in `MOLECULE_DESC` (= number of `+` separators + 1, all 105,316 non-null rows). Max observed = 10; no description has more than 10 components, so no truncation is visible in the file | CONFIRMED (whether upstream caps at 10: UNKNOWN, no impact) |
| 3 | `MOLECULE_DESC` | text | 1 | 16,135 | Molecule / combination description (`+`-separated) | CONFIRMED |
| 4 | `PACK_DESC` | text | 0 | 104,082 | Pack description (brand + strength + pack size). Not unique | CONFIRMED |
| 5 | `PROD_CODE` | int | 0 | 60,079 | Product (brand-level) code. Determines brand, manufacturer, company, product launch. Does **not** determine SUBGROUP (4,519 products span >1 subgroup) or MOLECULE_DESC (4,643) | CONFIRMED |
| 6 | `BRANDS` | text | 0 | 59,052 | Brand name. **Not a unique key**: 658 names are shared across companies; 739 names map to >1 `PROD_CODE` | CONFIRMED |
| 7 | `MANUFAC CODE` | int | 0 | 1,106 | Manufacturer code | CONFIRMED |
| 8 | `MANUFACT. DESC` | text | 0 | 1,106 | Manufacturer name (1:1 with code) | CONFIRMED |
| 9 | `COMPANY` | text | 0 | 1,077 | Parent company (manufacturers roll up to companies) | CONFIRMED hierarchy |
| 10 | `INDIAN_MNC` | text | 0 | 2 (`INDIAN`, `MNC`) | Company ownership segment | CONFIRMED values |
| 11 | `SUBGROUP` | text | 0 | 1,889 | Therapy subgroup (level 3) | CONFIRMED → GROUP 1:1 |
| 12 | `GROUP` | text | 0 | 261 | Therapy group (level 2) | CONFIRMED; 2 groups map to >1 supergroup (583 rows) — SUBGROUP → SUPERGROUP has 0 violations, so roll up via SUBGROUP |
| 13 | `SUPERGROUP` | text | 0 | 25 | Therapy area (level 1), e.g. CARDIAC, ANTI DIABETIC, RESPIRATORY | CONFIRMED |
| 14 | `ACUTE_CHRONIC` | text | 0 | 2 (`ACUTE`, `CHRONIC`) | Therapy segment; determined by SUBGROUP | CONFIRMED |
| 15 | `PACK_LNCH` | int YYYYMM | 0 | 604 | Pack launch month; `0` = unknown (682 rows); range 1967-04…2024-05; never earlier than product launch | CONFIRMED |
| 16 | `PROD_LNCH` | int YYYYMM | 0 | 601 | Product launch month; `0` = unknown (1,953 rows); range 1967-02…2024-05 | CONFIRMED |
| 17 | `INDEX` | text | 0 | 65,398 | Composite key `BRANDS : SUBGROUP : MANUFACT. DESC : PROD_CODE` (exact for all 105,317 rows); 1:1 with (PROD_CODE, SUBGROUP) = product-within-subgroup | CONFIRMED FROM SOURCE |
| 18 | `NFC` | text(3) | 0 | 261 | New Form Code (dosage form) | CONFIRMED values; meaning INFERRED (EphMRA NFC) |
| 19 | `NFC 1` | text | 0 | 17 | NFC level 1 (e.g. oral solid ordinary, parenteral) | CONFIRMED hierarchy |
| 20 | `NFC 2` | text | 0 | 90 | NFC level 2 | CONFIRMED (NFC 2 → NFC 1) |
| 21 | `NFC 3` | text | 0 | 261 | NFC level 3 | CONFIRMED (NFC 3 → NFC 2) |
| 22 | `SHORT DESCRIPTION` | text | 0 | 13 | Simplified dosage-form bucket (Oral Solids, Oral liquids, Topical, Parentral, …) | CONFIRMED values; note source spelling "Parentral", mixed case |

Hierarchies (CONFIRMED functional dependencies):
- Product: `PFC` → `PROD_CODE` → `BRANDS`, `MANUFAC CODE` → `MANUFACT. DESC` → `COMPANY` → `INDIAN_MNC`
- Therapy: `SUBGROUP` → `GROUP` → `SUPERGROUP` (2 exceptions at GROUP level); `SUBGROUP` → `ACUTE_CHRONIC`
- Form: `NFC 3` → `NFC 2` → `NFC 1`
- Molecule: `MOLECULE_DESC` → `Plain/Combination`

## 5. Measure columns

Value / volume families (each 36 monthly + 9 snapshot columns):

| Family | Column pattern | Count | Unit | Status |
|---|---|---|---|---|
| Value | `MON'YY`, `MONTH/CUMM/MAT MAY'YY` | 45 | ₹ crore | CONFIRMED FROM SOURCE — workbook labels its pivot `VAL IN CR'`; the pivot data fields are plain `Sum of MAT` with number format `0.0` (no scaling, no show-as), so raw DATA values are in crore. Corroborated: implied per-pack prices fall in a plausible rupee range (distribution in local `open_questions_evidence.json`) |
| Units | `UNIT MON'YY`, `UNIT MONTH/CUMM/MAT MAY'YY` | 45 | '000 packs | INFERRED (high confidence): workbook label `UNITS & QTY IN 000'` exists but no units field is placed in the pivot; `PR = 1e4 × value/units` yields ₹/pack only if units are '000 |
| Quantity | `QTY MON'YY`, `QTY MONTH/CUMM/MAT MAY'YY` | 45 | '000 counting units (e.g. tablets) | INFERRED (same label). QTY/UNIT is a constant per-pack factor for 98.9% of packs (median 10) |

Audit-split snapshot columns (positions 161–196), pattern `{SSA|HSA|DSA|TSA} - MAT ~ 05/{2022|2023|2024} - {LC|ST|UN}`:

| Element | Evidence | Status |
|---|---|---|
| `TSA` | TSA = SSA + HSA + DSA exactly for all rows, every year and suffix | CONFIRMED arithmetic |
| `TSA … LC` | = `MAT MAY'YY` (value) | CONFIRMED → LC = local-currency value |
| `TSA … UN` | = `UNIT MAT MAY'YY` | CONFIRMED → UN = units |
| `TSA … ST` | = `QTY MAT MAY'YY` (±0.005 rounding) | CONFIRMED → ST = QTY (standard/counting units) |
| `SSA`, `HSA`, `DSA` | Three additive, non-negative partitions of TSA; one dominant and two minor shares, stable across years (figures in local `open_questions_evidence.json`). Business meaning **not stated anywhere in the file** | UNKNOWN / REQUIRES BUSINESS CONFIRMATION — retained as `ssa/hsa/dsa_*` in `pack_snapshot`, excluded from analytics |

Other measures:

| Column(s) | Evidence | Status |
|---|---|---|
| `NI 24 MONTHS MAT MAY'YY` (3 cols) | Non-null only for packs launched within the 24 months to May'YY (100% of non-null rows); where non-null, exactly equals `MAT MAY'YY`; ~88–92% null by design | CONFIRMED behaviour → "New Introduction" MAT value |
| `PR_DEC'23` … `PR_MAY'24` (6 cols) | `PR = 10,000 × value / units` within 0.1% for all 77,408 tested rows in May'24; carries a non-zero price even when units = 0 (27,909 rows in May'24) | CONFIRMED formula → price per pack in ₹ (if value in crore and units in '000) |

## 6. Fields NOT present

| Needed concept | Status |
|---|---|
| Geography (state / zone / city / HQ) | **NOT PRESENT** — national data only |
| Channel (retail / hospital) | NOT PRESENT as a labelled field (SSA/HSA/DSA split is unlabelled — UNKNOWN) |
| Prescriber / HCP / specialty | NOT PRESENT |
| Promotion / spend / field force | NOT PRESENT |
| Price list / MRP separate from derived price | NOT PRESENT (only derived `PR_*`) |
| Targets / budgets / forecasts | NOT PRESENT |
| Explicit date columns | NOT PRESENT (headers encode period) |
| ATC classification | NOT PRESENT (proprietary therapy hierarchy SUPERGROUP/GROUP/SUBGROUP used instead) |

## 7. Analytical model

Implemented in M3 — see `docs/DATA_MODEL.md`. Summary: `pack` (PFC grain) · `fact_pack_month` (PFC × month) · `pack_snapshot` (PFC × May snapshot year) · `pack_price_month` (PFC × month, Dec'23–May'24) + derived dimension views. Price is also derivable as value/units.

M4 analytical usage: primary market = `SUBGROUP` (analytical definition, `docs/MARKET_DEFINITION.md`); segments = the source fields listed in `docs/SEGMENT_DEFINITIONS.md`. No new source fields were discovered or reinterpreted in M4.

## 8. Open questions — resolution (2026-09-26)

Evidence: `data/profile/open_questions_evidence.json`, `python/profiling/investigate_open_questions.py`, `tests/test_data_model.py::test_index_composition`.

| # | Question | Answer | Status |
|---|---|---|---|
| 1 | Value unit ₹ crore? Units/qty '000? | Value = ₹ crore (source's own label on an unscaled Sum pivot). Units/qty = '000 (label present; arithmetic consistent with ₹/pack prices) | Value: CONFIRMED FROM SOURCE · Units/qty: INFERRED (high confidence) |
| 2 | What are SSA, HSA, DSA? | Not determinable from the file. Technically: 3 additive partitions of TSA (= MAT), one dominant and two minor shares, stable across years | UNKNOWN / REQUIRES BUSINESS CONFIRMATION |
| 3 | Composition of `INDEX` | `BRANDS : SUBGROUP : MANUFACT. DESC : PROD_CODE`, exact for 100% of rows; 1:1 with (PROD_CODE, SUBGROUP) | CONFIRMED FROM SOURCE |
| 4 | Why do 2 GROUPs map to >1 SUPERGROUP? | Cause (reclassification vs. design) is not stated. Technically: 2 groups (4 and 7 subgroups; 102 and 481 rows) split across 2 supergroups each; SUBGROUP → SUPERGROUP is strictly 1:1 | Mechanism CONFIRMED; reason UNKNOWN (no impact — roll up via SUBGROUP) |
| 5 | Is `Ind` capped at 10? | `Ind` = component count for every row; max component count in any description is 10 | Relationship CONFIRMED; upstream cap UNKNOWN (no analytical impact) |
| 6 | May derived aggregates be published? | Licence terms are not in the file | UNKNOWN / REQUIRES BUSINESS CONFIRMATION |

### Business Assumptions / Open Questions (non-blocking)
Safest documented technical interpretation used until confirmed:

| Item | Limitation | Interpretation adopted |
|---|---|---|
| SSA / HSA / DSA | Meaning unknown | Keep in `pack_snapshot` only for reconciliation (TSA = SSA+HSA+DSA = MAT). No split-level analytics, labels or tools. All analytics use totals (TSA ≡ MAT) |
| Units/qty '000 | Not labelled on a units pivot field | Store as `units_k`, `qty_k`; label "'000 (inferred)" in outputs |
| GROUP → SUPERGROUP exceptions | Reason unknown | Therapy hierarchy anchored on SUBGROUP; supergroup roll-ups via `dim_therapy` (SUBGROUP grain) |
| `Ind` cap | Unknown | Use as-is (`molecule_count`) |
| Publication of aggregates | Licence unknown | No real aggregates in any public artefact; public demo uses synthetic data (M12) |
| `CUMM` basis | Source is calendar YTD (Jan–May) | Label as "YTD (calendar)"; Indian FY YTD would be a separately derived, clearly named metric |
