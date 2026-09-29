# SYNTHETIC PUBLIC DATASET (M13, SYN-1.0.0)

A **fictional, structure-only** dataset with the exact schema and grain of the private processed layer, so the whole product (SQL/Python engines, opportunity and scenario engines, application, agents, Power BI) runs without the licensed IMS data.

> **Synthetic data.** Every entity, code and number is invented. Nothing is copied, sampled, perturbed or scaled from the licensed data, and no value is calibrated on real aggregates. Results computed on it describe the synthetic market only.

## Use
```
cd python
..\.venv\Scripts\python.exe -m pci_synthetic.generate            # -> data/synthetic/ (~1 s, ~6 MB)
rem PCI_DATASET unset = synthetic (the default); `set PCI_DATASET=synthetic` is equivalent
cd .. && .venv\Scripts\python.exe run_app.py --port 8766           # app + agents on synthetic data
cd python && ..\.venv\Scripts\python.exe -m pci_powerbi.export     # opportunity export -> data/synthetic/powerbi/
..\.venv\Scripts\python.exe -m pci_powerbi.build                   # PBIP -> dashboards/_synthetic/ (git-ignored)
```
Then open `dashboards\_synthetic\PCI_Commercial_Intelligence.pbip` in Power BI Desktop and click **Refresh**.

`PCI_DATASET` switches every consumer, and there are no arbitrary paths (P1 isolation, `docs/DATA_ISOLATION.md`):
- unset, empty or `synthetic` (**default**) reads `data/synthetic`.
- `ims` reads the licensed layer in `PCI_IMS_DATA_DIR`, an explicit directory that must be outside the repository.
- Any other value (including the retired `private`) is an error; there is no fallback between the two.

The private test suite runs with `PCI_DATASET=ims` + `PCI_IMS_DATA_DIR`; tests marked `ims` are skipped otherwise.

## Contract
| Item | Rule |
|---|---|
| Inputs | `python/pci_synthetic/{spec,names}.py` and `processed_schema.json` (column names and types only). The generator reads **no** data file; a static test enforces this. |
| Output | `pack`, `fact_pack_month`, `pack_snapshot` and `pack_price_month` Parquet with identical column names, Arrow types and grain, plus `_manifest.json` with the M3 key structure (`dataset: "synthetic"`, `source.sha256` = fingerprint) |
| Calendar | same as private: Jun 2021 – May 2024 (36 months); May snapshots 2022/23/24; price months Dec 2023 – May 2024 |
| Scale | 4,000 packs × 36 = 144,000 fact rows; 2,300 products; 120 companies; 160 manufacturers; 12 therapy areas; 40 therapy groups; ~170 subgroups; ~345 molecule descriptors; 30 form codes |
| Determinism | seed `20260927`. Randomness comes only from `random.Random(seed).random()`, whose output is guaranteed across Python versions. The fingerprint is SHA-256 over canonical sorted table content plus the spec, independent of Parquet bytes and the pyarrow version. |
| Committed | generator, spec, vocabularies, schema snapshot and `data/synthetic/SYNTHETIC_FINGERPRINT.json`. **Not committed:** the Parquet data, which is regenerated. |

## Structure reproduced (tested)
- Unique PFC; dense pack × month grain; no nulls or negatives in the fact.
- Functional dependencies:
  - product → brand, manufacturer, launch
  - manufacturer → description, company, Indian/MNC
  - company → Indian/MNC
  - subgroup → group, area, acute/chronic
  - form code → form levels and description
  - pack → description, product, molecule, launch
- DQ quirks from `DATA_QUALITY_BASELINE.md`:
  - exactly 2 therapy groups span two therapy areas
  - brand names shared across companies
  - brand names mapping to several products
  - products spanning several subgroups and several molecules
  - non-unique pack descriptions
  - exactly 1 pack with no molecule data
  - unknown-launch sentinel 0 (month NULL)
  - pack launch ≥ product launch
  - dormant packs (all months zero)
  - `index_desc` = brand : subgroup : manufacturer : product code
  - molecule count and plain/combination consistent with the molecule text
- Snapshots equal the sums of the monthly fact (month, calendar YTD, MAT); NI-24 = MAT only for packs launched in the prior 24 months; SSA + HSA + DSA = TSA. The split columns stay non-analytical, as in the private data.
- Price per pack = 1e4 × value / units where units > 0, carried forward otherwise (the source PR definition).

## Invented numbers
All numbers come from invented distributions in `spec.py`, **not** from real data:
- lognormal pack size and value
- lognormal price per pack with annual April increases
- market growth ~ N(7%, 9%), with product growth spread around its market
- seasonal amplitude per therapy area, and monthly noise
- in-window launches with a 6-month ramp, discontinuations, sporadic zero months, and dormant packs

DQ quirk *rates* (such as the share of dormant packs) are structural properties taken from the committed DQ baseline.

## Names
- Entities (therapy areas, groups, subgroups, molecules, companies, manufacturers, brands, packs) are invented from syllables.
- Codes are fictional:
  - PFC, product and manufacturer codes have **10 digits** (source codes have ≤ 9).
  - Form codes start with `SF`.
  - Subgroup codes start with `S`.
- Only generic category domains are shared with the source vocabulary: ACUTE/CHRONIC, INDIAN/MNC, Plain/Combination, and dosage-form words such as "Tablet".

## Validation
| Test file | Needs private data? | What it proves |
|---|---|---|
| `tests/test_synthetic_m13.py` (20 tests) | no | fingerprint determinism and equality with the committed one; generator reads no data file; schema parity with the snapshot; grain, calendar and manifest; functional dependencies and DQ quirks; snapshot and price rules; **full unchanged M5 SQL = Python matrix**; API functions; opportunity scoring (both statuses, decomposition = score); scenarios (formulas, rejections); dataset switch; synthetic Power BI build can never overwrite the private project |
| `tests/test_synthetic_leakage_m13.py` (19 tests) | **yes (local only; skipped elsewhere)** | no shared entity label (13 columns); no shared code; no pack's 36-month value/units/qty vector equals a real one (1e-9); no shared pack-month (value, units, qty) triple; national monthly totals are neither near-equal to the real ones nor a constant multiple of them. Messages report counts only. |

**Findings from the first run, fixed in the generator:**
- 4 invented short brand names equalled real brands, so brands are now at least 8 letters.
- 4 synthetic PFC codes overlapped the real 9-digit code range, so synthetic codes are now 10 digits.
- A single one-month value coincided at 1e-6 resolution, so vector comparison now uses 1e-9, the same resolution as the triple test.

**End-to-end runs on synthetic data (M13):**
- application and tool API on port 8766
- governed agents: 7 intents routed to deterministic tools and passing QA; geography refused
- Power BI: export, build, refresh and reconciliation. Results: `evaluation/reports/POWER_BI_RECONCILIATION_SYNTHETIC.md`.

## Limits
- The synthetic market has realistic *structure*, but its magnitudes, growth rates and concentration are invented and carry no information about the real market.
- The M3 build from the source workbook (`pci_data.build_processed`) stays private; the synthetic dataset starts at the processed layer.
- The app shows no "synthetic data" badge yet (deferred to the portfolio milestone).
